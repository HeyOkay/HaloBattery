"""Tests for providers/asus.py. No hardware is needed.

The fake mouse answers the battery command 12 07 the way G-Helper's AsusMouse.cs
reads it: the echo, the battery in byte 5 and charging in byte 10 of the report
(bytes 4 and 9 once hidapi leaves out report id 0).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import asus as A  # noqa: E402


def reply(level, charging=0, report_id=False):
    r = [0x12, 0x07, 0x00, 0x00, level, 0x00, 0x00, 0x00, 0x00, charging] + [0] * 54
    return ([0x00] + r) if report_id else r


class FakeMouse:
    """mode: "answer", "error" (ff aa), "zeros", "silent", "events" (button reports first)."""

    def __init__(self, level=87, charging=0, mode="answer", report_id=False, answer_on=1):
        self.level, self.charging, self.mode = level, charging, mode
        self.report_id = report_id
        self.answer_on = answer_on      # answer the n-th request only
        self.writes = []
        self.queue = [[0x12, 0x01, 0x00, 0x04]]      # a stale button report, to be drained
        self.nonblocking = False
        self.opened = 0

    def on_write(self, data):
        self.writes.append(list(data))
        if list(data[:3]) != A.REQUEST or len(self.writes) < self.answer_on:
            return
        if self.mode == "answer":
            self.queue.append(reply(self.level, self.charging, self.report_id))
        elif self.mode == "events":
            self.queue += [[0x12, 0x01, 0, 0], [0x12, 0x00, 0x00, 0x00, 0x02],
                           reply(self.level, self.charging)]
        elif self.mode == "error":
            self.queue.append([0xFF, 0xAA] + [0] * 62)
        elif self.mode == "zeros":
            self.queue.append([0] * 64)

    def on_read(self):
        return self.queue.pop(0) if self.queue else []


class FakeBus:
    def __init__(self, mice):
        self.mice = mice

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                self.m = bus.mice[path]
                self.m.opened += 1

            def set_nonblocking(self, on):
                self.m.nonblocking = bool(on)

            def write(self, data):
                self.m.on_write(data)
                return len(data)

            def read(self, n, timeout=None):
                return self.m.on_read()

            def close(self):
                pass

        return FakeDevice


def issue_81_entries(pid=0x1A72):
    # the collections of the Gladius III Wireless AimPoint in issue #81
    return [
        {"product_id": pid, "interface_number": 0, "usage_page": 0xFF01, "usage": 1,
         "path": b"if0-ff01", "product_string": "ROG GIII WIRELESS AIMPOINT"},
        {"product_id": pid, "interface_number": 2, "usage_page": 0xFFC1, "usage": 1,
         "path": b"if2-ffc1", "product_string": "ROG GIII WIRELESS AIMPOINT"},
        {"product_id": pid, "interface_number": 2, "usage_page": 0x0001, "usage": 6,
         "path": b"if2-kbd", "product_string": "ROG GIII WIRELESS AIMPOINT"},
    ]


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (A.hid, A.hidlist)

    def tearDown(self):
        A.hid, A.hidlist = self._saved

    def poll(self, entries, mice):
        A.hid = types.SimpleNamespace(device=FakeBus(mice).device_class())
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return A.AsusProvider().poll()


class ParseTest(unittest.TestCase):
    def test_percent(self):
        self.assertEqual(A.parse_reply(reply(87), A.PERCENT), (87, False, ""))
        self.assertEqual(A.parse_reply(reply(40, 1), A.PERCENT), (40, True, ""))

    def test_report_id_in_front(self):
        self.assertEqual(A.parse_reply(reply(87, report_id=True), A.PERCENT), (87, False, ""))

    def test_steps(self):
        self.assertEqual(A.parse_reply(reply(3), A.STEPS), (75, False, "about 75%"))
        self.assertEqual(A.parse_reply(reply(4, 1), A.STEPS), (100, True, "about 100%, charging"))
        self.assertIsNone(A.parse_reply(reply(5), A.STEPS))

    def test_standby_is_not_empty(self):
        # G-Helper: battery 0 without charging = the mouse is in standby
        self.assertIsNone(A.parse_reply(reply(0), A.PERCENT))
        self.assertEqual(A.parse_reply(reply(0, 1), A.PERCENT), (0, True, ""))

    def test_other_reports_are_not_levels(self):
        self.assertIsNone(A.parse_reply([0x12, 0x01, 0, 0, 55, 0, 0, 0, 0, 0], A.PERCENT))
        self.assertIsNone(A.parse_reply([0xFF, 0xAA] + [0] * 20, A.PERCENT))
        self.assertIsNone(A.parse_reply(reply(101), A.PERCENT))
        self.assertIsNone(A.parse_reply([0x12, 0x07, 0, 0], A.PERCENT))     # too short

    def test_every_model_has_a_known_scale(self):
        for pid, (name, scale) in A.KNOWN.items():
            self.assertIn(scale, (A.PERCENT, A.STEPS), hex(pid))


class PollTest(ProviderTest):
    def test_gladius_iii_aimpoint_issue_81(self):
        mouse = FakeMouse(level=87)
        res = self.poll(issue_81_entries(), {b"if0-ff01": mouse})
        self.assertEqual([(r.name, r.level, r.charging, r.kind, r.source) for r in res],
                         [("ROG Gladius III Aimpoint", 87, False, "mouse", "asus")])
        self.assertEqual(len(mouse.writes[0]), 65)
        self.assertEqual(mouse.writes[0][:3], [0x00, 0x12, 0x07])
        self.assertEqual(set(mouse.writes[0][3:]), {0})
        self.assertEqual(len(mouse.writes), 1)

    def test_only_the_vendor_collection_of_interface_0_is_opened(self):
        # a mouse collection on interface 0 as well: it must not get the request
        entries = [dict(issue_81_entries()[0], usage_page=0x0001, usage=2, path=b"if0-mouse")]
        entries += issue_81_entries()
        mice = {p: FakeMouse() for p in (b"if0-mouse", b"if0-ff01", b"if2-ffc1", b"if2-kbd")}
        self.poll(entries, mice)
        self.assertEqual({p: m.opened for p, m in mice.items()},
                         {b"if0-mouse": 0, b"if0-ff01": 1, b"if2-ffc1": 0, b"if2-kbd": 0})

    def test_button_and_profile_reports_are_skipped(self):
        res = self.poll(issue_81_entries(), {b"if0-ff01": FakeMouse(level=64, mode="events")})
        self.assertEqual([r.level for r in res], [64])

    def test_error_zeros_and_silence_give_no_icon(self):
        for mode in ("error", "zeros", "silent"):
            mouse = FakeMouse(mode=mode)
            self.assertEqual(self.poll(issue_81_entries(), {b"if0-ff01": mouse}), [], mode)
            self.assertLessEqual(len(mouse.writes), A.WRITE_ATTEMPTS, mode)

    def test_error_and_zeros_are_final_answers(self):
        # "ff aa" (command not known) and all zeros (asleep) are not repeated
        for mode in ("error", "zeros"):
            mouse = FakeMouse(mode=mode)
            self.poll(issue_81_entries(), {b"if0-ff01": mouse})
            self.assertEqual(len(mouse.writes), 1, mode)

    def test_second_request_is_answered(self):
        mouse = FakeMouse(level=50, answer_on=2)
        res = self.poll(issue_81_entries(), {b"if0-ff01": mouse})
        self.assertEqual([r.level for r in res], [50])
        self.assertEqual(len(mouse.writes), 2)

    def test_cable_and_receiver_share_one_icon(self):
        rx = issue_81_entries(0x1A72)[0]
        cable = dict(issue_81_entries(0x1A70)[0], path=b"cable")
        for order in ([rx, cable], [cable, rx]):          # the charging reading wins either way
            res = self.poll(order, {b"if0-ff01": FakeMouse(level=80),
                                    b"cable": FakeMouse(level=81, charging=1)})
            self.assertEqual([(r.level, r.charging) for r in res], [(81, True)])

    def test_older_model_shows_an_approximate_level(self):
        e = [dict(issue_81_entries()[0], product_id=0x1960)]    # ROG Keris Wireless
        res = self.poll(e, {b"if0-ff01": FakeMouse(level=2)})
        self.assertEqual([(r.name, r.level, r.approx) for r in res],
                         [("ROG Keris Wireless", 50, "about 50%")])

    def test_unknown_asus_devices_are_not_opened(self):
        e = [dict(issue_81_entries()[0], product_id=0x1ACF)]
        mouse = FakeMouse()
        self.assertEqual(self.poll(e, {b"if0-ff01": mouse}), [])
        self.assertEqual(mouse.opened, 0)

    def test_omni_receiver_interface_0_is_not_opened(self):
        # the OMNI receiver's interface 0 has no 12 07 battery: only interface 2 is used
        e = [dict(issue_81_entries()[0], product_id=A.OMNI_PID)]
        mouse = FakeMouse()
        self.assertEqual(self.poll(e, {b"if0-ff01": mouse}), [])
        self.assertEqual(mouse.opened, 0)


# --- ROG OMNI receiver -------------------------------------------------------------------

# replies read from a ROG OMNI receiver with a Falchion RX Low Profile (1b06) and a Harpe
# Ace Mini (1b65) paired: the keyboard at 66 %, the mouse at 55 %, neither charging
OMNI_PAIRS_REPLY = [0x01, 0xA0, 0x00, 0x02, 0x00, 0x06, 0x1B, 0x02, 0x04,
                    0x65, 0x1B, 0x03, 0x05] + [0] * 51
OMNI_MOUSE_REPLY = [0x03, 0x12, 0x07, 0x00, 0x00, 0x37, 0x02, 0x0A, 0xED, 0x0E,
                    0x00, 0x00, 0x01] + [0] * 51
OMNI_KEYBOARD_REPLY = [0x02, 0x12, 0x01, 0x00, 0x00, 0x00, 0x42, 0x02, 0x01, 0x00,
                       0x14, 0x42, 0x27, 0x0F] + [0] * 50


def omni_mouse_reply(level, charging=0):
    r = list(OMNI_MOUSE_REPLY)
    r[5], r[10] = level, charging
    return r


def omni_keyboard_reply(level, charging=0, gauge=None):
    r = list(OMNI_KEYBOARD_REPLY)
    r[6] = level if gauge is None else gauge
    r[11], r[9] = level, charging
    return r


class FakeCollection:
    """Answers `request` (its first three bytes) with `replies`, one per request."""

    def __init__(self, request, *replies, events=()):
        self.request, self.replies, self.events = list(request[:3]), list(replies), list(events)
        self.writes, self.queue, self.opened = [], [], 0
        self.nonblocking = False

    def on_write(self, data):
        self.writes.append(list(data))
        if list(data[:3]) == self.request and self.replies:
            self.queue += self.events + [self.replies.pop(0)]

    def on_read(self):
        return self.queue.pop(0) if self.queue else []


def omni_entries(instance="7&1111111&0"):
    # the collections of one OMNI receiver: interface 0 is the keyboard, the vendor
    # collections are on interface 2
    def e(iface, col, page, usage):
        path = f"\\\\?\\hid#vid_0b05&pid_1ace&mi_0{iface}&col0{col}#{instance}&000{col - 1}#{{4d1e}}"
        return {"product_id": A.OMNI_PID, "interface_number": iface, "usage_page": page,
                "usage": usage, "path": path.encode(), "product_string": "ROG OMNI RECEIVER"}
    return [e(0, 1, 0x0001, 6), e(2, 1, 0xFF02, 1), e(2, 2, 0xFF00, 1), e(2, 3, 0xFF01, 1)]


def omni_bus(entries, pairs=OMNI_PAIRS_REPLY, mouse=OMNI_MOUSE_REPLY,
             keyboard=OMNI_KEYBOARD_REPLY):
    by_page = {d["usage_page"]: d["path"] for d in entries if d["interface_number"] == 2}
    return {
        by_page[0xFF02]: FakeCollection(A.OMNI_PAIRS, pairs),
        by_page[0xFF01]: FakeCollection(A.OMNI_MOUSE_REQUEST, mouse),
        by_page[0xFF00]: FakeCollection(A.OMNI_KEYBOARD_REQUEST, keyboard),
        [d["path"] for d in entries if d["interface_number"] == 0][0]: FakeCollection([0, 0, 0]),
    }


class OmniParseTest(unittest.TestCase):
    def test_pair_list(self):
        self.assertEqual(A.parse_pairs(OMNI_PAIRS_REPLY), [0x1B06, 0x1B65])
        self.assertEqual(A.parse_pairs([0x01, 0xA0] + [0] * 62), [])
        self.assertEqual(A.parse_pairs([0x03, 0x12, 0x07] + [0] * 61), [])

    def test_mouse(self):
        self.assertEqual(A.parse_omni_mouse(OMNI_MOUSE_REPLY), (55, False, ""))
        self.assertEqual(A.parse_omni_mouse(omni_mouse_reply(20, 1)), (20, True, ""))
        self.assertIsNone(A.parse_omni_mouse(omni_mouse_reply(0)))          # standby
        self.assertIsNone(A.parse_omni_mouse(OMNI_KEYBOARD_REPLY))

    def test_keyboard(self):
        self.assertEqual(A.parse_omni_keyboard(OMNI_KEYBOARD_REPLY, 11), (66, False, ""))
        self.assertEqual(A.parse_omni_keyboard(OMNI_KEYBOARD_REPLY, 6), (66, False, ""))
        self.assertEqual(A.parse_omni_keyboard(omni_keyboard_reply(30, 1), 6), (30, True, ""))
        self.assertIsNone(A.parse_omni_keyboard(omni_keyboard_reply(0), 6))   # standby
        self.assertIsNone(A.parse_omni_keyboard(omni_keyboard_reply(101), 6))
        self.assertIsNone(A.parse_omni_keyboard(OMNI_MOUSE_REPLY, 6))

    def test_falchion_reads_the_percentage_not_the_gauge(self):
        r = omni_keyboard_reply(73, gauge=7)
        self.assertEqual(A.parse_omni_keyboard(r, A.OMNI_KEYBOARDS[0x1B06][1]), (73, False, ""))
        self.assertEqual(A.parse_omni_keyboard(r, A.OMNI_KEYBOARDS[0x1A85][1]), (7, False, ""))

    def test_instance(self):
        a, b = omni_entries()[1]["path"], omni_entries()[3]["path"]
        self.assertEqual(A._instance(a), A._instance(b))
        self.assertNotEqual(A._instance(a), A._instance(omni_entries("7&2222222&0")[1]["path"]))


class OmniPollTest(ProviderTest):
    def test_mouse_and_keyboard(self):
        entries = omni_entries()
        bus = omni_bus(entries)
        res = self.poll(entries, bus)
        self.assertEqual(sorted((r.name, r.level, r.charging, r.kind, r.key) for r in res), [
            ("ROG Falchion RX Low Profile", 66, False, "keyboard", "asus:rog-falchion-rx-low-profile"),
            ("ROG Harpe Ace Mini", 55, False, "mouse", "asus:rog-harpe-ace-mini"),
        ])
        writes = {c.request[0]: c.writes for c in bus.values() if c.writes}
        self.assertEqual(sorted(writes), [0x01, 0x02, 0x03])
        for request in (A.OMNI_PAIRS, A.OMNI_MOUSE_REQUEST, A.OMNI_KEYBOARD_REQUEST):
            w = writes[request[0]]
            self.assertEqual(len(w), 1)
            self.assertEqual(len(w[0]), 64)
            self.assertEqual(w[0][:len(request)], request)
            self.assertEqual(set(w[0][len(request):]), {0})

    def test_interface_0_is_not_opened(self):
        entries = omni_entries()
        bus = omni_bus(entries)
        self.poll(entries, bus)
        self.assertEqual(bus[entries[0]["path"]].opened, 0)

    def test_only_paired_devices_are_asked(self):
        entries = omni_entries()
        pairs = OMNI_PAIRS_REPLY[:9] + [0] * 55                   # the keyboard only
        bus = omni_bus(entries, pairs=pairs)
        res = self.poll(entries, bus)
        self.assertEqual([(r.name, r.kind) for r in res], [("ROG Falchion RX Low Profile", "keyboard")])
        mouse = next(c for c in bus.values() if c.request[0] == 0x03)
        self.assertEqual(mouse.opened, 0)

    def test_unknown_paired_pids_are_skipped(self):
        entries = omni_entries()
        pairs = [0x01, 0xA0, 0, 2, 0, 0x34, 0x12, 2, 4] + [0] * 55
        bus = omni_bus(entries, pairs=pairs)
        self.assertEqual(self.poll(entries, bus), [])
        self.assertEqual(sum(c.opened for c in bus.values()), 1)        # the pair list only

    def test_no_pair_list_reply_asks_nothing_else(self):
        entries = omni_entries()
        bus = omni_bus(entries, pairs=[0x01, 0xFF, 0xAA] + [0] * 61)
        self.assertEqual(self.poll(entries, bus), [])
        self.assertEqual(sum(c.opened for c in bus.values()), 1)

    def test_sleeping_devices_give_no_icon(self):
        entries = omni_entries()
        bus = omni_bus(entries, mouse=[0x03] + [0] * 63, keyboard=omni_keyboard_reply(0))
        self.assertEqual(self.poll(entries, bus), [])

    def test_events_before_the_reply_are_skipped(self):
        entries = omni_entries()
        bus = omni_bus(entries)
        mouse = next(c for c in bus.values() if c.request[0] == 0x03)
        mouse.events = [[0x03, 0x12, 0x01, 0, 0, 0x02] + [0] * 58]
        res = self.poll(entries, bus)
        self.assertIn(("ROG Harpe Ace Mini", 55), [(r.name, r.level) for r in res])

    def test_two_receivers_do_not_mix(self):
        first, second = omni_entries("7&1111111&0"), omni_entries("7&2222222&0")
        bus = omni_bus(first, pairs=OMNI_PAIRS_REPLY[:9] + [0] * 55)    # keyboard only
        bus.update(omni_bus(second, pairs=[0x01, 0xA0, 0, 2, 0, 0x65, 0x1B, 3, 5] + [0] * 55,
                            mouse=omni_mouse_reply(40)))                 # mouse only
        res = self.poll(first + second, bus)
        self.assertEqual(sorted((r.name, r.level) for r in res),
                         [("ROG Falchion RX Low Profile", 66), ("ROG Harpe Ace Mini", 40)])

    def test_omni_and_cable_share_one_icon(self):
        # a Gladius III Aimpoint on the OMNI receiver and on its own cable
        entries = omni_entries()
        bus = omni_bus(entries, pairs=[0x01, 0xA0, 0, 2, 0, 0x72, 0x1A, 3, 5] + [0] * 55,
                       mouse=omni_mouse_reply(70))
        cable = dict(issue_81_entries(0x1A70)[0], path=b"cable")
        bus[b"cable"] = FakeMouse(level=71, charging=1)
        res = self.poll(entries + [cable], bus)
        self.assertEqual([(r.name, r.level, r.charging) for r in res],
                         [("ROG Gladius III Aimpoint", 71, True)])

    def test_every_omni_device_has_a_name(self):
        for pid, name in A.OMNI_MICE.items():
            self.assertTrue(name.startswith("ROG "), hex(pid))
        for pid, (name, byte) in A.OMNI_KEYBOARDS.items():
            self.assertIn(byte, (6, 11), hex(pid))


if __name__ == "__main__":
    unittest.main()
