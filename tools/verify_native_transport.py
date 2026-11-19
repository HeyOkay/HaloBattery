"""Verify an explicitly prepared frozen test bundle; never import app/providers.

prepare copies preferences into D: profiles. run-comparison launches the SAME
test executable sequentially with --legacy-native-io / default transport,
samples OS counters, and requests graceful exit using the test-only stop file.
The original Desktop bundle/configuration is never modified.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

MIB = 1024 * 1024
MODES = ("legacy", "native")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(MIB), b""):
            digest.update(block)
    return digest.hexdigest()


def d_path(path):
    resolved = Path(path).expanduser().resolve()
    if resolved.drive.lower() != "d:":
        raise ValueError(f"verification paths must be on D:, got {resolved}")
    return resolved


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def bundle_hashes(executable):
    directory = executable.parent.resolve()
    result = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(directory):
            raise ValueError("test bundle contains a file outside its own directory")
        result[path.relative_to(directory).as_posix()] = sha256(path)
    return result




class Observer:
    """Read process counters directly; no PowerShell/CIM observer subprocess."""

    class Memory(ctypes.Structure):
        _fields_ = [("cb", W.DWORD), ("PageFaultCount", W.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage", "PrivateUsage", "PrivateWorkingSetSize")
        ] + [("SharedCommitUsage", ctypes.c_uint64)]

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", W.DWORD),
                    ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                    ("pcPriClassBase", W.LONG), ("dwFlags", W.DWORD),
                    ("szExeFile", W.WCHAR * 260)]

    def __init__(self):
        if os.name != "nt":
            raise OSError("frozen test verification requires Windows")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "OpenProcess": ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            "CloseHandle": ([W.HANDLE], W.BOOL),
            "QueryFullProcessImageNameW": (
                [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)], W.BOOL),
            "K32GetProcessMemoryInfo": ([W.HANDLE, ctypes.c_void_p, W.DWORD], W.BOOL),
            "GetProcessHandleCount": ([W.HANDLE, ctypes.POINTER(W.DWORD)], W.BOOL),
            "K32EnumProcessModulesEx": ([W.HANDLE, ctypes.POINTER(W.HMODULE), W.DWORD,
                                         ctypes.POINTER(W.DWORD), W.DWORD], W.BOOL),
            "K32GetModuleBaseNameW": ([W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD], W.DWORD),
            "CreateToolhelp32Snapshot": ([W.DWORD, W.DWORD], W.HANDLE),
            "Process32FirstW": ([W.HANDLE, ctypes.POINTER(self.ProcessEntry)], W.BOOL),
            "Process32NextW": ([W.HANDLE, ctypes.POINTER(self.ProcessEntry)], W.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.memory = self.Memory()
        self.memory.cb = ctypes.sizeof(self.Memory)
        self.path_buffer = ctypes.create_unicode_buffer(32768)

    def processes(self):
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        entry = self.ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        rows = []
        try:
            found = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                rows.append(dict(pid=int(entry.th32ProcessID),
                                 parent_pid=int(entry.th32ParentProcessID),
                                 threads=int(entry.cntThreads), name=entry.szExeFile))
                found = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        return rows

    def image_path(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            size = W.DWORD(len(self.path_buffer))
            if not self.kernel.QueryFullProcessImageNameW(
                    handle, 0, self.path_buffer, ctypes.byref(size)):
                return None
            return str(Path(self.path_buffer.value).resolve())
        finally:
            self.kernel.CloseHandle(handle)

    def memory_for(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            if not self.kernel.K32GetProcessMemoryInfo(
                    handle, ctypes.byref(self.memory), ctypes.sizeof(self.memory)):
                return None
            if not self.memory.PrivateWorkingSetSize:
                raise RuntimeError("OS private-working-set counter is unavailable")
            handles = W.DWORD()
            if not self.kernel.GetProcessHandleCount(handle, ctypes.byref(handles)):
                raise ctypes.WinError(ctypes.get_last_error())
            return dict(private_working_set=int(self.memory.PrivateWorkingSetSize),
                        private_commit=int(self.memory.PrivateUsage),
                        private_commit_lifetime_peak=int(self.memory.PeakPagefileUsage),
                        working_set=int(self.memory.WorkingSetSize),
                        handles=int(handles.value))
        finally:
            self.kernel.CloseHandle(handle)

    def module_names(self, pid):
        handle = self.kernel.OpenProcess(0x1000 | 0x0010, False, pid)
        if not handle:
            return None
        try:
            modules = (W.HMODULE * 1024)()
            needed = W.DWORD()
            if not self.kernel.K32EnumProcessModulesEx(
                    handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3):
                return None
            names = []
            buffer = ctypes.create_unicode_buffer(260)
            for module in modules[:min(len(modules), needed.value // ctypes.sizeof(W.HMODULE))]:
                if self.kernel.K32GetModuleBaseNameW(handle, module, buffer, len(buffer)):
                    names.append(buffer.value)
            return sorted(names, key=str.casefold)
        finally:
            self.kernel.CloseHandle(handle)

    def sample_tree(self, root_pid):
        rows = self.processes()
        descendants = {root_pid}
        for _ in range(len(rows) + 1):
            expanded = descendants | {row["pid"] for row in rows
                                       if row["parent_pid"] in descendants}
            if expanded == descendants:
                break
            descendants = expanded
        measurements = []
        for row in rows:
            if row["pid"] not in descendants:
                continue
            memory = self.memory_for(row["pid"])
            if memory is None:
                continue
            measurements.append(dict(row, memory=memory))
        main = next((row for row in measurements if row["pid"] == root_pid), None)
        total = {field: sum(row["memory"][field] for row in measurements)
                 for field in ("private_working_set", "private_commit", "working_set", "handles")}
        return dict(main=main, tree=measurements, total=total)

    def refuse_running_halobattery(self, executable):
        names = {"halobattery.exe", executable.name.casefold()}
        running = [row for row in self.processes() if row["name"].casefold() in names]
        if running:
            raise RuntimeError("close existing HaloBattery before this test; running PIDs: "
                               + ", ".join(str(row["pid"]) for row in running))


def prepare(args):
    executable = d_path(args.exe)
    output = d_path(args.output)
    if not executable.is_file() or executable.suffix.casefold() != ".exe":
        raise ValueError("provide the frozen test executable")
    if output.is_relative_to(executable.parent):
        raise ValueError("profiles/reports must be outside the frozen bundle directory")
    if (executable.parent / "portable.txt").exists():
        raise ValueError("remove portable mode from the D: test bundle before preparing")
    original = Path(args.config).expanduser().resolve()
    cfg = json.loads(original.read_text(encoding="utf-8-sig"))
    if not isinstance(cfg, dict):
        raise ValueError("original configuration must be a JSON object")
    if output.exists() and any(output.iterdir()):
        raise ValueError("use a fresh empty verification output directory")
    output.mkdir(parents=True, exist_ok=True)
    baseline_prefs = dict(cfg)
    baseline_prefs.update(interval=15, bluetooth=True, update_check=False, status_file=True)
    profiles = {}
    for mode in MODES:
        profile = output / mode
        appdata = profile / "roaming"
        data = appdata / "HaloBattery"
        data.mkdir(parents=True, exist_ok=False)
        write_json(data / "config.json", baseline_prefs)
        profiles[mode] = dict(appdata=str(appdata), data=str(data),
                              stop_file=str(profile / "stop-request.txt"))
    manifest = dict(
        executable=str(executable), bundle=str(executable.parent),
        bundle_hashes=bundle_hashes(executable), original_config=str(original),
        original_config_sha256=sha256(original), profiles=profiles,
        mode_arguments={"legacy": ["--legacy-native-io"],
                        "native": []},
        copied_config_overrides=dict(interval=15, bluetooth=True, update_check=False,
                                     status_file=True),
        required_reading_prefixes=args.require_prefix,
        prepared_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        constraints=["No provider or HID command is constructed by this observer.",
                     "Application polling uses the unchanged, sourced provider requests.",
                     "Do not run another HaloBattery simultaneously."],
    )
    write_json(output / "manifest.json", manifest)
    print(json.dumps(dict(prepared=str(output / "manifest.json"),
                          executable=str(executable), launched=False)), flush=True)
    return 0


def load_manifest(output):
    output = d_path(output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    executable = d_path(manifest["executable"])
    if bundle_hashes(executable) != manifest["bundle_hashes"]:
        raise RuntimeError("frozen bundle changed after preparation")
    if sha256(manifest["original_config"]) != manifest["original_config_sha256"]:
        raise RuntimeError("original user preferences changed after preparation")
    for profile in manifest["profiles"].values():
        for field in ("appdata", "data", "stop_file"):
            if not d_path(profile[field]).is_relative_to(output):
                raise RuntimeError("verification profile escapes the prepared output directory")
    return output, manifest


def read_status(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("devices"), list) else None


def invalid_statuses(data):
    problems = []
    for item in data.get("devices", []):
        if not isinstance(item, dict):
            problems.append("non-object device")
            continue
        level = item.get("level")
        if level is not None and (type(level) is not int or not 0 <= level <= 100):
            problems.append(f"invalid level for {item.get('key')}")
        for field in ("online", "charging", "charging_known", "polling_stale"):
            if field in item and type(item[field]) is not bool:
                problems.append(f"invalid {field} for {item.get('key')}")
    return problems


def online_reading_keys(statuses):
    return sorted({item["key"] for entry in statuses for item in entry["status"]["devices"]
                   if isinstance(item, dict) and isinstance(item.get("key"), str)
                   and type(item.get("level")) is int and 0 <= item["level"] <= 100
                   and item.get("online") is True and item.get("polling_stale") is not True})


def summaries(samples, warmup):
    warm = [sample for sample in samples if sample["elapsed"] >= warmup and sample["main"]]
    if not warm:
        raise RuntimeError("no warm measurements were collected")
    result = {}
    for label in ("main", "tree"):
        result[label] = {}
        for field in ("private_working_set", "private_commit", "working_set", "handles"):
            values = [(sample["main"]["memory"] if label == "main" else sample["total"])[field]
                      for sample in warm]
            all_values = [(sample["main"]["memory"] if label == "main" else sample["total"])[field]
                          for sample in samples if sample["main"]]
            result[label][field] = dict(
                warm_min=min(values), warm_median=statistics.median(values),
                warm_max=max(values), entire_run_sampled_max=max(all_values))
    result["main_threads"] = dict(
        warm_median=statistics.median(sample["main"]["threads"] for sample in warm),
        warm_max=max(sample["main"]["threads"] for sample in warm))
    result["main_private_commit_os_lifetime_peak"] = max(
        sample["main"]["memory"]["private_commit_lifetime_peak"] for sample in samples
        if sample["main"])
    return result


def run_one(output, manifest, mode, seconds, warmup, audible):
    executable = d_path(manifest["executable"])
    observer = Observer()
    observer.refuse_running_halobattery(executable)
    profile = manifest["profiles"][mode]
    data = d_path(profile["data"])
    stop_file = d_path(profile["stop_file"])
    if stop_file.exists() or (output / f"{mode}-report.json").exists():
        raise RuntimeError("this mode was already run; prepare a fresh output directory")
    environment = os.environ.copy()
    environment["APPDATA"] = profile["appdata"]
    command = [str(executable), *manifest["mode_arguments"][mode],
               "--verify-native-io", "--test-stop-file", str(stop_file)]
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=str(executable.parent), env=environment,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=0x08000000)
    print(json.dumps(dict(event="started", mode=mode, pid=process.pid, command=command,
                          isolated_appdata=profile["appdata"])), flush=True)
    if audible:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.MessageBeep.argtypes, user.MessageBeep.restype = [W.UINT], W.BOOL
        user.MessageBeep(0x40)
    samples, statuses, problems = [], [], []
    last_status_hash = None
    first_status_seconds = None
    failure = None
    known_children = set()
    module_names = None
    try:
        while time.monotonic() - started < seconds:
            if process.poll() is not None:
                raise RuntimeError(f"application exited early with code {process.returncode}")
            if os.path.normcase(observer.image_path(process.pid) or "") != os.path.normcase(str(executable)):
                raise RuntimeError("launched PID does not match the prepared test executable")
            sample = observer.sample_tree(process.pid)
            sample["elapsed"] = time.monotonic() - started
            if not sample["main"]:
                raise RuntimeError("main process disappeared during observation")
            known_children.update(row["pid"] for row in sample["tree"]
                                  if row["pid"] != process.pid)
            samples.append(sample)
            if module_names is None and sample["elapsed"] >= warmup:
                module_names = observer.module_names(process.pid)
            status = read_status(data / "status.json")
            if status is not None:
                status_hash = hashlib.sha256(json.dumps(status, sort_keys=True).encode()).hexdigest()
                if status_hash != last_status_hash:
                    statuses.append(dict(elapsed=sample["elapsed"], status=status))
                    problems.extend(invalid_statuses(status))
                    first_status_seconds = (sample["elapsed"] if first_status_seconds is None
                                            else first_status_seconds)
                    last_status_hash = status_hash
            time.sleep(0.25)
    except Exception as exc:
        failure = str(exc)
    finally:
        stop_requested = time.monotonic()
        stop_file.write_text("graceful test shutdown\n", encoding="utf-8")
        try:
            process.wait(timeout=8.0)
            exit_seconds = time.monotonic() - stop_requested
        except subprocess.TimeoutExpired:
            exit_seconds = None
            failure = (failure + "; " if failure else "") + (
                f"graceful shutdown exceeded 8s; PID {process.pid} remains for root inspection")
        # No force termination: the root agent owns explicit failure recovery.
    final_status = read_status(data / "status.json")
    log_path = data / "halo_battery.log"
    log_parts = []
    for part in (data / "halo_battery.log.1", log_path):
        try:
            log_parts.append(part.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    combined_log = "\n".join(log_parts)
    log_tail = combined_log[-24000:]
    enabled_header = "native I/O enabled: in-process transport"
    enabled_observed = enabled_header.casefold() in combined_log.casefold()
    mode_verified = enabled_observed if mode == "native" else not enabled_observed
    attention = [line for line in log_tail.splitlines()
                 if any(word in line.casefold() for word in (
                     "error", "traceback", "quarantin", "stale", "timeout", "native"))]
    remaining_pids = {row["pid"] for row in observer.processes()}
    still_alive_children = sorted(known_children & remaining_pids)
    while still_alive_children and time.monotonic() - stop_requested < 8.0:
        time.sleep(0.05)
        remaining_pids = {row["pid"] for row in observer.processes()}
        still_alive_children = sorted(known_children & remaining_pids)
    tree_exit_seconds = time.monotonic() - stop_requested if not still_alive_children else None
    original_unchanged = sha256(manifest["original_config"]) == manifest["original_config_sha256"]
    bundle_unchanged = bundle_hashes(executable) == manifest["bundle_hashes"]
    summary = summaries(samples, warmup) if samples and samples[-1]["elapsed"] >= warmup else None
    reading_keys = online_reading_keys(statuses)
    missing_required = [prefix for prefix in manifest.get("required_reading_prefixes", [])
                        if not any(key.startswith(prefix) for key in reading_keys)]
    report = dict(
        mode=mode, pid=process.pid, command=command, duration_seconds=seconds,
        warmup_seconds=warmup, executable_sha256=sha256(executable),
        bundle_unchanged=bundle_unchanged, original_config_unchanged=original_unchanged,
        memory=summary, samples=samples, status_changes=statuses,
        first_status_seconds=first_status_seconds, invalid_statuses=problems,
        online_reading_keys=reading_keys, missing_required_reading_prefixes=missing_required,
        final_status=final_status, log_attention=attention[-100:], log_tail=log_tail,
        actual_mode_verified_from_log=mode_verified,
        native_enable_header_observed=enabled_observed, loaded_modules=module_names,
        rtss_injected=(any(name.casefold() == "rtsshooks64.dll" for name in module_names)
                       if module_names is not None else None),
        shutdown=dict(returncode=process.poll(), elapsed_seconds=exit_seconds,
                      tree_exit_elapsed_seconds=tree_exit_seconds,
                      final_status_running=final_status.get("running") if final_status else None,
                      observed_children_still_alive=still_alive_children),
        failure=failure,
        healthy=(failure is None and process.poll() == 0 and bool(reading_keys)
                 and not missing_required and mode_verified
                 and not problems and original_unchanged and bundle_unchanged
                 and final_status is not None and final_status.get("running") is False
                 and not still_alive_children),
        limitations=["Actual application/provider polling is being exercised.",
                     "No device command bytes are generated or modified by this verifier.",
                     "Working-set peaks are sampled; private-commit OS lifetime peaks are also recorded."],
    )
    write_json(output / f"{mode}-report.json", report)
    print(json.dumps(dict(event="finished", mode=mode, pid=process.pid,
                          healthy=report["healthy"], failure=failure,
                          report=str(output / f"{mode}-report.json"))), flush=True)
    return report


def compare(output, manifest):
    reports = {mode: json.loads((output / f"{mode}-report.json").read_text(encoding="utf-8"))
               for mode in MODES}
    baseline, native = reports["legacy"], reports["native"]
    if any(not isinstance(report.get("memory"), dict) for report in reports.values()):
        raise RuntimeError("both modes need completed warm measurements before comparison")
    if baseline["executable_sha256"] != native["executable_sha256"]:
        raise RuntimeError("comparison used different executable bytes")
    delta = {}
    checks = {}
    for label in ("main", "tree"):
        delta[label] = {}
        for field in ("private_working_set", "private_commit"):
            delta[label][field] = {
                statistic: native["memory"][label][field][statistic]
                           - baseline["memory"][label][field][statistic]
                for statistic in ("warm_median", "warm_max", "entire_run_sampled_max")}
        checks[label] = all(value <= 5 * MIB
                            for item in delta[label].values() for value in item.values())
    commit_os_delta = (native["memory"]["main_private_commit_os_lifetime_peak"]
                       - baseline["memory"]["main_private_commit_os_lifetime_peak"])
    checks["main"] = checks["main"] and commit_os_delta <= 5 * MIB
    observed = {}
    for mode, report in reports.items():
        observed[mode] = {item["key"]: item
            for entry in report["status_changes"] for item in entry["status"]["devices"]
            if isinstance(item, dict) and isinstance(item.get("key"), str)}
    missing = sorted(set(observed["legacy"]) - set(observed["native"]))
    missing_readings = sorted(set(baseline["online_reading_keys"])
                              - set(native["online_reading_keys"]))
    result = dict(
        healthy=all(report["healthy"] for report in reports.values()),
        same_executable=True, duration_seconds=baseline["duration_seconds"],
        delta_bytes=delta, budget_bytes=5 * MIB,
        main_memory_budget_pass=checks["main"], tree_memory_budget_pass=checks["tree"],
        main_private_commit_os_lifetime_peak_delta=commit_os_delta,
        observed_devices=observed, baseline_keys_missing_in_native=missing,
        baseline_fresh_readings_missing_in_native=missing_readings,
        original_config_unchanged=sha256(manifest["original_config"]) ==
                                  manifest["original_config_sha256"],
        bundle_unchanged=bundle_hashes(Path(manifest["executable"])) == manifest["bundle_hashes"],
        note=("Tree counters include the existing Bluetooth PowerShell process; inspect its "
              "variation separately from main-process transport overhead."),
    )
    result["accepted"] = (result["healthy"] and all(checks.values()) and not missing and not missing_readings
                          and result["original_config_unchanged"] and result["bundle_unchanged"])
    write_json(output / "comparison.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--exe", type=Path, required=True)
    prep.add_argument("--config", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--require-prefix", action="append", default=[],
                      help="device-key prefix that must obtain an online nonstale battery reading")
    for command in ("run", "run-comparison", "compare"):
        part = commands.add_parser(command)
        part.add_argument("--output", type=Path, required=True)
        if command != "compare":
            part.add_argument("--seconds", type=float, default=45.0)
            part.add_argument("--warmup", type=float, default=10.0)
            part.add_argument("--audible", action="store_true")
        if command == "run":
            part.add_argument("--mode", choices=MODES, required=True)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        return prepare(args)
    output, manifest = load_manifest(args.output)
    if args.action in ("run", "run-comparison"):
        if not 30 <= args.seconds <= 60 or not 0 <= args.warmup < args.seconds:
            parser.error("use 30..60 seconds with warmup shorter than duration")
        modes = (args.mode,) if args.action == "run" else MODES
        for mode in modes:
            report = run_one(output, manifest, mode, args.seconds, args.warmup, args.audible)
            if not report["healthy"]:
                return 2
        if args.action == "run":
            return 0
    result = compare(output, manifest)
    print(json.dumps({key: value for key, value in result.items()
                      if key != "observed_devices"}, indent=2))
    return 0 if result["accepted"] else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps(dict(error=str(exc))), file=sys.stderr, flush=True)
        raise SystemExit(2)
