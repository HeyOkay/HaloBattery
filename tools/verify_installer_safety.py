"""Offline installer safety check with disposable Windows process copies.

This compiles and runs the actual production Pascal shutdown and Run-value
helpers inside an isolated no-install/no-registry runner, then checks fixture
processes. Portable data is checked against the source deletion entries without
running an uninstaller. No installed app is changed.
Run: python -B tools/verify_installer_safety.py --workspace D:\\Ai\\Codex --iscc PATH
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "installer" / "HaloBattery.iss"


def source_contract(source=None):
    source = SCRIPT.read_text(encoding="utf-8") if source is None else source
    identity = source.split("function IsInstallProcess(", 1)[1].split("function CloseApp(", 1)[0]
    if not re.match(r"Process: THandle\): Boolean;", identity):
        raise AssertionError("identity checks require native-width process handles")
    templates = re.findall(r"ExpandConstant\(\s*'([^']+)'\s*\)", identity)
    expected = [r"{app}\{#AppExe}", r"{app}\_internal\settings-ui\HaloBattery.Settings.exe"]
    if templates != expected or identity.count("CompareText(Image,") != 2 or identity.count("= 0)") != 2:
        raise AssertionError("installer identity must use two exact executable path comparisons")
    if "Pos(" in identity or "Lowercase(" in identity:
        raise AssertionError("substring identity comparison is unsafe")
    if re.search(r"taskkill|/im\s|/t(?:\s|'|\")", source, re.IGNORECASE):
        raise AssertionError("image-name or process-tree termination is forbidden")
    access = re.search(r"InstallerOpenProcess\(\$([0-9a-f]+), False, Ids\[I\]\)", source, re.I)
    budget = re.search(r"Deadline := InstallerTicks\(\) \+ ([0-9]+);", source)
    if access is None or int(access[1], 16) != 0x00101001:
        raise AssertionError("process handles need query-limited, terminate and synchronize access")
    if budget is None or not 0 < int(budget[1]) <= 5000:
        raise AssertionError("cleanup must have a shared bounded wait")
    if not all(token in source for token in (
            "Process: THandle", "array of THandle", "QueryFullProcessImageNameW@kernel32.dll",
            "if IsInstallProcess(Process) then", "Handles[HandleCount] := Process;",
            "InstallerTerminateProcess(Handles[I],", "InstallerCloseHandle(Handles[I]);",
            "IdCount := BytesUsed div 4;", "for I := 0 to IdCount - 1 do")):
        raise AssertionError("verified native handle ownership contract changed")
    run_value = source.split("function RunValueBelongsToInstall(", 1)[1].split("procedure CurUninstallStepChanged", 1)[0]
    if "CompareText(Trim(Cmd), '\"' + ExpandConstant('{app}\\{#AppExe}') + '\"') = 0" not in run_value:
        raise AssertionError("Run value cleanup must match exactly the generated quoted command")
    deletion = source.split("[UninstallDelete]", 1)[1].split("[Code]", 1)[0]
    entries = re.findall(r'Type:\s*(\w+);\s*Name:\s*"([^"]+)"', deletion)
    if entries != [("filesandordirs", r"{app}\_internal"), ("dirifempty", "{app}")]:
        raise AssertionError("uninstall may only recursively remove the reserved program library directory")
    app_id = re.search(r"^AppId=(.+)$", source, re.MULTILINE)
    if app_id is None or app_id[1].strip() != "{{1E3A3904-9F39-4150-82EA-25D23968E2D6}":
        raise AssertionError("installer identity changed")
    return dict(templates=templates, access=int(access[1], 16), budget_ms=int(budget[1]), deletions=entries)


def compile_runner(iscc, base, app):
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("type\n  TProcessIds")
    helpers = source[start:source.index("function PrepareToInstall(", start)]
    run_start = source.index("function RunValueBelongsToInstall(")
    helpers += source[run_start:source.index("procedure CurUninstallStepChanged", run_start)]
    runner = base / "installer-helper.iss"
    runner.write_text(
        '#define AppExe "HaloBattery.exe"\n'
        '[Setup]\nAppId=HaloBatteryInstallerSafetyFixture\n'
        'AppName=Halo Battery installer safety fixture\nAppVersion=1.0\n'
        'PrivilegesRequired=lowest\nUninstallable=no\nCreateUninstallRegKey=no\n'
        'CreateAppDir=yes\nUsePreviousAppDir=no\nDisableProgramGroupPage=yes\n'
        'CloseApplications=no\nDefaultDirName=' + str(app) + '\n'
        'OutputDir=' + str(base) + '\nOutputBaseFilename=installer-helper\n'
        '[Code]\n' + helpers + '''
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  NativeHandle: THandle;
  Started: Int64;
  Cmd: String;
begin
  Started := InstallerTicks();
  Result := CloseApp();
  if Result <> '' then
    Exit;
  Cmd := '"' + ExpandConstant('{app}\\{#AppExe}') + '"';
  if not RunValueBelongsToInstall(Cmd) then
    RaiseException('Canonical Run command rejected');
  if not RunValueBelongsToInstall(Uppercase(Cmd)) then
    RaiseException('Case-insensitive Run comparison failed');
  if RunValueBelongsToInstall(Cmd + ' --probe') then
    RaiseException('Run command with arguments accepted');
  if RunValueBelongsToInstall(RemoveQuotes(Cmd)) then
    RaiseException('Noncanonical unquoted Run command accepted');
  if RunValueBelongsToInstall('"' + ExpandConstant('{app}') + '-backup\\HaloBattery.exe"') then
    RaiseException('Sibling Run command accepted');
  if RunValueBelongsToInstall('"D:\\portable\\HaloBattery.exe" --data-dir "' +
      ExpandConstant('{app}') + '\\"') then
    RaiseException('Run executable path confused with argument');
  NativeHandle := 0;
  if not SaveStringToFile(ExpandConstant('{param:report}'),
      'ok' + #13#10 + 'handle_bytes=' + IntToStr(SizeOf(NativeHandle)) + #13#10 +
      'elapsed_ms=' + IntToStr(InstallerTicks() - Started) + #13#10, False) then
    RaiseException('Cannot save fixture result');
end;
''', encoding="utf-8")
    result = subprocess.run([str(iscc), "/Q", str(runner)], capture_output=True,
                            text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise AssertionError("Pascal fixture compile failed: " + result.stdout + result.stderr)
    return base / "installer-helper.exe"


def run_fixture(workspace, iscc):
    if os.name != "nt":
        raise RuntimeError("native process fixture requires Windows")
    workspace = Path(workspace).resolve(strict=True)
    if workspace.drive.casefold() != "d:" or workspace == Path(workspace.anchor):
        raise ValueError("fixture workspace must be a directory below the D: drive root")
    contract = source_contract()
    iscc = Path(iscc).resolve(strict=True)
    children = []
    with tempfile.TemporaryDirectory(prefix="installer-safety-", dir=workspace) as temporary:
        base = Path(temporary).resolve()
        app, portable, prefix = base / "install 日本語", base / "portable", base / "install 日本語-backup"
        executables = [app / "HaloBattery.exe", app / "_internal/settings-ui/HaloBattery.Settings.exe",
                       portable / "HaloBattery.exe", portable / "_internal/settings-ui/HaloBattery.Settings.exe",
                       prefix / "HaloBattery.exe"]
        sentinels = {"portable.txt": b"", "config.json": b'{"interval":30}',
                     "history.json": b'{"devices":{}}', "halo_battery.log": b"preserve log",
                     "settings-window.json": b"preserve window", "iconify/pins.json": b"preserve icon",
                     "my notes.txt": b"user file"}
        for name, content in sentinels.items():
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        try:
            command = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"
            for executable in executables:
                executable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(command, executable)
                child = subprocess.Popen([str(executable), "/d", "/q", "/k"],
                                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                children.append(child)
                if child.poll() is not None:
                    raise AssertionError("synthetic process failed to start")
            report = base / "pascal-result.txt"
            runner = compile_runner(iscc, base, app)
            result = subprocess.run([str(runner), "/CURRENTUSER", "/SP-", "/VERYSILENT",
                                     "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS",
                                     "/DIR=" + str(app), "/report=" + str(report),
                                     "/LOG=" + str(base / "runner.log")],
                                    capture_output=True, timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode or not report.is_file():
                raise AssertionError("Pascal fixture failed: " + (base / "runner.log").read_text(
                    encoding="utf-8", errors="replace"))
            lines = report.read_text(encoding="utf-8-sig").splitlines()
            if not lines or lines[0] != "ok":
                raise AssertionError("Pascal helper did not report success: " + repr(lines))
            details = dict(line.split("=", 1) for line in lines[1:])
            expected = {child.pid for child in children[:2]}
            for child in children[:2]:
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    evidence = workspace / "installer-safety-failure"
                    evidence.mkdir(exist_ok=True)
                    for name in ("runner.log", "installer-helper.iss", "pascal-result.txt"):
                        shutil.copy2(base / name, evidence / name)
                    raise
            if any(child.poll() is not None for child in children[2:]):
                raise AssertionError("unrelated portable/prefix process was stopped")
            # Simulate only the source's extra deletion entries after removing a
            # fixture's logged program file. Every path stays in this fixture.
            (app / "HaloBattery.exe").unlink()
            for kind, template in contract["deletions"]:
                path = Path(template.replace("{app}", str(app)).replace("\\", os.sep)).resolve()
                if path != app and base not in path.parents:
                    raise AssertionError("fixture deletion escaped its root")
                if kind == "filesandordirs":
                    shutil.rmtree(path)
                elif kind == "dirifempty":
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            for name, content in sentinels.items():
                if (app / name).read_bytes() != content:
                    raise AssertionError("portable/user data was changed: " + name)
            return dict(passed=True, execution="Production Pascal helpers in no-install/no-registry fixture runner",
                        pascal_handle_bits=int(details["handle_bytes"]) * 8,
                        elapsed_ms=int(details["elapsed_ms"]),
                        targets_stopped=len(expected), unrelated_processes_preserved=len(children) - 2,
                        user_files_preserved=len(sentinels),
                        data_check="source-derived deletion plan; no uninstaller executed",
                        installer_appid_unchanged=True)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=3)
                if child.stdin is not None:
                    child.stdin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--iscc", type=Path, required=True)
    args = parser.parse_args()
    result = run_fixture(args.workspace, args.iscc)
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
