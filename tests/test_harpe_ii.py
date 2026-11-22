"""Harpe II frames and collection selection from upstream PR #364.

Uses that contributor's captured receiver reply and the existing fake ASUS bus.
"""
from test_device_pr185_asus import (
    A, FakeCollection, FakeMouse, ProviderTest, issue_81_entries,
    omni_entries, omni_bus, omni_keyboard_reply,
)


class HarpeIITest(ProviderTest):
    def entries(self, product="ROG SPEEDNOVA 8K RECEIVER"):
        return [dict(d, product_id=0x1AD0, product_string=product)
                for d in omni_entries()]

    def receiver(self, level=53, charging=0):
        # Captured: 03 12 07 00 00 35 02 14 fd 0e 00 ... -> 53%, not charging.
        r = [3, 0x12, 7, 0, 0, level, 2, 0x14, 0xFD, 0x0E, charging] + [0] * 53
        return FakeCollection([3, 0x12, 7], r, events=([3, 5, 1, 0],))

    def run_receiver(self, mouse, product="ROG SPEEDNOVA 8K RECEIVER"):
        entries = self.entries(product)
        bus = {d["path"]: FakeCollection([]) for d in entries}
        target = next(d["path"] for d in entries if d["usage_page"] == A.OMNI_MOUSE)
        bus[target] = mouse
        return self.poll(entries, bus), bus

    def test_captured_receiver_reply_and_only_col03_is_written(self):
        mouse = self.receiver()
        out, bus = self.run_receiver(mouse)
        self.assertEqual([(s.name, s.level, s.charging) for s in out],
                         [("ROG Harpe II Ace", 53, False)])
        self.assertEqual(len(mouse.writes[0]), 64)
        self.assertEqual(mouse.writes[0][:3], [3, 0x12, 7])
        self.assertTrue(all(d.opened == 0 for d in bus.values() if d is not mouse))

    def test_extreme_name(self):
        out, _ = self.run_receiver(self.receiver(40, 1), "ROG HARPE II EXTREME")
        self.assertEqual([(s.name, s.level, s.charging) for s in out],
                         [("ROG Harpe II Extreme", 40, True)])

    def test_short_garbage_standby_and_out_of_range_replies(self):
        for r in ([3, 0x12], [3, 0xFF, 0xAA], [3] + [0] * 63,
                  [3, 0x12, 7, 0, 0, 101] + [0] * 58,
                  [3, 0x12, 7] + [0] * 61):
            with self.subTest(reply=r[:6]):
                out, _ = self.run_receiver(FakeCollection([3, 0x12, 7], r))
                self.assertEqual(out, [])

    def test_cable_and_receiver_share_icon_and_keep_charging(self):
        entries = self.entries()
        path = next(d["path"] for d in entries if d["usage_page"] == A.OMNI_MOUSE)
        cable = dict(issue_81_entries()[0], product_id=0x1C69, path=b"cable")
        for reverse in (False, True):
            cable_device = FakeMouse(level=54, charging=1)
            infos = entries + [cable]
            out = self.poll(list(reversed(infos)) if reverse else infos,
                            {path: self.receiver(), b"cable": cable_device})
            self.assertEqual([(s.level, s.charging) for s in out], [(54, True)])
            self.assertEqual(len(cable_device.writes[0]), 64)

    def test_read_exception_closes_device(self):
        from unittest.mock import MagicMock, patch
        dev = MagicMock()
        dev.read.side_effect = [[], OSError("receiver disconnected")]
        with patch.object(A.hid, "device", return_value=dev):
            provider = A.AsusProvider()
            self.assertIsNone(provider._ask(b"receiver", [3, 0x12, 7],
                                           A.is_omni_mouse_reply, 64))
        dev.close.assert_called_once()

    def test_omni_keyboard_and_harpe_receiver_read_independently(self):
        keyboard = omni_entries("7&2222222&0")
        pairs = [1, 0xA0, 0, 1, 0, 0xB0, 0x1A, 2, 4] + [0] * 55
        bus = omni_bus(keyboard, pairs=pairs, keyboard=omni_keyboard_reply(53, 1))
        mouse = self.entries()
        path = next(d["path"] for d in mouse if d["usage_page"] == A.OMNI_MOUSE)
        bus[path] = self.receiver()
        out = self.poll(mouse + keyboard, bus)
        self.assertEqual(sorted((s.kind, s.level, s.charging) for s in out),
                         [("keyboard", 53, True), ("mouse", 53, False)])
