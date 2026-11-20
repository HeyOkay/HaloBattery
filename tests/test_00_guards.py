"""Installs the no-network / no-real-device guards for the whole run (this file is imported
first by discovery) and checks that the tests' paths are not the user's real ones.

Run from the repository root:

    python -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ helpers
import _sandbox  # noqa: E402

_sandbox.install_guards()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_hide_rename import hb  # noqa: E402

try:
    import serial
except ImportError:
    serial = None


class GuardTests(unittest.TestCase):
    def test_network_is_blocked(self):
        import socket
        with self.assertRaises(AssertionError):
            socket.create_connection(("example.invalid", 80))
        with socket.socket() as sock, self.assertRaises(AssertionError):
            sock.connect(("127.0.0.1", 9))

    def test_real_hid_is_blocked(self):
        import hid
        with self.assertRaises(AssertionError):
            hid.enumerate()
        with self.assertRaises(AssertionError):
            hid.device()

    @unittest.skipIf(serial is None, "pyserial is not installed")
    def test_real_serial_ports_are_blocked(self):
        from serial.tools import list_ports
        with self.assertRaises(AssertionError):
            list_ports.comports()
        with self.assertRaises(AssertionError):
            serial.Serial("COM1", 115200)

    @unittest.skipIf(serial is None, "pyserial is not installed")
    def test_serial_aliases_and_subclasses_cannot_open_a_port(self):
        import importlib
        native = importlib.import_module(
            "serial.serialwin32" if os.name == "nt" else "serial.serialposix")

        class DerivedSerial(native.Serial):
            pass

        # No port is configured: even a broken guard cannot reach real hardware.
        port = DerivedSerial(port=None)
        with self.assertRaisesRegex(AssertionError, "real serial port"):
            port.open()

    def test_real_usb_is_blocked(self):
        import usb.core
        with self.assertRaises(AssertionError):
            usb.core.find(idVendor=0x2CA3, idProduct=0x4011)

    def test_native_usb_backend_is_blocked(self):
        import libusb_package
        with self.assertRaisesRegex(AssertionError, "native USB backend"):
            libusb_package.get_libusb1_backend()

    def test_the_dji_session_is_blocked_before_loading_native_usb(self):
        from unittest import mock
        from providers import dji
        with mock.patch.object(dji.usb.core, "find") as find:
            with self.assertRaisesRegex(AssertionError, "native USB backend"):
                dji._Receiver()._session()
        find.assert_not_called()

    @unittest.skipIf(serial is None, "pyserial is not installed")
    def test_the_sony_provider_cannot_reach_a_port_through_the_guard(self):
        # the provider calls these by attribute, so the guard covers the provider itself
        from providers import sony_inzone
        with self.assertRaises(AssertionError):
            sony_inzone.SonyInzoneProvider().poll()

    def test_app_data_is_a_temporary_folder_not_the_real_one(self):
        real = os.environ.get("APPDATA")
        for name in ("DATA_DIR", "CONFIG_PATH", "HISTORY_PATH", "STATUS_PATH"):
            path = os.path.normcase(os.path.abspath(getattr(hb, name)))
            with self.subTest(path=name):
                if real:
                    self.assertFalse(path.startswith(os.path.normcase(os.path.join(real, "HaloBattery"))),
                                     path)
                self.assertTrue(path.startswith(os.path.normcase(os.path.abspath(_sandbox.root()))), path)

    def test_absent_optional_serial_skips_only_serial_checks(self):
        script = textwrap.dedent('''
            import builtins, io, json, sys, unittest
            sys.path[:0] = ["tests", "."]
            original = builtins.__import__
            def without_serial(name, *args, **kwargs):
                if name == "serial" or name.startswith("serial."):
                    raise ImportError("optional pyserial unavailable for this fixture")
                return original(name, *args, **kwargs)
            builtins.__import__ = without_serial
            names = [
                "test_real_serial_ports_are_blocked",
                "test_serial_aliases_and_subclasses_cannot_open_a_port",
                "test_the_sony_provider_cannot_reach_a_port_through_the_guard",
                "test_network_is_blocked", "test_real_hid_is_blocked",
            ]
            suite = unittest.defaultTestLoader.loadTestsFromNames([
                "test_00_guards.GuardTests." + name for name in names])
            output = io.StringIO()
            result = unittest.TextTestRunner(stream=output).run(suite)
            if not result.wasSuccessful():
                raise AssertionError(output.getvalue())
            print(json.dumps({"run": result.testsRun, "skipped": len(result.skipped)}))
        ''')
        result = subprocess.run([sys.executable, "-B", "-c", script],
                                cwd=os.path.dirname(os.path.dirname(__file__)),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]),
                         {"run": 5, "skipped": 3})


if __name__ == "__main__":
    unittest.main()
