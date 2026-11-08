"""Installs the no-network / no-real-HID guards for the whole run (this file is imported
first by discovery) and checks that the tests' paths are not the user's real ones.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ helpers
import _sandbox  # noqa: E402

_sandbox.install_guards()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_hide_rename import hb  # noqa: E402


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

    def test_app_data_is_a_temporary_folder_not_the_real_one(self):
        real = os.environ.get("APPDATA")
        for name in ("DATA_DIR", "CONFIG_PATH", "HISTORY_PATH", "STATUS_PATH"):
            path = os.path.normcase(os.path.abspath(getattr(hb, name)))
            with self.subTest(path=name):
                if real:
                    self.assertFalse(path.startswith(os.path.normcase(os.path.join(real, "HaloBattery"))),
                                     path)
                self.assertTrue(path.startswith(os.path.normcase(os.path.abspath(_sandbox.root()))), path)


if __name__ == "__main__":
    unittest.main()
