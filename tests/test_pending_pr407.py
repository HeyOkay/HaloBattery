"""PR #407 model/transport regressions with capture and synthetic fixtures."""
import types
import unittest
from unittest.mock import Mock, patch

from providers import atk_keyboard as A, inphic as I


VGN_PRODUCT = "VGN N75 PRO 2.4G Dongle"
# Synthetic existing ATK-layout response at PR #407's owner-reported VGN 59%.
# That report provides app logs, not a raw VGN capture; request IDs are replaced.
VGN_REPLY = bytes.fromhex("04 01 00 1a 00 00 00 00 3b 00") + bytes(54)
# Literal Delux announcements recorded in the PR's hardware session.
DELUX_REPLY = bytes.fromhex("03 3f 40 01 4a")


def keyboard_entry(**changes):
    return {"vendor_id": 0x320F, "product_id": 0x5088,
            "product_string": VGN_PRODUCT, "interface_number": 1,
            "usage_page": 0xFF1C, "usage": 0x92, "path": b"keyboard", **changes}


class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.activity = Mock()
        self.activity.start.return_value = self.activity.active.return_value = True
        self.provider = A.AtkKeyboardProvider(activity=self.activity)
        self.dev = Mock()
        self.answer = VGN_REPLY
        self.reply = []
        def write(request):
            self.reply = list(self.answer)
            if self.reply:
                self.reply[1:3] = request[1:3]
            return len(request)
        def read(*args):
            result, self.reply = self.reply, []
            return result
        self.dev.write.side_effect, self.dev.read.side_effect = write, read
        self.entries = [keyboard_entry()]
        self.clock = 0.0
        def tick():
            self.clock += .1
            return self.clock
        for context in (
            patch.object(A, "hid", types.SimpleNamespace(device=lambda: self.dev)),
            patch.object(A.hidlist, "enumerate", side_effect=lambda vid: self.entries),
            patch.object(A.time, "monotonic", side_effect=tick),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_vgn_synthetic_fixture_uses_existing_battery_command_and_activity_gate(self):
        status = self.provider.poll()[0]
        self.assertEqual((status.name, status.level, status.charging, status.online),
                         ("VGN N75 PRO", 59, False, True))
        self.dev.write.assert_called_once_with([4, 1, 0, 0x1A] + [0] * 28)
        self.dev.close.assert_called_once_with()

    def test_unknown_brand_or_vgn_on_cable_id_is_never_opened(self):
        for entry in (keyboard_entry(product_id=0x5055),
                      keyboard_entry(product_string="Unrelated keyboard"),
                      keyboard_entry(vendor_id=0x1234),
                      keyboard_entry(product_string=123),
                      keyboard_entry(usage_page=1, usage=6)):
            self.entries = [entry]
            self.assertEqual(self.provider.poll(), [])
        self.dev.open_path.assert_not_called()
        self.dev.write.assert_not_called()

    def test_idle_vgn_does_not_send_request(self):
        self.activity.active.return_value = False
        status = self.provider.poll()[0]
        self.assertEqual((status.name, status.level, status.polling_paused),
                         ("VGN N75 PRO", None, True))
        self.dev.write.assert_not_called()

    def test_vgn_wireless_then_atk_cable_resets_name_and_level(self):
        self.assertEqual(self.provider.poll()[0].name, "VGN N75 PRO")
        self.entries = [keyboard_entry(product_id=0x5055, product_string="ATK V75X",
                                       path=b"cable")]
        self.answer = bytes.fromhex("04 00 00 1a 00 00 00 00 50 01") + bytes(54)
        status = self.provider.poll()[0]
        self.assertEqual((status.name, status.level, status.charging), ("ATK V75X", 80, True))

    def test_failed_atk_cable_never_relabels_vgn_cached_level(self):
        self.provider.poll()
        self.entries = [keyboard_entry(product_id=0x5055, product_string="ATK V75X",
                                       path=b"cable")]
        self.answer = b""
        status = self.provider.poll()[0]
        self.assertEqual((status.name, status.level, status.online), ("ATK V75X", None, False))

    def test_brand_change_on_same_path_clears_cache_and_refresh_throttle(self):
        self.provider.poll()
        self.entries = [keyboard_entry(product_string=A.PRODUCT)]
        self.answer = b""
        status = self.provider.poll()[0]
        self.assertEqual((status.name, status.level), ("ATK V75X", None))
        self.assertEqual(self.dev.write.call_count, 2)

    def test_same_atk_keyboard_retains_level_across_failed_cable_switch(self):
        self.entries = [keyboard_entry(product_string=A.PRODUCT)]
        self.provider.poll()
        self.entries = [keyboard_entry(product_id=0x5055, product_string="ATK V75X",
                                       path=b"cable")]
        self.answer = b""
        status = self.provider.poll()[0]
        self.assertEqual((status.name, status.level, status.online), ("ATK V75X", 59, False))

    def test_activity_paths_are_limited_to_selected_brand(self):
        self.entries += [keyboard_entry(usage_page=1, usage=6, path=b"vgn-keys"),
                         keyboard_entry(product_string=A.PRODUCT, usage_page=1,
                                        usage=6, path=b"atk-keys")]
        self.provider.poll()
        self.activity.configure.assert_called_once_with([b"vgn-keys"])


def mouse_entry(pid=0xFA65, **changes):
    return {"vendor_id": 0x1D57, "product_id": pid, "interface_number": 2,
            "usage_page": 0xA, "usage": 0, "path": b"mouse",
            "product_string": "2.4G 8K HS Receiver", **changes}


class DeluxTests(unittest.TestCase):
    def setUp(self):
        self.provider = I.InphicProvider()
        self.dev = Mock()
        self.dev.read.return_value = []
        self.entries = [mouse_entry()]
        self.now = 1000.0
        for context in (
            patch.object(I, "hid", types.SimpleNamespace(device=lambda: self.dev)),
            patch.object(I.hidlist, "enumerate", side_effect=lambda vid: self.entries),
            patch.object(I.time, "time", side_effect=lambda: self.now),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_literal_capture_is_gated_by_receiver_and_named_without_writes(self):
        self.dev.read.return_value = DELUX_REPLY
        status = self.provider.poll()[0]
        self.assertEqual((status.name, status.level, status.online), ("Delux M900 Pro", 74, True))
        self.assertIsNone(I.parse_frame(DELUX_REPLY, I.PIDS[0xFA60][1]))
        self.dev.write.assert_not_called()
        self.dev.send_feature_report.assert_not_called()
        self.dev.get_feature_report.assert_not_called()

    def test_delux_name_survives_cached_and_grey_status(self):
        self.dev.read.return_value = DELUX_REPLY
        self.provider.poll()
        self.dev.read.return_value = []
        for age, online in ((30, True), (100, False)):
            self.now = 1000 + age
            status = self.provider.poll()[0]
            self.assertEqual((status.name, status.level, status.online),
                             ("Delux M900 Pro", 74, online))

    def test_rejected_delux_frame_does_not_change_existing_inphic_name(self):
        self.dev.read.return_value = bytes.fromhex("03 95 40 01 4b")
        self.provider.poll()
        self.dev.read.return_value = bytes.fromhex("03 3f 40 01 ff")
        status = self.provider.poll()[0]
        self.assertEqual((status.name, status.level), ("Inphic In9 Pro", 75))

    def test_fa65_has_twelve_reads_but_fa60_retains_five(self):
        for pid, attempts in ((0xFA65, 12), (0xFA60, 5)):
            self.provider = I.InphicProvider()
            self.dev.reset_mock()
            self.entries = [mouse_entry(pid)]
            self.assertEqual(self.provider.poll(), [])
            self.assertEqual(self.dev.read.call_count, attempts)
            self.dev.read.assert_called_with(100, 250)
            self.dev.close.assert_called_once_with()

    def test_full_budget_is_retained_on_remembered_collection(self):
        self.dev.read.return_value = DELUX_REPLY
        self.provider.poll()
        self.dev.reset_mock()
        self.dev.read.return_value = []
        self.now += 60
        self.provider.poll()
        self.assertEqual(self.dev.read.call_count, 12)

    def test_delux_model_cannot_rename_fa60(self):
        self.entries = [mouse_entry(0xFA60)]
        self.dev.read.return_value = DELUX_REPLY
        self.assertEqual(self.provider.poll(), [])
        self.assertNotIn(0xFA60, self.provider._model)

    def test_disappearance_during_read_returns_no_status_and_closes(self):
        self.dev.read.side_effect = OSError("disconnected")
        self.assertEqual(self.provider.poll(), [])
        self.dev.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
