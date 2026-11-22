"""Tests for the stuck Audeze dongle: it answers every packet with an empty echo of the
request, so the battery never arrives although the headset is on. No hardware: the
dongle is a fake that answers like the real one did.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from providers import audeze  # noqa: E402

PATH = b"\\\\?\\hid#vid_3329&pid_4b18&mi_00&col02#x"


def echo(req):
    """What the stuck 4B18 dongle answered: 07 00 <byte 2 of the request> 00 ..."""
    return [audeze.REPORT_ID_IN, 0x00, req[2]] + [0] * (audeze.MSG_SIZE - 3)


def battery(level):
    return ([audeze.REPORT_ID_IN, 0x05, 0x5D, 0x07, 0x00] + list(audeze.BATTERY_MARKER)
            + [level] + [0] * (audeze.MSG_SIZE - 10))


class FakeDongle:
    """Answers each request with echo() while stuck, else with the battery."""

    def __init__(self, stuck, level=80):
        self.stuck, self.level, self.last = stuck, level, None

    def open_path(self, path):
        pass

    def write(self, data):
        self.last = list(data)
        return len(data)

    def get_input_report(self, report_id, size):
        if self.last is None:
            return []
        req, self.last = self.last, None
        return echo(req) if self.stuck else battery(self.level)

    def close(self):
        pass


def ifaces(product="Audeze Maxwell HID", pid=0x4B18):
    return [{"product_id": pid, "serial_number": "0000000000000000", "path": PATH,
             "usage_page": 0xFF13, "usage": 0x0001, "interface_number": 0,
             "product_string": product}]


class StuckDongleTests(unittest.TestCase):
    def setUp(self):
        self.dongle = FakeDongle(stuck=True)
        self.infos = ifaces()
        for p in (mock.patch.object(audeze.hid, "device", lambda: self.dongle),
                  mock.patch.object(audeze.hidlist, "enumerate", lambda vid: self.infos),
                  mock.patch.object(audeze.time, "sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)
        self.p = audeze.AudezeProvider()


    def test_the_xbox_dongle_string_counts_as_switched_off(self):
        # #244: with the headset off the Xbox dongle names itself "Audeze Maxwell
        # XBOX Dongle"; matching only the PC dongle's string left it reading the
        # stored level as if the headset were on.
        self.infos = ifaces("Audeze Maxwell XBOX Dongle", pid=0x4B18)
        self.p.poll()
        self.assertEqual(self.p.poll(), [])
        self.assertTrue(any("no headset linked" in line for line in self.p.diagnostics()))


if __name__ == "__main__":
    unittest.main()
