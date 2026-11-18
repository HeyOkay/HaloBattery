"""Harpe II frames and collection selection from upstream PR #364.

Uses that contributor's captured receiver reply and the existing fake ASUS bus.
The generic SPEEDNOVA identity is shared with keyboards (#238), so polling must
hold the Harpe query until the paired model can be identified from a source.
"""
from test_device_pr185_asus import (
    A, FakeCollection, FakeMouse, ProviderTest, issue_81_entries,
    omni_entries, omni_bus, omni_keyboard_reply,
)


class HarpeIITest(ProviderTest):
    def entries(self, product="ROG SPEEDNOVA 8K RECEIVER"):
        return [dict(d, product_id=0x1AD0, product_string=product,
                     path=d["path"].replace(b"pid_1ace", b"pid_1ad0"))
                for d in omni_entries()]

    def receiver_reply(self, level=53, charging=0):
        # Captured: 03 12 07 00 00 35 02 14 fd 0e 00 ... -> 53%, not charging.
        return [3, 0x12, 7, 0, 0, level, 2, 0x14, 0xFD, 0x0E, charging] + [0] * 53

    def receiver(self, level=53, charging=0):
        return FakeCollection([3, 0x12, 7], self.receiver_reply(level, charging),
                              events=([3, 5, 1, 0],))

    def receiver_bus(self, entries, mouse):
        bus = {d["path"]: FakeCollection([]) for d in entries}
        control = next(d["path"] for d in entries if d["usage_page"] == 0xFF02)
        # Synthetic 64-byte pair reply with no known model; this is not evidence
        # for any Harpe paired PID. The pair-list query itself is sourced in #238.
        bus[control] = FakeCollection([1, 0xA0, 0], [1, 0xA0] + [0] * 62)
        target = next(d["path"] for d in entries if d["usage_page"] == 0xFF01)
        bus[target] = mouse
        return bus

    def run_receiver(self, mouse, product="ROG SPEEDNOVA 8K RECEIVER"):
        entries = self.entries(product)
        bus = self.receiver_bus(entries, mouse)
        return self.poll(entries, bus), bus

    def test_captured_receiver_reply_still_parses_as_53_percent(self):
        self.assertEqual(A.parse_omni_mouse(self.receiver_reply()), (53, False, ""))

    def test_generic_receiver_does_not_authorize_the_harpe_query(self):
        mouse = self.receiver()
        out, bus = self.run_receiver(mouse)
        self.assertEqual(out, [])
        self.assertEqual(mouse.opened, 0)
        self.assertEqual(mouse.writes, [])
        for device in bus.values():
            for request in device.writes:
                self.assertEqual(request, [1, 0xA0, 0, 0] + [0] * 60)

    def test_extreme_name_does_not_authorize_the_harpe_query(self):
        mouse = self.receiver(40, 1)
        out, _ = self.run_receiver(mouse, "ROG HARPE II EXTREME")
        self.assertEqual(out, [])
        self.assertEqual(mouse.opened, 0)
        self.assertEqual(mouse.writes, [])

    def test_short_garbage_standby_and_out_of_range_replies(self):
        for r in ([3, 0x12], [3, 0xFF, 0xAA], [3] + [0] * 63,
                  [3, 0x12, 7, 0, 0, 101] + [0] * 58,
                  [3, 0x12, 7] + [0] * 61):
            with self.subTest(reply=r[:6]):
                self.assertIsNone(A.parse_omni_mouse(r))

    def test_cable_and_receiver_share_icon_and_keep_charging(self):
        entries = self.entries()
        cable = dict(issue_81_entries()[0], product_id=0x1C69, path=b"cable")
        for reverse in (False, True):
            cable_device = FakeMouse(level=54, charging=1)
            receiver_device = self.receiver()
            bus = self.receiver_bus(entries, receiver_device)
            bus[b"cable"] = cable_device
            infos = entries + [cable]
            out = self.poll(list(reversed(infos)) if reverse else infos, bus)
            self.assertEqual([(s.level, s.charging) for s in out], [(54, True)])
            self.assertEqual(len(cable_device.writes[0]), 64)
            self.assertEqual(receiver_device.opened, 0)
            self.assertEqual(receiver_device.writes, [])

    def test_read_exception_closes_device(self):
        from unittest.mock import MagicMock, patch
        dev = MagicMock()
        dev.read.side_effect = [[], OSError("receiver disconnected")]
        with patch.object(A.hid, "device", return_value=dev):
            provider = A.AsusProvider()
            self.assertIsNone(provider._ask(b"receiver", [3, 0x12, 7],
                                           A.is_omni_mouse_reply, 64))
        dev.close.assert_called_once()

    def test_omni_keyboard_still_reads_with_an_ambiguous_harpe_receiver_present(self):
        keyboard = omni_entries("7&2222222&0")
        pairs = [1, 0xA0, 0, 1, 0, 0xB0, 0x1A, 2, 4] + [0] * 55
        bus = omni_bus(keyboard, pairs=pairs, keyboard=omni_keyboard_reply(53, 1))
        mouse = self.entries()
        receiver_device = self.receiver()
        bus.update(self.receiver_bus(mouse, receiver_device))
        out = self.poll(mouse + keyboard, bus)
        self.assertEqual(sorted((s.kind, s.level, s.charging) for s in out),
                         [("keyboard", 53, True)])
        self.assertEqual(receiver_device.opened, 0)
        self.assertEqual(receiver_device.writes, [])
