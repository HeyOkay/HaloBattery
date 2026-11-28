"""Issue #109's old VT9 Pro pushes, independent of the provider's constants.

CAPTURE is the complete seven-byte interrupt-IN payload repeated ten times in
https://github.com/user-attachments/files/32778389/wireshark.capture.pcapng.gz
gzip SHA-256: 1e77dd10c5169d4a3c0fe227bdff902fc6cfc7344130f7d34de1839097574b47
USB descriptor packet 5 identifies bus 2/device 1 as 24AE:185A; its endpoint
82 carries these frames. Charging/other percentages below are synthetic edge
fixtures using the state meanings in test-build commit d5a5a5dfc6e93b2b26719f590e7dc65f1e3e4bdb.
Tests never download the capture, enumerate a real HID device, or write to one.
Padded fixtures are synthetic Windows transport cases, not captured report sizes:
HIDP_CAPS.InputReportByteLength is the collection maximum (Microsoft Learn,
ns-hidpi-_hidp_caps); HIDAPI windows/hid.c at 378a53a8b6c098a32bea5dd659d647898030ccb0
and halo/hid_transport.py preserve numbered-report completion bytes. The old
test build accepts >=7; 64 below is only the existing listener's caller bound.
"""
from types import SimpleNamespace
import unittest
from unittest import mock

from providers import rapoo


CAPTURE = bytes.fromhex("bb b0 42 20 03 01 64")
REPORT7 = bytes.fromhex("07 02 01 d0 07 d0 07 01 54 00 01 01 01")
KEYBOARD_BD = bytes.fromhex("bd 01 00 07 00 46 00 01 54 ad cf 7d")


def descriptor(path, usage=2, *, pid=0x185A, page=0xFF00, interface=1, vendor=0x24AE):
    return dict(path=path, vendor_id=vendor, product_id=pid, interface_number=interface,
                usage_page=page, usage=usage, product_string="Rapoo Gaming Device")


class Device:
    def __init__(self, frames=()):
        self.frames, self.calls = list(frames), []
        self.opened = self.closed = 0
    def open_path(self, path):
        self.opened += 1
    def read(self, count, timeout):
        self.calls.append((count, timeout))
        value = self.frames.pop(0) if self.frames else []
        if isinstance(value, Exception):
            raise value
        return value
    def close(self):
        self.closed += 1
    def write(self, *args):
        raise AssertionError("VT9 Pro support is passive; writes are forbidden")
    send_feature_report = write
    get_feature_report = write


class VT9ProTests(unittest.TestCase):
    def setUp(self):
        self.provider = rapoo.RapooProvider()
        self.now = 1000.0
        clock = SimpleNamespace(time=lambda: self.now, sleep=lambda duration: None)
        patch = mock.patch.object(rapoo, "time", clock)
        patch.start(); self.addCleanup(patch.stop)

    def poll(self, descriptors, devices):
        class RoutedDevice:
            def open_path(inner, path):
                inner.device = devices[path]
                inner.device.open_path(path)
            def __getattr__(inner, name):
                return getattr(inner.device, name)
        with mock.patch.object(rapoo.hidlist, "enumerate", return_value=descriptors), \
                mock.patch.object(rapoo.hid, "device", side_effect=RoutedDevice), \
                mock.patch("threading.Thread", side_effect=AssertionError("unexpected provider thread")):
            return self.provider.poll()

    def test_literal_captured_frame_restores_old_receiver_key_and_percentage(self):
        item = descriptor(b"vt9pro")
        device = Device([CAPTURE])
        result = self.poll([item], {item["path"]: device})
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind) for s in result],
                         [("rapoo:185a", "Rapoo VT9 Pro", 100, False, True, "rapoo", "mouse")])
        self.assertEqual(device.calls, [(64, 250)])
        self.assertEqual(device.closed, 1)

    def test_zero_hundred_and_sourced_charging_state_without_clamping(self):
        cases = (("bb b0 42 20 03 01 00", (0, False)),
                 ("bb b0 42 20 03 01 64", (100, False)),
                 ("bb b0 42 20 03 02 50", (80, True)))
        for frame, expected in cases:
            with self.subTest(frame=frame):
                self.assertEqual(rapoo.parse_vt9pro(bytes.fromhex(frame)), expected)

    def test_payload_length_marker_flags_connection_state_and_range_are_required(self):
        invalid = [None, True, {}, "bb b0 42 20 03 01 64", REPORT7, KEYBOARD_BD,
                   bytes.fromhex("bc b0 42 20 03 01 64 00 00 00")]
        invalid += [CAPTURE[:length] for length in range(7)]
        invalid += [CAPTURE + bytes(58), CAPTURE + bytes(121)]
        for index, value in ((0, 0xBC), (1, 0xB1), (4, 0), (4, 2),
                             (5, 0), (5, 3), (5, 4), (5, 255),
                             (6, -1), (6, 101), (6, 255), (6, True),
                             (5, True), (2, "flags"), (3, None)):
            frame = list(CAPTURE); frame[index] = value
            invalid.append(frame)
        for frame in invalid:
            with self.subTest(frame=frame):
                self.assertIsNone(rapoo.parse_vt9pro(frame))

    def test_zero_padded_windows_completion_preserves_the_seven_byte_payload(self):
        item = descriptor(b"vt9pro")
        for length in (8, 10, 32, 64):
            with self.subTest(buffer_length=length):
                frame = CAPTURE + bytes(length - 7)
                self.assertEqual(rapoo.parse_vt9pro(frame), (100, False))
                result = self.poll([item], {item["path"]: Device([frame])})
                self.assertEqual([(s.level, s.charging, s.online) for s in result], [(100, False, True)])

    def test_nonzero_tail_is_not_padding_and_oversized_buffers_are_refused(self):
        for length in (8, 10, 32, 64):
            with self.subTest(buffer_length=length):
                frame = CAPTURE + bytes(length - 8) + b"\x01"
                self.assertIsNone(rapoo.parse_vt9pro(frame))
        self.assertIsNone(rapoo.parse_vt9pro(CAPTURE + bytes(58)))

    def test_report7_and_bd_do_not_authorize_a_vt9pro_reading(self):
        item = descriptor(b"old-receiver")
        device = Device([REPORT7, KEYBOARD_BD])
        self.assertEqual(self.poll([item], {item["path"]: device}), [])
        self.assertEqual(len(device.calls), 16)
        self.assertEqual(device.closed, 1)

    def test_bb_does_not_authorize_any_other_claimed_rapoo_pid(self):
        for pid in (0x1413, 0x4613, 0x4824, 0x1924, 0x14A9):
            with self.subTest(pid=pid):
                item = descriptor(b"different-family", pid=pid)
                device = Device([CAPTURE])
                self.assertEqual(self.poll([item], {item["path"]: device}), [])
                self.assertEqual(device.closed, 1)

    def test_old_and_new_receivers_keep_separate_formats_and_cache_keys(self):
        old, new = descriptor(b"old"), descriptor(b"new", pid=0x1413)
        result = self.poll([old, new], {b"old": Device([CAPTURE]), b"new": Device([REPORT7])})
        self.assertEqual(sorted((s.key, s.level) for s in result), [("rapoo:1413", 84), ("rapoo:185a", 100)])

    def test_only_dumped_vendor_collections_on_interface_one_are_listened_to(self):
        valid = descriptor(b"known")
        refused = [descriptor(b"pointer", page=1, interface=0),
                   descriptor(b"keyboard", page=1, usage=6),
                   descriptor(b"consumer", page=0x0C, usage=1),
                   descriptor(b"other-interface", interface=2),
                   descriptor(b"other-vendor-usage", usage=0x0F),
                   descriptor(b"wrong-vendor", vendor=0x1234),
                   descriptor(b"not-claimed", pid=0x1215)]
        devices = {item["path"]: Device([CAPTURE]) for item in [valid] + refused}
        result = self.poll(refused + [valid], devices)
        self.assertEqual([s.key for s in result], ["rapoo:185a"])
        self.assertTrue(all(devices[item["path"]].opened == 0 for item in refused))

    def test_second_known_vendor_collection_is_found_and_remembered(self):
        first, second = descriptor(b"usage2"), descriptor(b"usage14", usage=14)
        a, b = Device(), Device([CAPTURE])
        self.assertEqual([s.level for s in self.poll([first, second], {b"usage2": a, b"usage14": b})], [100])
        self.assertEqual(len(a.calls), 16)
        self.assertEqual(self.provider._chosen[0x185A], b"usage14")
        a, b = Device([CAPTURE]), Device([CAPTURE])
        self.assertEqual([s.level for s in self.poll([first, second], {b"usage2": a, b"usage14": b})], [100])
        self.assertEqual(a.opened, 0)
        self.assertEqual(b.opened, 1)

    def test_transition_and_wrong_family_are_skipped_before_a_settled_frame(self):
        item = descriptor(b"vt9pro")
        device = Device([REPORT7, bytes.fromhex("bb b0 42 20 03 00 64"), CAPTURE])
        self.assertEqual([s.level for s in self.poll([item], {b"vt9pro": device})], [100])
        self.assertEqual(len(device.calls), 3)

    def test_silence_and_read_disappearance_keep_grey_cache_but_unplug_clears_it(self):
        item = descriptor(b"vt9pro")
        self.poll([item], {b"vt9pro": Device([CAPTURE])})
        for frames in ([], [OSError("receiver disappeared during read")]):
            with self.subTest(frames=frames):
                device = Device(frames)
                result = self.poll([item], {b"vt9pro": device})
                self.assertEqual([(s.level, s.online) for s in result], [(100, False)])
                self.assertTrue(result[0].asleep, "silence means asleep, not a charger")
                self.assertFalse(result[0].charging, "a greyed reading keeps no charging flag")
                self.assertEqual(device.closed, 1)
        self.assertEqual(self.poll([], {}), [])
        self.assertNotIn("rapoo:185a", self.provider._last)
        self.assertEqual(self.poll([item], {b"vt9pro": Device()}), [])

    def test_passive_read_attempts_are_bounded_and_cache_expires(self):
        item = descriptor(b"vt9pro")
        self.poll([item], {b"vt9pro": Device([CAPTURE])})
        self.now += 301
        device = Device([[]] * 16 + [CAPTURE])
        self.assertEqual(self.poll([item], {b"vt9pro": device}), [])
        self.assertEqual(len(device.calls), 16)
        self.assertEqual(device.closed, 1)
