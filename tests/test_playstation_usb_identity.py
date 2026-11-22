"""Sourced USB identity reads, with fake HID only and literal pairing payloads."""
import unittest
from types import SimpleNamespace
from unittest import mock

from providers import playstation as P
from test_playstation import Clock, FakePad, BT_PATH, USB_PATH, USB_PATH_2, entry, usb_pad


MAC = "a0ab51123456"
OTHER_MAC = "102030405060"


def pairing(report_id=9, size=20, mac=MAC):
    # SDL prints indices 6..1; the report includes the device, not host, address.
    return [report_id, *bytes.fromhex(mac)[::-1], *([0] * (size - 7))]


def descriptor(path=USB_PATH, pid=0x0ce6, **extra):
    value = entry(path, pid=pid, mac="")
    value.update(vendor_id=0x054c, bus_type=1)
    value.update(extra)
    return value


class UsbIdentityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.provider = P.PlayStationProvider()
        self.requests = []
        self.opens = []
        self.closes = []
        patcher = mock.patch.object(P, "time", SimpleNamespace(time=self.clock.time, sleep=self.clock.sleep))
        patcher.start()
        self.addCleanup(patcher.stop)

    def poll(self, descriptors, pads, replies=None):
        owner = self
        replies = replies or {}

        class Device:
            def open_path(self, path):
                owner.opens.append(path)
                self.path = path
                self.pad = pads[path]

            def set_nonblocking(self, enabled):
                pass

            def get_feature_report(self, report_id, size):
                owner.requests.append((self.path, report_id, size))
                if report_id in (9, 0x12):
                    reply = replies.get(self.path, [])
                    if isinstance(reply, Exception):
                        raise reply
                    return reply
                return self.pad.on_feature(report_id)

            def read(self, size):
                return self.pad.on_read()

            def close(self):
                owner.closes.append(self.path)

        with mock.patch.object(P, "hid", SimpleNamespace(device=Device)), \
                mock.patch.object(P, "hidlist", SimpleNamespace(enumerate=lambda vid: list(descriptors))):
            return self.provider.poll()

    def test_each_sourced_model_uses_exact_sdl_buffer_on_existing_handle(self):
        for pid, rid, maximum, minimum, trigger in ((0x05c4, 0x12, 64, 16, 2),
                                                  (0x09cc, 0x12, 64, 16, 2),
                                                  (0x0ce6, 9, 128, 20, 5),
                                                  (0x0df2, 9, 128, 20, 5)):
            with self.subTest(pid=pid):
                path = USB_PATH.replace(b"0ce6", f"{pid:04x}".encode())
                pad = usb_pad(0x08)
                if trigger == 2:
                    report = [1] + [0] * 63
                    report[30] = 8
                    pad.on_read = lambda: report
                self.requests.clear()
                self.opens.clear()
                self.closes.clear()
                values = self.poll([descriptor(path, pid)], {path: pad}, {path: pairing(rid, minimum)})
                self.assertEqual([(v.key, v.level) for v in values], [(f"ps:{pid:04x}:" + MAC, 80)])
                self.assertEqual(self.requests, [(path, rid, maximum), (path, trigger, 64)])
                self.assertEqual(self.opens, [path])
                self.assertEqual(self.closes, [path])

    def test_read_allowlist_requires_positive_usb_sony_gamepad_metadata(self):
        valid = descriptor()
        self.assertEqual(P.usb_identity_request(valid), (9, 128, 20))
        for changes in ({"vendor_id": None}, {"vendor_id": 0x1234}, {"product_id": 0x05c5},
                        {"product_id": 0x0ba0}, {"usage_page": 0xff00}, {"usage": 4},
                        {"bus_type": 2}, {"bus_type": 0}, {"bus_type": True},
                        {"path": BT_PATH}, {"path": b"opaque-device"},
                        {"path": USB_PATH.replace(b"0ce6", b"09cc")}):
            with self.subTest(changes=changes):
                self.assertIsNone(P.usb_identity_request(dict(valid, **changes)))

    def test_bluetooth_even_with_usb_bus_metadata_gets_no_identity_feature(self):
        for path in (BT_PATH, b"\\\\?\\hid#vid&0002054c_pid&0ce6#controller",
                     b"\\\\?\\hid#{00001124-0000-1000-8000-00805f9b34fb}#controller",
                     USB_PATH + b"#{00001124-0000-1000-8000-00805f9b34fb}"):
            with self.subTest(path=path):
                info = descriptor(path, serial_number=MAC)
                self.assertIsNone(P.usb_identity_request(info))
                values = self.poll([info], {path: FakePad("full")})
                self.assertEqual(values[0].key, "ps:0ce6:" + MAC)
                self.assertEqual(self.requests, [])

    def test_missing_bus_type_requires_positive_unambiguous_usb_path(self):
        info = descriptor()
        del info["bus_type"]
        self.assertEqual(P.usb_identity_request(info), (9, 128, 20))
        for path in (BT_PATH, b"opaque-device", USB_PATH + b"#vid&0002054c"):
            self.assertIsNone(P.usb_identity_request(dict(info, path=path)))

    def test_complete_payload_echo_and_mac_are_required(self):
        for request in ((0x12, 64, 16), (9, 128, 20)):
            rid, maximum, minimum = request
            valid = pairing(rid, minimum)
            self.assertEqual(P.parse_usb_identity(valid, request), MAC)
            self.assertEqual(P.parse_usb_identity(bytes(valid), request), MAC)
            self.assertEqual(P.parse_usb_identity(valid + [0] * (maximum - minimum), request), MAC)
            for invalid in (None, "not bytes", 3, valid[:7], valid[:-1], [rid + 1] + valid[1:],
                            valid + [0] * (maximum - minimum + 1), pairing(rid, minimum, "000000000000"),
                            pairing(rid, minimum, "ffffffffffff"), [True] + valid[1:],
                            [rid, -1] + valid[2:], [rid, 256] + valid[2:], [rid, 1.0] + valid[2:]):
                with self.subTest(request=request, invalid=invalid):
                    self.assertEqual(P.parse_usb_identity(invalid, request), "")

    def test_usb_without_serial_pairs_only_with_matching_bluetooth_mac(self):
        values = self.poll([entry(BT_PATH, mac=MAC.upper()), descriptor()],
                           {BT_PATH: FakePad("basic"), USB_PATH: usb_pad(0x16)},
                           {USB_PATH: pairing()})
        self.assertEqual([(v.key, v.level, v.charging) for v in values], [("ps:0ce6:" + MAC, 60, True)])
        self.assertEqual(self.provider.legacy_key_aliases(), {"ps:0ce6:" + MAC.upper(): "ps:0ce6:" + MAC})
        self.assertNotIn("ps:0ce6:", self.provider.legacy_key_aliases())

    def test_usb_key_survives_port_move_transport_change_and_provider_restart(self):
        first = self.poll([descriptor()], {USB_PATH: usb_pad(0x08)}, {USB_PATH: pairing()})[0].key
        moved = self.poll([descriptor(USB_PATH_2)], {USB_PATH_2: usb_pad(0x08)}, {USB_PATH_2: pairing()})[0].key
        wireless = self.poll([entry(BT_PATH)], {BT_PATH: FakePad("full")})[0].key
        self.provider = P.PlayStationProvider()
        restarted = self.poll([descriptor()], {USB_PATH: usb_pad(0x08)}, {USB_PATH: pairing()})[0].key
        self.assertEqual([first, moved, wireless, restarted], ["ps:0ce6:" + MAC] * 4)

    def test_same_port_replacement_is_requeried_each_poll(self):
        first = self.poll([descriptor()], {USB_PATH: usb_pad(0x08)}, {USB_PATH: pairing()})[0].key
        second = self.poll([descriptor()], {USB_PATH: usb_pad(0x08)}, {USB_PATH: pairing(mac=OTHER_MAC)})[0].key
        self.assertNotEqual(first, second)
        self.assertEqual(second, "ps:0ce6:" + OTHER_MAC)
        self.assertEqual([request for request in self.requests if request[1] == 9],
                         [(USB_PATH, 9, 128)] * 2)
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_identity_failure_does_not_reuse_previous_pad_or_lose_battery(self):
        self.poll([descriptor()], {USB_PATH: usb_pad(0x08)}, {USB_PATH: pairing()})
        for reply in ([], pairing()[:7], OSError("device disappeared")):
            with self.subTest(reply=reply):
                values = self.poll([descriptor()], {USB_PATH: usb_pad(0x04)}, {USB_PATH: reply})
                self.assertEqual([(v.key, v.level) for v in values], [("ps:0ce6:usb-8&1234abcd&0", 40)])
                self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_multiple_collections_read_identity_once_on_gamepad(self):
        other = USB_PATH.replace(b"mi_03#", b"mi_03&col02#").replace(b"&0&0000#", b"&0&0001#")
        values = self.poll([descriptor(other, usage_page=0xff00, usage=1), descriptor()],
                           {other: usb_pad(0x08), USB_PATH: usb_pad(0x08)}, {USB_PATH: pairing()})
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].key, "ps:0ce6:" + MAC)
        self.assertEqual([r for r in self.requests if r[1] == 9], [(USB_PATH, 9, 128)])

    def test_duplicate_physical_macs_do_not_hide_or_migrate_any_pad(self):
        values = self.poll([descriptor(), descriptor(USB_PATH_2), entry(BT_PATH)],
                           {USB_PATH: usb_pad(0x08), USB_PATH_2: usb_pad(0x04), BT_PATH: FakePad("full")},
                           {USB_PATH: pairing(), USB_PATH_2: pairing()})
        self.assertEqual(len({v.key for v in values}), 3)
        self.assertEqual(sorted(v.level for v in values), [40, 70, 80])
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_conflicting_metadata_and_device_address_cannot_pair(self):
        values = self.poll([descriptor(serial_number=OTHER_MAC), entry(BT_PATH)],
                           {USB_PATH: usb_pad(0x08), BT_PATH: FakePad("full")}, {USB_PATH: pairing()})
        self.assertEqual(len(values), 2)
        self.assertIn("ps:0ce6:usb-8&1234abcd&0", {v.key for v in values})
        self.assertEqual(self.provider.legacy_key_aliases(), {})


if __name__ == "__main__":
    unittest.main()
