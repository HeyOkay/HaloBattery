"""PlayStation endpoint identity from existing metadata; no new device requests."""
import unittest
from types import SimpleNamespace
from unittest import mock

from providers import playstation as P
from test_playstation import Clock, FakeBus, FakePad, BT_PATH, USB_PATH, USB_PATH_2, entry, usb_pad


class PlayStationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        patcher = mock.patch.object(P, "time", SimpleNamespace(time=self.clock.time, sleep=self.clock.sleep))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = P.PlayStationProvider()

    def poll(self, descriptors, pads):
        bus = FakeBus(pads)
        with mock.patch.object(P, "hid", SimpleNamespace(device=bus.device_class())), \
                mock.patch.object(P, "hidlist", SimpleNamespace(enumerate=lambda vid: list(descriptors))):
            return self.provider.poll()

    def test_same_model_different_controllers_are_not_merged_without_identity(self):
        values = self.poll([entry(BT_PATH), entry(USB_PATH, mac="")],
                           {BT_PATH: FakePad("full", status=0x02), USB_PATH: usb_pad(0x19)})
        self.assertEqual(len(values), 2)
        self.assertEqual({value.key: (value.level, value.charging) for value in values},
                         {"ps:0ce6:a0ab51123456": (20, False), "ps:0ce6:usb-8&1234abcd&0": (90, True)})

    def test_unique_equal_mac_metadata_merges_transports_and_preserves_bt_key(self):
        bt_serial = "A0AB51123456"
        values = self.poll([entry(BT_PATH, mac=bt_serial), entry(USB_PATH, mac="a0:ab:51:12:34:56")],
                           {BT_PATH: FakePad("full", status=0x02), USB_PATH: usb_pad(0x17)})
        self.assertEqual([(v.key, v.level, v.charging) for v in values],
                         [("ps:0ce6:" + bt_serial, 70, True)])
        self.assertEqual(self.provider.legacy_key_aliases(),
                         {"ps:0ce6:": "ps:0ce6:" + bt_serial,
                          "ps:0ce6:a0:ab:51:12:34:56": "ps:0ce6:" + bt_serial})

    def test_equal_usb_and_bt_serials_still_read_both_transport_reports(self):
        bt = FakePad("full", status=0x02)
        usb = usb_pad(0x18)
        values = self.poll([entry(BT_PATH), entry(USB_PATH, mac="a0ab51123456")], {BT_PATH: bt, USB_PATH: usb})
        self.assertEqual([(v.level, v.charging) for v in values], [(80, True)])
        self.assertEqual(bt.features, [])
        self.assertEqual(usb.features, [5])

    def test_same_mac_on_two_usb_clones_does_not_prove_which_matches_bt(self):
        values = self.poll([entry(BT_PATH), entry(USB_PATH, mac="a0ab51123456"),
                            entry(USB_PATH_2, mac="a0ab51123456")],
                           {BT_PATH: FakePad("full", status=0x02), USB_PATH: usb_pad(0x17), USB_PATH_2: usb_pad(0x14)})
        self.assertEqual(len(values), 3)
        self.assertEqual(len({v.key for v in values}), 3)
        self.assertEqual(sorted(v.level for v in values), [20, 40, 70])
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_empty_dummy_zero_and_broadcast_serials_are_not_matching_proof(self):
        for serial in ("", "dummy", "000000000000", "00:00:00:00:00:00", "FFFFFFFFFFFF",
                       "A0AB51123456:", ":A0AB51123456", "A0:AB-51:12:34:56"):
            with self.subTest(serial=serial):
                values = self.poll([entry(BT_PATH, mac=serial), entry(USB_PATH, mac=serial)],
                                   {BT_PATH: FakePad("full", status=0x02), USB_PATH: usb_pad(0x17)})
                self.assertEqual(len(values), 2)
                self.assertEqual(len({v.key for v in values}), 2)

    def test_multiple_bt_candidates_with_same_mac_are_not_paired_arbitrarily(self):
        base = {"pid": 0x0ce6, "name": "pad", "reading": (20, False), "basic": False,
                "serial": "a0ab51123456", "mac": "a0ab51123456", "usb": "", "identity": "a0ab51123456"}
        first, second = dict(base, identity="bt-first"), dict(base, identity="bt-second")
        cable = dict(base, bluetooth=False, mac="", usb="usb-instance", identity="usb-instance", reading=(80, True))
        first["bluetooth"] = second["bluetooth"] = True
        result = self.provider._merge([first, second, cable])
        self.assertEqual(len(result), 3)
        self.assertEqual([c["identity"] for c in result], ["bt-first", "bt-second", "usb-instance"])
        self.assertEqual([c["mac"] for c in result], ["", "", ""])

    def test_duplicate_bt_serial_metadata_cannot_hide_another_endpoint(self):
        second_bt = BT_PATH.replace(b"9&2b0f7c5a", b"9&0000bbbb")
        values = self.poll([entry(BT_PATH), entry(second_bt), entry(USB_PATH, mac="a0ab51123456")],
                           {BT_PATH: FakePad("full", status=0x02), second_bt: FakePad("full", status=0x04),
                            USB_PATH: usb_pad(0x17)})
        self.assertEqual(len(values), 3)
        self.assertEqual(len({v.key for v in values}), 3)
        self.assertEqual(sorted(v.level for v in values), [20, 40, 70])

    def test_usb_keys_do_not_change_as_another_controller_appears_or_disappears(self):
        descriptors = [entry(USB_PATH, mac=""), entry(USB_PATH_2, mac="")]
        pads = {USB_PATH: usb_pad(0x08), USB_PATH_2: usb_pad(0x03)}
        first = self.poll(descriptors[:1], pads)[0].key
        both = {value.level: value.key for value in self.poll(descriptors, pads)}
        reverse = {value.level: value.key for value in self.poll(list(reversed(descriptors)), pads)}
        second = self.poll(descriptors[1:], pads)[0].key
        self.assertEqual(first, both[80])
        self.assertEqual(second, both[30])
        self.assertEqual(both, reverse)

    def test_serial_clones_keep_distinct_stable_usb_endpoint_keys(self):
        descriptors = [entry(USB_PATH, mac="generic-clone"), entry(USB_PATH_2, mac="generic-clone")]
        pads = {USB_PATH: usb_pad(0x08), USB_PATH_2: usb_pad(0x03)}
        first = self.poll(descriptors[:1], pads)[0].key
        both = {v.level: v.key for v in self.poll(descriptors, pads)}
        self.assertEqual(len(set(both.values())), 2)
        self.assertEqual(first, both[80])
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_unparseable_paths_get_distinct_hash_keys_and_keep_them(self):
        one, two = b"opaque-usb-endpoint-one", b"opaque-usb-endpoint-two"
        descriptors = [entry(one, mac=""), entry(two, mac="")]
        pads = {one: usb_pad(0x08), two: usb_pad(0x03)}
        first = self.poll(descriptors[:1], pads)[0].key
        both = {v.level: v.key for v in self.poll(descriptors, pads)}
        self.assertEqual(len(set(both.values())), 2)
        self.assertIn("usb-path-", first)
        self.assertEqual(first, both[80])
        self.assertEqual(both, {v.level: v.key for v in self.poll(list(reversed(descriptors)), pads)})

    def test_moving_a_serialless_controller_to_another_port_is_new_identity(self):
        old = self.poll([entry(USB_PATH, mac="")], {USB_PATH: usb_pad(0x08)})[0].key
        moved = self.poll([entry(USB_PATH_2, mac="")], {USB_PATH_2: usb_pad(0x08)})[0].key
        self.assertNotEqual(old, moved)

    def test_legacy_alias_is_only_the_sole_candidate_and_is_cached(self):
        pad = usb_pad(0x08)
        value = self.poll([entry(USB_PATH, mac="")], {USB_PATH: pad})[0]
        clock, features = self.clock.now, list(pad.features)
        expected = {"ps:0ce6:": value.key}
        self.assertEqual(self.provider.legacy_key_aliases(), expected)
        self.provider.legacy_key_aliases().clear()
        self.assertEqual(self.provider.legacy_key_aliases(), expected)
        self.assertEqual(self.clock.now, clock)
        self.assertEqual(pad.features, features)
        self.poll([entry(USB_PATH, mac=""), entry(USB_PATH_2, mac="")],
                  {USB_PATH: usb_pad(0x08), USB_PATH_2: usb_pad(0x03)})
        self.assertEqual(self.provider.legacy_key_aliases(), {})
        self.poll([], {})
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_second_unreadable_usb_candidate_still_prevents_legacy_alias(self):
        values = self.poll([entry(USB_PATH, mac=""), entry(USB_PATH_2, mac="")],
                           {USB_PATH: usb_pad(0x08)})
        self.assertEqual(len(values), 2)
        self.assertEqual(sum(v.level is not None for v in values), 1)
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_conflicting_collection_serial_metadata_is_not_matching_proof(self):
        second_collection = USB_PATH.replace(b"mi_03#", b"mi_03&col02#").replace(b"&0&0000#", b"&0&0001#")
        values = self.poll([entry(BT_PATH), entry(USB_PATH, mac="a0ab51123456"),
                            entry(second_collection, mac="102030405060")],
                           {BT_PATH: FakePad("full", status=0x02), USB_PATH: usb_pad(0x17),
                            second_collection: usb_pad(0x17)})
        self.assertEqual(len(values), 2)
        self.assertEqual(sorted(v.level for v in values), [20, 70])

    def test_different_macs_never_merge_even_when_battery_levels_match(self):
        values = self.poll([entry(BT_PATH), entry(USB_PATH, mac="102030405060")],
                           {BT_PATH: FakePad("full", status=0x05), USB_PATH: usb_pad(0x05)})
        self.assertEqual(len(values), 2)

    def test_sole_bluetooth_without_mac_migrates_plain_key(self):
        for serial in ("", "dummy", "000000000000"):
            with self.subTest(serial=serial):
                value = self.poll([entry(BT_PATH, mac=serial)], {BT_PATH: FakePad("basic")})[0]
                self.assertEqual(self.provider.legacy_key_aliases()["ps:0ce6:"], value.key)
                if serial:
                    self.assertEqual(self.provider.legacy_key_aliases()["ps:0ce6:" + serial], value.key)

    def test_no_mac_bluetooth_and_usb_never_move_plain_key_to_usb(self):
        for serial in ("", "dummy"):
            for reverse in (False, True):
                descriptors = [entry(BT_PATH, mac=serial), entry(USB_PATH, mac="")]
                if reverse:
                    descriptors.reverse()
                values = self.poll(descriptors, {BT_PATH: FakePad("basic"), USB_PATH: usb_pad(0x08)})
                self.assertEqual(len(values), 2)
                self.assertNotIn("ps:0ce6:", self.provider.legacy_key_aliases())

    def test_unique_legacy_usb_serials_migrate_to_their_own_endpoint(self):
        descriptors = [entry(USB_PATH, mac="pad-one"), entry(USB_PATH_2, mac="pad-two")]
        for ordering in (descriptors, list(reversed(descriptors))):
            values = self.poll(ordering, {USB_PATH: usb_pad(0x08), USB_PATH_2: usb_pad(0x03)})
            keys = {value.level: value.key for value in values}
            self.assertEqual(self.provider.legacy_key_aliases(),
                             {"ps:0ce6:pad-one": keys[80], "ps:0ce6:pad-two": keys[30]})

    def test_conflicting_collection_serial_cannot_migrate_a_shared_legacy_key(self):
        other_collection = USB_PATH.replace(b"&0&0000#", b"&0&0001#")
        self.poll([entry(USB_PATH, mac="pad-one"), entry(other_collection, mac="pad-two"),
                   entry(USB_PATH_2, mac="pad-two")],
                  {USB_PATH: usb_pad(0x08), other_collection: usb_pad(0x08), USB_PATH_2: usb_pad(0x03)})
        self.assertNotIn("ps:0ce6:pad-one", self.provider.legacy_key_aliases())
        self.assertNotIn("ps:0ce6:pad-two", self.provider.legacy_key_aliases())

    def test_shared_usb_bt_placeholder_serial_does_not_prove_legacy_owner(self):
        self.poll([entry(BT_PATH, mac="dummy"), entry(USB_PATH, mac="dummy")],
                  {BT_PATH: FakePad("basic"), USB_PATH: usb_pad(0x08)})
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_unreadable_bluetooth_still_blocks_plain_usb_alias(self):
        self.poll([entry(BT_PATH, mac=""), entry(USB_PATH, mac="")],
                  {USB_PATH: usb_pad(0x08)})
        self.assertNotIn("ps:0ce6:", self.provider.legacy_key_aliases())

    def test_legacy_serial_cannot_take_over_another_live_endpoint_key(self):
        self.poll([entry(USB_PATH, mac="usb-8&5678ef01&0"), entry(USB_PATH_2, mac="")],
                  {USB_PATH: usb_pad(0x08), USB_PATH_2: usb_pad(0x03)})
        self.assertNotIn("ps:0ce6:usb-8&5678ef01&0", self.provider.legacy_key_aliases())


if __name__ == "__main__":
    unittest.main()
