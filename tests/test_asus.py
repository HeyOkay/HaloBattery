"""Tests for providers/asus.py. No hardware is needed.

The fake mouse answers the battery command 12 07 the way G-Helper's AsusMouse.cs
reads it: the echo, the battery in byte 5 and charging in byte 10 of the report
(bytes 4 and 9 once hidapi leaves out report id 0). The Harpe II Ace asks the same on
its receiver's mi_02&col03 with report id 3; keyboards on the OMNI receiver are found in
its pair list (01 a0) and answer 02 12 01 with the battery in byte 6, charging in byte 9.

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


OMNI_PATH = b"\\\\?\\HID#VID_0B05&PID_1ACE&MI_%s#8&1a2b3c4d&0&0001#{4d1e55b2-f16f-11cf-88cb-001111000030}"
HARPE_PATH = b"\\\\?\\HID#VID_0B05&PID_1AD0&MI_%s#8&55aa66bb&0&0002#{4d1e55b2-f16f-11cf-88cb-001111000030}"


def kb_reply(level, charging=0):
    return [0x02, 0x12, 0x01, 0, 0, 0, level, 0x03, 0, charging] + [0] * 54


def pair_list(*pids):
    r = [0x01, 0xA0, 0x00, 0x00, 0x00]
    for pid in pids:
        r += [pid & 0xFF, pid >> 8, 0x00, 0x00]
    return r + [0] * (64 - len(r))


class Scripted:
    """A collection that answers a request (matched on its first bytes) with replies."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.writes, self.queue, self.opened = [], [], 0
        self.nonblocking = False

    def on_write(self, data):
        self.writes.append(list(data))
        for head, replies in self.answers.items():
            if tuple(data[:len(head)]) == head:
                self.queue += [list(x) for x in replies]

    def on_read(self):
        return self.queue.pop(0) if self.queue else []


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
        for pid, model in A.MODELS.items():
            self.assertIn(model.scale, (A.PERCENT, A.STEPS), hex(pid))
            self.assertIn(model.length, (64, 65), hex(pid))

    def test_report_id_3(self):
        # Harpe II Ace on its receiver: hidapi keeps report id 3 in front
        r = [0x03, 0x12, 0x07, 0, 0, 66, 0, 0, 0, 0, 1] + [0] * 53
        self.assertEqual(A.parse_reply(r, A.PERCENT, 0x03), (66, True, ""))
        self.assertIsNone(A.parse_reply(r, A.PERCENT, 0x00))
        self.assertTrue(A.is_empty([0x03] + [0] * 63, 0x03))
        self.assertTrue(A.is_error([0x03, 0xFF, 0xAA] + [0] * 61, 0x03))

    def test_keyboard_reply(self):
        self.assertEqual(A.parse_keyboard_reply(kb_reply(72)), (72, False, ""))
        self.assertEqual(A.parse_keyboard_reply(kb_reply(15, 1)), (15, True, ""))
        self.assertIsNone(A.parse_keyboard_reply(kb_reply(0)))
        self.assertIsNone(A.parse_keyboard_reply(kb_reply(101)))
        self.assertIsNone(A.parse_keyboard_reply(reply(50)))           # a mouse reply

    def test_pair_list(self):
        self.assertEqual(A.parse_pair_list(pair_list(0x1B7A, 0x1B65)), [0x1B7A, 0x1B65])
        self.assertEqual(A.parse_pair_list(pair_list()), [])

    def test_windows_paths(self):
        d = {"path": OMNI_PATH % b"02&col02"}
        self.assertEqual(A.collection_of(d), "mi_02&col02")
        self.assertEqual(A.receiver_instance(d), A.receiver_instance({"path": OMNI_PATH % b"02&col01"}))
        self.assertNotEqual(A.receiver_instance(d),
                            A.receiver_instance({"path": OMNI_PATH.replace(b"&1a2b", b"&9f9f") % b"02&col02"}))


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
        e = [dict(issue_81_entries()[0], product_id=0x1ACE)]    # OMNI receiver: not included
        mouse = FakeMouse()
        self.assertEqual(self.poll(e, {b"if0-ff01": mouse}), [])
        self.assertEqual(mouse.opened, 0)


def harpe_entries(pid=0x1AD0, product="ROG HARPE II ACE"):
    # collections of the receiver as Windows lists them; only col03 is the battery channel
    out = []
    for col, page in ((b"00", 0x0001), (b"02&col01", 0x0001), (b"02&col02", 0xFF00),
                      (b"02&col03", 0xFF01)):
        ifn = 0 if col == b"00" else 2
        out.append({"product_id": pid, "interface_number": ifn, "usage_page": page, "usage": 1,
                    "path": HARPE_PATH % col, "product_string": product})
    return out


def omni_entries(path=OMNI_PATH):
    out = []
    for col, page in ((b"00", 0x0001), (b"02&col01", 0xFF00), (b"02&col02", 0xFF00),
                      (b"02&col03", 0xFF01)):
        out.append({"product_id": 0x1ACE, "interface_number": 0 if col == b"00" else 2,
                    "usage_page": page, "usage": 1, "path": path % col,
                    "product_string": "ROG OMNI RECEIVER"})
    return out


def real(hexs):
    b = [int(x, 16) for x in hexs.split()]
    return b + [0] * (64 - len(b))


class RealRepliesTest(unittest.TestCase):
    """Replies from a diagnostics report: Harpe II Ace on its SPEEDNOVA 8K receiver
    (1AD0) and a Strix Scope II 96 on the OMNI receiver (1ACE), both at 53 %, the
    keyboard on its cable. Bytes 8-9 (mouse) and 12-13 (keyboard) look like the cell
    voltage in mV (3837 and 3974): two different cells, so 53 % twice is a coincidence."""

    def test_harpe_ii_ace(self):
        r = real("03 12 07 00 00 35 02 14 fd 0e 00 00 01 00 00 00")
        self.assertEqual(A.parse_reply(r, A.PERCENT, 0x03), (53, False, ""))

    def test_omni_pair_list(self):
        self.assertEqual(A.parse_pair_list(real("01 a0 00 01 00 b0 1a 02 04 00 00 00")), [0x1AB0])

    def test_strix_scope_ii_96(self):
        r = real("02 12 01 00 00 00 35 02 01 01 14 35 86 0f 00 00")
        self.assertEqual(A.parse_keyboard_reply(r), (53, True, ""))


class HarpeIITest(ProviderTest):
    def harpe(self, level=66, charging=0):
        r = [0x03, 0x12, 0x07, 0, 0, level, 0, 0, 0, 0, charging] + [0] * 53
        return Scripted({(0x03, 0x12, 0x07): [[0x03, 0x05, 0x01, 0x00], r]})

    def test_receiver_reads_col03_with_report_id_3(self):
        devs = {e["path"]: Scripted() for e in harpe_entries()}
        devs[HARPE_PATH % b"02&col03"] = mouse = self.harpe(66)
        res = self.poll(harpe_entries(), devs)
        self.assertEqual([(r.name, r.level, r.charging, r.kind) for r in res],
                         [("ROG Harpe II Ace", 66, False, "mouse")])
        self.assertEqual(mouse.writes[0][:3], [0x03, 0x12, 0x07])
        self.assertEqual(len(mouse.writes[0]), 64)
        self.assertEqual([m.opened for p, m in devs.items() if m is not mouse], [0, 0, 0])

    def test_extreme_on_the_same_receiver(self):
        entries = harpe_entries(product="ROG HARPE II EXTREME")
        res = self.poll(entries, {HARPE_PATH % b"02&col03": self.harpe(40, 1)})
        self.assertEqual([(r.name, r.level, r.charging) for r in res],
                         [("ROG Harpe II Extreme", 40, True)])

    def test_mouse_off(self):
        off = Scripted({(0x03, 0x12, 0x07): [[0x03] + [0] * 63]})
        self.assertEqual(self.poll(harpe_entries(), {HARPE_PATH % b"02&col03": off}), [])
        self.assertEqual(len(off.writes), 1)

    def test_cable(self):
        e = [dict(issue_81_entries()[0], product_id=0x1C69)]
        mouse = FakeMouse(level=90, charging=1)
        res = self.poll(e, {b"if0-ff01": mouse})
        self.assertEqual([(r.name, r.level, r.charging) for r in res],
                         [("ROG Harpe II Ace", 90, True)])
        self.assertEqual(len(mouse.writes[0]), 64)


class OmniKeyboardTest(ProviderTest):
    def receiver(self, paired, kb_answers, path=OMNI_PATH):
        devs = {e["path"]: Scripted() for e in omni_entries(path)}
        devs[path % b"02&col01"] = Scripted({(0x01, 0xA0): [pair_list(*paired)]})
        devs[path % b"02&col02"] = Scripted({(0x02, 0x12, 0x01): kb_answers})
        return devs

    def test_strix_scope_ii_96_rx(self):
        devs = self.receiver([0x1B7A], [[0x02, 0x70, 0x01, 0x00], kb_reply(72)])
        res = self.poll(omni_entries(), devs)
        self.assertEqual([(r.name, r.level, r.charging, r.kind, r.key) for r in res],
                         [("ROG Strix Scope II 96 RX", 72, False, "keyboard",
                           "asus:rog-strix-scope-ii-96-rx")])
        lst, kb = devs[OMNI_PATH % b"02&col01"], devs[OMNI_PATH % b"02&col02"]
        self.assertEqual(lst.writes[0][:4], [0x01, 0xA0, 0x00, 0x00])
        self.assertEqual(kb.writes[0][:3], [0x02, 0x12, 0x01])
        self.assertEqual({len(lst.writes[0]), len(kb.writes[0])}, {64})
        self.assertEqual(devs[OMNI_PATH % b"02&col03"].opened, 0)     # the mouse channel
        self.assertEqual(devs[OMNI_PATH % b"00"].opened, 0)

    def test_charging(self):
        res = self.poll(omni_entries(), self.receiver([0x1AB0], [kb_reply(30, 1)]))
        self.assertEqual([(r.name, r.level, r.charging) for r in res],
                         [("ROG Strix Scope II 96", 30, True)])

    def test_no_known_keyboard_paired(self):
        devs = self.receiver([0x1B65], [kb_reply(72)])                # a mouse only
        self.assertEqual(self.poll(omni_entries(), devs), [])
        self.assertEqual(devs[OMNI_PATH % b"02&col02"].opened, 0)

    def test_keyboard_off(self):
        for answer in ([0x02] + [0] * 63, [0x02, 0xFF, 0xAA] + [0] * 61, kb_reply(0)):
            self.assertEqual(self.poll(omni_entries(), self.receiver([0x1B7A], [answer])), [])
        devs = self.receiver([0x1B7A], [])                             # silence
        self.assertEqual(self.poll(omni_entries(), devs), [])
        self.assertEqual(len(devs[OMNI_PATH % b"02&col02"].writes), A.WRITE_ATTEMPTS)

    def test_two_receivers_use_their_own_channel(self):
        other = OMNI_PATH.replace(b"&1a2b3c4d", b"&9f9f9f9f")
        devs = self.receiver([0x1B7A], [kb_reply(72)])
        devs.update(self.receiver([], [kb_reply(11)], other))
        res = self.poll(omni_entries() + omni_entries(other), devs)
        self.assertEqual([(r.name, r.level) for r in res], [("ROG Strix Scope II 96 RX", 72)])
        self.assertEqual(devs[other % b"02&col02"].opened, 0)

    def test_receiver_and_harpe_ii_together(self):
        # the setup of the request: keyboard on the OMNI, Harpe II Ace on its own receiver
        devs = self.receiver([0x1B7A], [kb_reply(72)])
        r = [0x03, 0x12, 0x07, 0, 0, 55, 0, 0, 0, 0, 0] + [0] * 53
        devs[HARPE_PATH % b"02&col03"] = Scripted({(0x03, 0x12, 0x07): [r]})
        res = self.poll(harpe_entries() + omni_entries(), devs)
        self.assertEqual(sorted((r.name, r.level, r.kind) for r in res),
                         [("ROG Harpe II Ace", 55, "mouse"),
                          ("ROG Strix Scope II 96 RX", 72, "keyboard")])


if __name__ == "__main__":
    unittest.main()
