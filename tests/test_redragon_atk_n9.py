"""Independent wire fixtures from the pinned sources in the provider docs.

No hardware or network: HID and serial enumeration and handles are replaced.
"""
import types
import unittest
from unittest import mock

from providers import atk_n9 as A, pulsar as P


def serial_reply(command, value):
    return bytes([4, 0, command, 0, value]).ljust(64, b"\x00")


class FakePort:
    def __init__(self, connected=1, battery=0xB2, fail=False):
        self.connected, self.battery, self.fail = connected, battery, fail
        self.writes = []
        self.data = b""
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def reset_input_buffer(self):
        self.data = b""

    def write(self, data):
        self.writes.append(data)
        if self.fail:
            raise OSError("unplugged")
        command = data[2]
        # Unrelated event followed by a reply split over several reads.
        self.data = serial_reply(9, 1) + serial_reply(
            command, self.connected if command == 4 else self.battery)
        return len(data)

    def read(self, size):
        chunk, self.data = self.data[:min(size, 13)], self.data[min(size, 13):]
        return chunk


class AtkN9Tests(unittest.TestCase):
    def poll(self, port, pid=0x11CB):
        info = types.SimpleNamespace(vid=0x373B, pid=pid, device="COM3", serial_number="unit")
        with mock.patch.object(A.list_ports, "comports", return_value=[info]), \
                mock.patch.object(A.serial, "Serial", return_value=port) as factory:
            result = A.AtkN9Provider().poll()
        return result, factory

    def test_requests_are_only_gets(self):
        self.assertEqual(A.make_request(3), bytes.fromhex("02 00 03") + bytes(61))
        self.assertEqual(A.make_request(4), bytes.fromhex("02 00 04") + bytes(61))
        with self.assertRaises(ValueError):
            A.make_request(5)

    def test_power_boundaries_and_charging(self):
        for value, expected in ((0, (0, False)), (100, (100, False)),
                                (128, (0, True)), (228, (100, True)), (178, (50, True))):
            self.assertEqual(expected, A.parse_reply(serial_reply(3, value), 3))
        for value in (101, 127, 229, 255):
            self.assertIsNone(A.parse_reply(serial_reply(3, value), 3))

    def test_rejects_short_garbage_wrong_command_and_lengths(self):
        frame = serial_reply(3, 50)
        for data in (None, b"", frame[:5], bytes(64), frame + b"x", serial_reply(4, 1),
                     bytes([3]) + frame[1:], bytes([64]) + frame[1:]):
            self.assertIsNone(A.parse_reply(data, 3))
        self.assertIsNone(A.parse_reply(serial_reply(4, 2), 4))

    def test_poll_fragmented_reply_and_events(self):
        port = FakePort()
        found, factory = self.poll(port)
        self.assertEqual((50, True, "headset", "atk_n9"),
                         (found[0].level, found[0].charging, found[0].kind, found[0].source))
        self.assertEqual([A.make_request(4), A.make_request(3)], port.writes)
        self.assertTrue(port.closed)
        factory.assert_called_once_with("COM3", 9600, timeout=0.1, write_timeout=0.5)

    def test_off_does_not_query_battery(self):
        port = FakePort(connected=0)
        found, _ = self.poll(port)
        self.assertFalse(found[0].online)
        self.assertIsNone(found[0].level)
        self.assertFalse(found[0].charging_known)
        self.assertEqual(1, len(port.writes))

    def test_power_cycle_recovers_with_same_identity(self):
        info = types.SimpleNamespace(vid=0x373B, pid=0x11CB, device="COM3", serial_number="unit")
        ports = [FakePort(), FakePort(connected=0), FakePort(battery=49)]
        provider = A.AtkN9Provider()
        with mock.patch.object(A.list_ports, "comports", return_value=[info]), \
                mock.patch.object(A.serial, "Serial", side_effect=ports):
            readings = [provider.poll()[0] for _ in ports]
        self.assertEqual([True, False, True], [s.online for s in readings])
        self.assertEqual([50, None, 49], [s.level for s in readings])
        self.assertEqual(1, len({s.key for s in readings}))
        self.assertEqual(5, provider.refresh_interval)
        with mock.patch.object(A.list_ports, "comports", return_value=[]):
            self.assertEqual([], provider.poll())
        self.assertIsNone(provider.refresh_interval)

    def test_unrelated_device_is_never_opened(self):
        found, factory = self.poll(FakePort(), pid=0x1204)
        self.assertEqual([], found)
        factory.assert_not_called()

    def test_unplug_during_query_closes_port(self):
        port = FakePort(fail=True)
        self.assertEqual([], self.poll(port)[0])
        self.assertTrue(port.closed)

    def test_silent_port_has_bounded_deadline(self):
        port = FakePort()
        port.read = lambda size: b""
        with mock.patch.object(A.time, "monotonic", side_effect=[0, 0.1, 2]):
            self.assertIsNone(A.AtkN9Provider()._query(port, 3))


class RedragonTests(unittest.TestCase):
    def test_power_cycle_greys_cached_level_and_recovers(self):
        info = dict(product_id=0xF54F, path=b"report8", usage_page=0xFF02, usage=2)
        frame = list(bytes.fromhex("08 04 00 00 00 02 37 00 0f 40 00 00 00 00 00 00 c1"))
        provider = P.PulsarProvider()
        with mock.patch.object(P.hidlist, "enumerate", side_effect=lambda vid: [info] if vid == 0x3554 else []), \
                mock.patch.object(P, "output_length", return_value=17), \
                mock.patch.object(provider, "_query", side_effect=[frame, None, frame]):
            readings = [provider.poll()[0] for _ in range(3)]
        self.assertEqual([True, False, True], [s.online for s in readings])
        self.assertEqual([55, 55, 55], [s.level for s in readings])
        self.assertTrue(readings[1].wake_on_input)
        self.assertEqual(1, len({s.key for s in readings}))
        self.assertEqual(5, provider.refresh_interval)
        with mock.patch.object(P.hidlist, "enumerate", return_value=[]):
            self.assertEqual([], provider.poll())
        self.assertIsNone(provider.refresh_interval)

    def test_boundaries_and_unknown_charge_are_not_invented(self):
        info = dict(product_id=0xF54F, path=b"report8", usage_page=0xFF02, usage=2)
        for level, flag, valid in ((0, 0, True), (100, 1, True), (101, 0, False),
                                   (50, 2, False)):
            frame = [8, 4, 0, 0, 0, 2, level, flag] + [0] * 8
            frame.append((0x55 - sum(frame)) & 255)
            with self.subTest(level=level, flag=flag), \
                    mock.patch.object(P.hidlist, "enumerate", side_effect=lambda vid: [info] if vid == 0x3554 else []), \
                    mock.patch.object(P, "output_length", return_value=17), \
                    mock.patch.object(P.PulsarProvider, "_query", return_value=frame):
                found = P.PulsarProvider().poll()
                self.assertEqual(valid, bool(found))
                if valid:
                    self.assertEqual(level, found[0].level)

    def test_only_documented_collection_is_opened(self):
        # Reference WebHID payload 04 00 00 00 00 02 32 01 0f a0 ...,
        # with report ID 08 and checksum 65 (sum of complete frame = 55 mod 256).
        reply = list(bytes.fromhex("08 04 00 00 00 02 32 01 0f a0 00 00 00 00 00 00 65"))
        self.assertEqual(sum(reply) & 255, 0x55)
        for pid in (0xF54C, 0xF54F, 0xF55F, 0xF54E):
            with self.subTest(pid=pid):
                infos = [dict(product_id=pid, path=b"wrong", usage_page=0xFF04, usage=2),
                         dict(product_id=pid, path=b"right", usage_page=0xFF02, usage=2)]
                dev = mock.Mock()
                dev.read.side_effect = [[], reply]
                with mock.patch.object(P.hidlist, "enumerate", side_effect=lambda vid: infos if vid == 0x3554 else []), \
                        mock.patch.object(P.hid, "device", return_value=dev), \
                        mock.patch.object(P, "output_length", return_value=17):
                    found = P.PulsarProvider().poll()
                dev.open_path.assert_called_once_with(b"right")
                dev.write.assert_called_once_with([8, 4] + [0] * 14 + [0x49])
                self.assertEqual((50, True), (found[0].level, found[0].charging))
                dev.close.assert_called_once()

    def test_no_report8_collection_means_no_io(self):
        info = dict(product_id=0xF54F, path=b"wrong", usage_page=0xFF04, usage=2)
        with mock.patch.object(P.hidlist, "enumerate", side_effect=lambda vid: [info] if vid == 0x3554 else []), \
                mock.patch.object(P.hid, "device") as factory:
            self.assertEqual([], P.PulsarProvider().poll())
        factory.assert_not_called()
