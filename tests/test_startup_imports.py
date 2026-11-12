"""Fresh-process startup imports must not depend on loose packages beside an EXE.

Load the real application as a module (never __main__) with fake HID/network guards.
Only paths and startup data selection are exercised; no tray or device poll runs.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox

ROOT = Path(__file__).resolve().parents[1]

CHILD = textwrap.dedent(r'''
    import importlib.machinery
    import importlib.util
    import json
    from pathlib import Path
    import sys

    root, app_dir, frozen = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == "1"
    sys.path.insert(0, str(root / "tests"))
    import _sandbox
    _sandbox.install_guards()
    if frozen:
        # Model PyInstaller's bundled-module path before executing app startup.
        # The loose EXE directory must never be placed in front of this path.
        sys.path.insert(0, str(root))
        sys.frozen = True
        sys._MEIPASS = str(root)
        sys.executable = str(app_dir / "HaloBattery.exe")
    else:
        # A foreign source-launch cwd is importable, but the app's own source
        # directory must win. ROOT deliberately was not added above.
        sys.path.insert(0, str(Path.cwd()))
    name = "startup_import_regression"
    loader = importlib.machinery.SourceFileLoader(name, str(root / "halo_battery.pyw"))
    spec = importlib.util.spec_from_loader(name, loader)
    app = importlib.util.module_from_spec(spec)
    sys.modules[name] = app
    loader.exec_module(app)
    import halo
    import providers
    print(json.dumps({
        "halo": str(Path(halo.__file__).resolve()),
        "providers": str(Path(providers.__file__).resolve()),
        "base_dir": app.BASE_DIR,
        "data_base_dir": app.DATA_BASE_DIR,
        "data_dir": app.DATA_DIR,
        "portable": app.PORTABLE,
        "portable_requested": app.PORTABLE_REQUESTED,
        "sys_path": sys.path,
    }))
''')


class StartupImportTests(unittest.TestCase):
    def run_startup(self, *, frozen, portable=False):
        scratch = Path(_sandbox.mkdtemp(prefix="startup_imports_"))
        app_dir = scratch / "exe"
        cwd = scratch / "foreign-cwd"
        appdata = scratch / "appdata"
        temp = scratch / "temp"
        for folder in (app_dir, cwd, appdata, temp):
            folder.mkdir()
        for folder in (app_dir, cwd):
            for package in ("halo", "providers"):
                path = folder / package
                path.mkdir()
                (path / "__init__.py").write_text(
                    "raise ImportError('loose package must not shadow app: " + package + "')\n",
                    encoding="utf-8")
        if portable:
            (app_dir / "portable.txt").write_text("", encoding="utf-8")
        environment = dict(os.environ, APPDATA=str(appdata), TEMP=str(temp), TMP=str(temp))
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", CHILD, str(ROOT), str(app_dir), str(int(frozen))],
            cwd=cwd, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(Path(state["halo"]), ROOT / "halo" / "__init__.py")
        self.assertEqual(Path(state["providers"]), ROOT / "providers" / "__init__.py")
        expected_base = app_dir if frozen else ROOT
        self.assertEqual(Path(state["base_dir"]), expected_base)
        self.assertEqual(Path(state["data_base_dir"]), expected_base)
        self.assertEqual(state["portable"], portable)
        self.assertEqual(state["portable_requested"], portable)
        self.assertEqual(Path(state["data_dir"]), app_dir if portable else appdata / "HaloBattery")
        if frozen:
            self.assertNotIn(str(app_dir), state["sys_path"])
        else:
            self.assertEqual(Path(state["sys_path"][0]), ROOT)
        self.assertEqual((app_dir / "portable.txt").exists(), portable)
        return state

    def test_frozen_ignores_loose_packages_beside_executable(self):
        self.run_startup(frozen=True)

    def test_frozen_portable_marker_keeps_executable_data_directory(self):
        self.run_startup(frozen=True, portable=True)

    def test_source_launch_from_foreign_cwd_prefers_its_own_packages(self):
        self.run_startup(frozen=False)


if __name__ == "__main__":
    unittest.main()
