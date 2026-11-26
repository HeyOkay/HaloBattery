"""Missing/blocked native HID imports must not prevent the rest of the app loading."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock

from halo import hid_backend

ROOT = Path(__file__).resolve().parents[1]


class AvailableHidTests(unittest.TestCase):
    def test_genuine_backend_is_returned_unchanged_without_touching_devices(self):
        backend = ModuleType('hid')
        backend.enumerate = mock.Mock(side_effect=AssertionError('unexpected enumeration'))
        backend.device = mock.Mock(side_effect=AssertionError('unexpected device creation'))
        with mock.patch.dict(sys.modules, {'hid': backend}):
            result, error = hid_backend.load_hid()
            self.assertIs(result, backend)
            self.assertEqual(error, '')
            again, error = hid_backend.load_hid()
            self.assertIs(again, backend)
            self.assertIs(sys.modules['hid'], backend)
            self.assertEqual(error, '')
        backend.enumerate.assert_not_called()
        backend.device.assert_not_called()


CHILD = r'''
import importlib, importlib.abc, importlib.util, importlib.machinery
import json, pathlib, sys, threading
from unittest import mock

root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'tests'))
import _sandbox
_sandbox.install_port_guards()
import socket
def refuse_network(*args, **kwargs):
    raise AssertionError('unexpected network access')
socket.create_connection = socket.getaddrinfo = refuse_network
socket.socket.connect = socket.socket.connect_ex = refuse_network

class FailingHid(importlib.abc.MetaPathFinder):
    attempts = 0
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'hid':
            self.attempts += 1
            kind = {'import': ImportError, 'os': OSError, 'missing': ModuleNotFoundError}[sys.argv[2]]
            raise kind('HID_BLOCKED_TEST: native extension unavailable; \u8def\u5f84')

failure = FailingHid()
sys.modules.pop('hid', None)
sys.meta_path.insert(0, failure)
from halo import hid_backend
backend, reason = hid_backend.load_hid()
assert backend is None and 'HID_BLOCKED_TEST' in reason
assert '路径' in reason
assert failure.attempts == 1
import hid
assert isinstance(hid, type(sys))
assert hid.enumerate() == []
assert hid.enumerate(0xffff, 0xffff) == []
first = hid.enumerate()
first.append({'not': 'a real device'})
assert hid.enumerate() == []
try:
    hid.device(path=b'never opened')
except OSError as exc:
    assert reason in str(exc)
else:
    raise AssertionError('unavailable backend created a device')
for operation in ('read', 'write', 'open_path', 'send_feature_report'):
    assert not hasattr(hid, operation)

# Every legacy provider import sees the controlled facade. None may start an
# active device reader merely because its module is imported.
with mock.patch.object(threading.Thread, 'start', side_effect=AssertionError('unexpected worker at import')):
    import providers
    assert len([key for key in sys.modules if key.startswith('providers.')]) > 50
    loader = importlib.machinery.SourceFileLoader('hid_failure_app_probe', str(root / 'halo_battery.pyw'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    app = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = app
    loader.exec_module(app)
    assert app.hid is None, 'application must expose backend absence, not pretend facade is usable'

# Repairing the import path does not silently change a running process's
# backend. A deliberate restart is required after the native module is fixed.
sys.meta_path.remove(failure)
class RecoveredHid(importlib.abc.MetaPathFinder):
    attempts = 0
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'hid':
            self.attempts += 1
            raise AssertionError('silent backend re-import')
recovered = RecoveredHid()
sys.meta_path.insert(0, recovered)
for helper in (hid_backend, importlib.reload(hid_backend)):
    again, repeated_reason = helper.load_hid()
    assert again is None and repeated_reason == reason
assert recovered.attempts == 0
assert sys.modules['hid'] is hid
print(json.dumps({'reason': reason, 'provider_imports_ok': True, 'app_import_ok': True,
                  'native_import_attempts': failure.attempts, 'recovery_import_attempts': recovered.attempts}))
'''


class UnavailableHidTests(unittest.TestCase):
    def test_import_and_native_dll_failures_leave_app_importable(self):
        for failure in ('import', 'os', 'missing'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                result = subprocess.run([sys.executable, '-B', '-c', CHILD, str(ROOT), failure],
                    cwd=ROOT, env=dict(os.environ, APPDATA=temp),
                    capture_output=True, text=True, timeout=25)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertTrue(report['app_import_ok'])
                self.assertTrue(report['provider_imports_ok'])
                self.assertEqual(report['native_import_attempts'], 1)
                self.assertEqual(report['recovery_import_attempts'], 0)


if __name__ == '__main__':
    unittest.main()
