"""Tests for providers/vaxee.py. No hardware is needed.

The fake device answers the feature-report exchange of a VAXEE mouse behind a
VXD02 4K dongle, as documented in ryanlewis/mousebatt's src/protocol.rs:

    request  0e a5 <cmd> 01 01 ...
    reply    0e a5 <cmd> 01 01 <value>

Command 0x0b carries the level in 5 % steps and 0x10 the charging flag.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import vaxee as V  # noqa: E402


def reply(cmd, value):
    r = [0x00] * V.REPORT_LEN
    r[0] = V.REPORT_ID
    r[1] = V.HEADER
    r[2] = cmd
    r[3] = 0x01
    r[4] = 0x01
    r[5] = value
    return r


class FakeCollection:
    """The vendor collection. answer=False behaves like a sleeping mouse."""

    def __init__(self, battery=17, charging=0, answer=True):
        self.battery = battery
        self.charging = charging
        self.answer = answer
        self.sent = []
        self.opened = 0
        self.cmd = None

    def send(self, data):
        self.sent.append(list(data))
        self.cmd = data[2]
        return len(data)

    def get(self, length):
        if not self.answer or self.cmd not in (V.CMD_BATTERY, V.CMD_CHARGING):
            return []
        value = self.battery if self.cmd == V.CMD_BATTERY else self.charging
        return reply(self.cmd, value)[:length]


def fake_device_class(cols):
    class FakeDevice:
        def open_path(self, path):
            self.c = cols[path]
            self.c.opened += 1

        def send_feature_report(self, data):
            return self.c.send(data)

        def get_feature_report(self, report_id, length):
            return self.c.get(length)

        def close(self):
            pass
    return FakeDevice


def entries(pid=0x2001, page=V.USAGE_PAGE, product="VXD02 4K Dongle", prefix=b"device"):
    return [{"product_id": pid, "vendor_id": V.VAXEE_VID,
             "interface_number": 1, "usage_page": page, "usage": 0x0000,
             "path": prefix + b"-1", "product_string": product}]


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (V.hid, V.hidlist, V.time)
        self.now = [1000.0]
        V.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)
        self.provider = V.VaxeeProvider()

    def tearDown(self):
        V.hid, V.hidlist, V.time = self._saved

    def poll(self, entries_, cols):
        V.hid = types.SimpleNamespace(device=fake_device_class(cols))
        V.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries_))
        return self.provider.poll()

    def cols(self, entries_, **kw):
        return {e["path"]: FakeCollection(**kw) for e in entries_}

    def test_dongle_is_read(self):
        entries_ = entries()
        res = self.poll(entries_, self.cols(entries_))
        self.assertEqual(
            [(r.key, r.name, r.level, r.charging, r.online, r.source, r.kind) for r in res],
            [("vaxee", "VAXEE mouse", 85, False, True, "vaxee", "mouse")])

    def test_charging_and_five_percent_steps(self):
        entries_ = entries()
        res = self.poll(entries_, self.cols(entries_, battery=20, charging=7))
        self.assertEqual([(r.level, r.charging) for r in res], [(100, True)])

    def test_battery_boundaries(self):
        entries_ = entries()
        for raw, percent in ((0, 0), (20, 100)):
            with self.subTest(raw=raw):
                res = self.poll(entries_, self.cols(entries_, battery=raw))
                self.assertEqual([(r.level, r.online) for r in res], [(percent, True)])

    def test_invalid_battery_is_refused_without_a_cached_reading(self):
        entries_ = entries()
        for raw in (21, 255):
            with self.subTest(raw=raw):
                self.assertEqual(self.poll(entries_, self.cols(entries_, battery=raw)), [])
                self.assertTrue(any("invalid battery value" in line
                                    for line in self.provider.diagnostics()))

    def test_invalid_battery_keeps_only_the_valid_cache_then_recovers(self):
        entries_ = entries()
        cols = self.cols(entries_)
        self.poll(entries_, cols)
        for c in cols.values():
            c.battery = 255
        self.now[0] += 30
        res = self.poll(entries_, cols)
        self.assertEqual([(r.level, r.online) for r in res], [(85, False)])
        self.now[0] += V.ASLEEP_KEEP
        self.assertEqual(self.poll(entries_, cols), [])
        for c in cols.values():
            c.battery = 0
        res = self.poll(entries_, cols)
        self.assertEqual([(r.level, r.online) for r in res], [(0, True)])

    def test_request_frame(self):
        entries_ = entries()
        cols = self.cols(entries_)
        self.poll(entries_, cols)
        sent = cols[entries_[0]["path"]].sent
        self.assertEqual(sent[0], [0x0E, 0xA5, V.CMD_BATTERY, 0x01, 0x01] + [0x00] * 59)
        self.assertEqual(sent[1], [0x0E, 0xA5, V.CMD_CHARGING, 0x01, 0x01] + [0x00] * 59)

    def test_cable_is_asked_first_and_wins(self):
        dongle = entries()
        cable = entries(pid=0x1008, product="VAXEE XE-S", prefix=b"cable")
        cols = {**self.cols(dongle, battery=14, charging=0),
                **self.cols(cable, battery=15, charging=1)}
        res = self.poll(dongle + cable, cols)
        self.assertEqual([(r.level, r.charging) for r in res], [(75, True)])
        self.assertFalse(cols[dongle[0]["path"]].sent)
        self.assertEqual(res[0].name, "VAXEE XE-S")

    def test_a_charging_dongle_reading_beats_an_idle_cable_one(self):
        dongle = entries()
        cable = entries(pid=0x1008, product="VAXEE XE-S", prefix=b"cable")
        cols = {**self.cols(dongle, battery=14, charging=1),
                **self.cols(cable, battery=15, charging=0)}
        res = self.poll(dongle + cable, cols)
        self.assertEqual([(r.level, r.charging, r.name) for r in res],
                         [(70, True, "VAXEE mouse")])

    def test_sleeping_mouse_keeps_its_last_reading_greyed(self):
        entries_ = entries()
        cols = self.cols(entries_)
        self.poll(entries_, cols)
        for c in cols.values():
            c.answer = False
        self.now[0] += 30
        res = self.poll(entries_, cols)
        self.assertEqual([(r.level, r.online) for r in res], [(85, False)])

    def test_greyed_reading_expires_after_asleep_keep(self):
        entries_ = entries()
        cols = self.cols(entries_)
        self.poll(entries_, cols)
        for c in cols.values():
            c.answer = False
        self.now[0] += V.ASLEEP_KEEP + 1
        self.assertEqual(self.poll(entries_, cols), [])

    def test_removed_receiver_drops_the_last_reading(self):
        entries_ = entries()
        cols = self.cols(entries_)
        self.poll(entries_, cols)
        self.assertEqual(self.poll([], {}), [])
        for c in cols.values():
            c.answer = False
        self.assertEqual(self.poll(entries_, cols), [])

    def test_other_usage_pages_are_ignored(self):
        entries_ = entries(page=0xFF02)
        cols = self.cols(entries_)
        self.assertEqual(self.poll(entries_, cols), [])
        self.assertEqual(cols[entries_[0]["path"]].opened, 0)

    def test_wrong_command_echo_is_refused(self):
        self.assertIsNone(V.parse_reply(reply(V.CMD_CHARGING, 1), V.CMD_BATTERY))
        self.assertEqual(V.parse_reply(reply(V.CMD_BATTERY, 9), V.CMD_BATTERY), 9)

    def test_name_is_not_prefixed_twice(self):
        self.assertEqual(V.display_name("VAXEE XE-S"), "VAXEE XE-S")
        self.assertEqual(V.display_name(""), "VAXEE mouse")

    def test_a_dongle_part_number_is_not_shown_as_the_model(self):
        self.assertEqual(V.display_name("VXD02 4K Dongle", is_dongle=True),
                         "VAXEE mouse")
        self.assertEqual(V.display_name("VAXEE XE-S", is_dongle=False),
                         "VAXEE XE-S")


if __name__ == "__main__":
    unittest.main()
