"""Verify complete native payload inclusion without PE dependency re-analysis."""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import venv
import zipfile

from tools.build_exe import build_process_environment, install_build_requirements, native_payload_entries
from tools import build_exe


class NativePayloadTests(unittest.TestCase):
    def test_complete_tree_keeps_paths_and_data_classification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            names = ["HaloBattery.Settings.exe", "coreclr.dll",
                     "Assets/Fonts/font.ttf", "ja-JP/Microsoft.ui.xaml.dll.mui",
                     "Assets/тест.svg"]
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"MZ" if path.suffix in (".dll", ".exe") else b"data")
            entries = native_payload_entries(root)
            self.assertEqual(len(entries), len(names))
            self.assertEqual({destination for destination, _, _ in entries},
                             {"settings-ui/" + name for name in names})
            for destination, source, kind in entries:
                self.assertEqual(kind, "DATA")
                # A Windows TEMP directory may use a short profile name while
                # resolve() expands it (RUNNER~1 -> runneradmin).
                self.assertTrue(Path(source).samefile(
                    root / destination.removeprefix("settings-ui/")))
            self.assertEqual(entries, native_payload_entries(root))

    def test_missing_companion_fails_before_packaging(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(FileNotFoundError, "Publish the settings companion"):
                native_payload_entries(Path(temp))


class HashedBuildRequirementsTests(unittest.TestCase):
    def test_dependency_failure_never_runs_packager_or_replaces_previous_output(self):
        with tempfile.TemporaryDirectory(prefix="halo build failure ") as temp:
            root = Path(temp)
            native = root / "native"
            native.mkdir()
            (native / "HaloBattery.Settings.exe").write_bytes(b"test-owned")
            for name in ("halo.ico", "version_info.txt"):
                (root / name).write_bytes(b"test-owned")
            dist = root / "dist"
            dist.mkdir()
            previous = dist / "previous.exe"
            previous.write_bytes(b"previous output")
            work = root / "work"
            with mock.patch.object(build_exe, "ROOT", root), mock.patch.object(sys, "argv", [
                    "build_exe.py", "--native-ui", str(native), "--distpath", str(dist), "--workpath", str(work)]), \
                    mock.patch.object(build_exe.venv, "EnvBuilder"), \
                    mock.patch.object(build_exe, "install_build_requirements",
                                      side_effect=subprocess.CalledProcessError(1, ["pip", "hash mismatch"])), \
                    mock.patch.object(build_exe.subprocess, "run") as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    build_exe.main()
                run.assert_not_called()
            self.assertEqual(previous.read_bytes(), b"previous output")
            self.assertFalse(list(work.glob("build-environment-*")))

    def test_pip_inputs_ignore_developer_configuration(self):
        with mock.patch.dict(os.environ, {"PIP_EXTRA_INDEX_URL": "https://private.invalid/",
                                         "PIP_NO_VERIFY": "anything"}):
            env = build_process_environment()
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        self.assertFalse(any(key.upper().startswith("PIP_") and key != "PIP_CONFIG_FILE" for key in env))
        with mock.patch("tools.build_exe.subprocess.run") as run:
            install_build_requirements(Path("build-python.exe"), Path("requirements-build.txt"))
        command = run.call_args.args[0]
        self.assertIn("--require-hashes", command)
        self.assertIn("--only-binary=:all:", command)
        self.assertIn("https://pypi.org/simple", command)
        self.assertTrue(run.call_args.kwargs["check"])

    def install_fixture(self, hash_kind):
        with tempfile.TemporaryDirectory(prefix="halo hashed build ") as temp:
            root = Path(temp)
            wheels = root / "wheels"
            wheels.mkdir()
            wheel = wheels / "halo_build_probe-1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("halo_build_probe/__init__.py", "VALUE = 'test-owned'\n")
                archive.writestr("halo_build_probe-1.0.dist-info/METADATA",
                                 "Metadata-Version: 2.1\nName: halo-build-probe\nVersion: 1.0\n")
                archive.writestr("halo_build_probe-1.0.dist-info/WHEEL",
                                 "Wheel-Version: 1.0\nGenerator: HaloBattery offline test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                archive.writestr("halo_build_probe-1.0.dist-info/RECORD", "")
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            suffix = "" if hash_kind == "missing" else " --hash=sha256:" + (
                digest if hash_kind == "valid" else "0" * 64)
            requirements = root / "requirements.txt"
            requirements.write_text("halo-build-probe==1.0" + suffix + "\n", encoding="utf-8")
            environment = root / "venv"
            venv.EnvBuilder(with_pip=True).create(environment)
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if hash_kind == "valid":
                install_build_requirements(python, requirements, wheels)
            else:
                with self.assertRaises(subprocess.CalledProcessError):
                    install_build_requirements(python, requirements, wheels)
            result = subprocess.run([str(python), "-I", "-c",
                                     "import importlib.util; print(importlib.util.find_spec('halo_build_probe') is not None)"],
                                    check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), str(hash_kind == "valid"))

    def test_offline_wheel_with_valid_committed_hash_installs(self):
        self.install_fixture("valid")

    def test_offline_wheel_with_altered_hash_is_refused(self):
        self.install_fixture("altered")

    def test_offline_wheel_without_hash_is_refused(self):
        self.install_fixture("missing")


class LocalBuildPreparationTests(unittest.TestCase):
    def test_batch_entrypoint_uses_verified_builder_without_ambient_install(self):
        batch = (build_exe.ROOT / "scripts/build_exe.bat").read_text(encoding="utf-8")
        python_commands = [line for line in batch.splitlines() if line.startswith("python ")]
        self.assertEqual(python_commands, [r"python tools\build_exe.py --prepare-local-build"])
        self.assertNotIn("pip install", batch)

    def test_assets_use_verified_python_and_native_publish_is_locked_entrypoint(self):
        python = Path("verified/python.exe")
        native = Path("output/native")
        intermediate = Path("output/native-obj")
        with mock.patch.object(build_exe.subprocess, "run") as run:
            build_exe.prepare_local_build(python, native, intermediate)
        self.assertEqual(len(run.call_args_list), 3)
        for call in run.call_args_list[:2]:
            self.assertEqual(call.args[0][:2], [str(python), "-I"])
            self.assertTrue(call.kwargs["check"])
        self.assertEqual(Path(run.call_args_list[0].args[0][2]).name, "make_icon.py")
        self.assertEqual(Path(run.call_args_list[1].args[0][2]).name, "make_version.py")
        native_command = run.call_args_list[2].args[0]
        self.assertIn(str(build_exe.ROOT / "scripts/build_settings.ps1"), native_command)
        self.assertIn(str(native.resolve()), native_command)
        self.assertIn(str(intermediate.resolve()), native_command)
        self.assertTrue(run.call_args_list[2].kwargs["check"])

    def test_local_build_verifies_once_then_prepares_and_freezes_with_same_python(self):
        with tempfile.TemporaryDirectory(prefix="halo local build ") as temp:
            root = Path(temp)
            events = []
            with mock.patch.object(build_exe, "ROOT", root), mock.patch.object(sys, "argv", [
                    "build_exe.py", "--prepare-local-build", "--workpath", str(root / "work")]), \
                    mock.patch.object(build_exe.venv, "EnvBuilder") as builder, \
                    mock.patch.object(build_exe, "install_build_requirements",
                                      side_effect=lambda python, *_: events.append(("verify", python))), \
                    mock.patch.object(build_exe, "prepare_local_build",
                                      side_effect=lambda python, *_: events.append(("prepare", python))), \
                    mock.patch.object(build_exe, "freeze_python",
                                      side_effect=lambda python, *_: events.append(("freeze", python))):
                build_exe.main()
            builder.assert_called_once_with(with_pip=True)
            builder.return_value.create.assert_called_once()
            self.assertEqual([event for event, _ in events], ["verify", "prepare", "freeze"])
            self.assertEqual(len({python for _, python in events}), 1)
            self.assertFalse(list((root / "work").glob("build-environment-*")))

    def test_local_dependency_failure_does_not_prepare_or_freeze(self):
        with tempfile.TemporaryDirectory(prefix="halo local failure ") as temp:
            root = Path(temp)
            with mock.patch.object(build_exe, "ROOT", root), mock.patch.object(sys, "argv", [
                    "build_exe.py", "--prepare-local-build", "--workpath", str(root / "work")]), \
                    mock.patch.object(build_exe.venv, "EnvBuilder"), \
                    mock.patch.object(build_exe, "install_build_requirements",
                                      side_effect=subprocess.CalledProcessError(1, ["pip", "hash mismatch"])), \
                    mock.patch.object(build_exe, "prepare_local_build") as prepare, \
                    mock.patch.object(build_exe, "freeze_python") as freeze:
                with self.assertRaises(subprocess.CalledProcessError):
                    build_exe.main()
                prepare.assert_not_called()
                freeze.assert_not_called()

    def test_native_failure_stops_packaging(self):
        with tempfile.TemporaryDirectory(prefix="halo native build failure ") as temp:
            root = Path(temp)
            with mock.patch.object(build_exe, "ROOT", root), mock.patch.object(sys, "argv", [
                    "build_exe.py", "--prepare-local-build", "--workpath", str(root / "work")]), \
                    mock.patch.object(build_exe.venv, "EnvBuilder"), \
                    mock.patch.object(build_exe, "install_build_requirements"), \
                    mock.patch.object(build_exe.subprocess, "run", side_effect=[None, None,
                        subprocess.CalledProcessError(1, ["powershell.exe", "locked restore failure"])]), \
                    mock.patch.object(build_exe, "freeze_python") as freeze:
                with self.assertRaises(subprocess.CalledProcessError):
                    build_exe.main()
                freeze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
