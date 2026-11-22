"""The Phone Link read guard must not choose app packages before startup does."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

import _sandbox


ROOT = Path(__file__).resolve().parents[1]
CHILD = textwrap.dedent(r'''
    import importlib
    from pathlib import Path
    import sys

    tests, trusted, foreign = map(Path, sys.argv[1:])
    sys.path.insert(0, str(tests))
    sys.path.insert(0, str(foreign))
    import _sandbox
    _sandbox.install_guards()
    _sandbox.install_phone_cache_guard()
    assert 'providers' not in sys.modules, 'guard eagerly imported providers'
    hooks = [finder for finder in sys.meta_path
             if getattr(finder, '_phone_cache_sandbox', False)]
    assert len(hooks) == 1, 'repeated guard installation duplicated the hook'

    # Model the app choosing its trusted source path only after guard setup.
    sys.path.insert(0, str(trusted))
    module = importlib.import_module('providers.phone_link')
    assert Path(module.__file__).parent == trusted / 'providers'
    for path in (None, foreign):
        try:
            module._read_snapshot(path)
        except AssertionError as exc:
            assert 'real Phone Link local state' in str(exc), str(exc)
        else:
            raise AssertionError('unsafe cache path was accepted')
    assert module.calls == [], 'blocked reads reached the underlying reader'
    safe = Path(_sandbox.mkdtemp())
    assert module._read_snapshot(safe) == safe
    assert module.calls == [safe]

    # Also cover reinstalling after the provider already exists and reloading it.
    _sandbox.install_phone_cache_guard()
    module = importlib.reload(module)
    try:
        module._read_snapshot()
    except AssertionError as exc:
        assert 'real Phone Link local state' in str(exc), str(exc)
    else:
        raise AssertionError('reload removed the cache guard')
    assert module.calls == []
''')
APP_CHILD = textwrap.dedent(r'''
    import importlib.machinery
    import importlib.util
    from pathlib import Path
    import sys

    root = Path(sys.argv[1])
    sys.path.insert(0, str(root / 'tests'))
    sys.path.insert(0, str(Path.cwd()))
    import _sandbox
    _sandbox.install_guards()
    assert 'providers' not in sys.modules
    name = 'phone_guard_app_bootstrap'
    loader = importlib.machinery.SourceFileLoader(name, str(root / 'halo_battery.pyw'))
    spec = importlib.util.spec_from_loader(name, loader)
    app = importlib.util.module_from_spec(spec)
    sys.modules[name] = app
    loader.exec_module(app)
    from providers import phone_link
    assert Path(phone_link.__file__).resolve() == root / 'providers' / 'phone_link.py'
    try:
        phone_link.PhoneLinkProvider().poll()
    except AssertionError as exc:
        assert 'real Phone Link local state' in str(exc), str(exc)
    else:
        raise AssertionError('default provider poll escaped the cache guard')
''')


class PhoneLinkSandboxTests(unittest.TestCase):
    def test_app_bootstrap_selects_own_package_before_guard_blocks_default_poll(self):
        scratch = Path(_sandbox.mkdtemp(prefix="phone_guard_app_"))
        foreign = scratch / "foreign"
        (foreign / "providers").mkdir(parents=True)
        (foreign / "providers" / "__init__.py").write_text(
            "raise AssertionError('guard imported a foreign providers package')\n",
            encoding="utf-8")
        for name in ("appdata", "localappdata", "temp"):
            (scratch / name).mkdir()
        environment = dict(
            os.environ, APPDATA=str(scratch / "appdata"),
            LOCALAPPDATA=str(scratch / "localappdata"), TEMP=str(scratch / "temp"),
            TMP=str(scratch / "temp"), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", APP_CHILD, str(ROOT)],
            cwd=foreign, env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_guard_defers_package_selection_and_guards_later_imports(self):
        scratch = Path(_sandbox.mkdtemp(prefix="phone_guard_startup_"))
        trusted, foreign = scratch / "trusted", scratch / "foreign"
        for directory in (trusted, foreign):
            (directory / "providers").mkdir(parents=True)
        (foreign / "providers" / "__init__.py").write_text(
            "raise AssertionError('guard imported a foreign providers package')\n",
            encoding="utf-8")
        (trusted / "providers" / "__init__.py").write_text("", encoding="utf-8")
        (trusted / "providers" / "phone_link.py").write_text(
            "calls = []\ndef _read_snapshot(package_root=None):\n"
            "    calls.append(package_root)\n    return package_root\n", encoding="utf-8")
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", CHILD, str(ROOT / "tests"),
             str(trusted), str(foreign)],
            cwd=foreign, env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
