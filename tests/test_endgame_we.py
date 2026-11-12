"""XM2we status queries using fake HID devices; no real hardware is opened."""
import unittest
from unittest.mock import patch

from providers import endgame_we as E


def reply(op, payload):
    data = [9, op, 0, 0, 0, len(payload)] + payload
    data += [0] * (16 - len(data))
    return data + [(0x55 - sum(data)) & 255]


class FakeDevice:
    def __init__(self, battery=None, link=None):
        self.answers = {4: reply(4, [75, 0]) if battery is None else battery,
                        3: reply(3, [1]) if link is None else link}
        self.pending = []
        self.sent = []
        self.closed = False

    def open_path(self, path):
        self.path = path

    def read(self, size, timeout):
        return self.pending.pop(0) if self.pending else []

    def send_feature_report(self, data):
        self.sent.append(data)
        self.pending.append(self.answers[data[1]])
        return len(data)

    def close(self):
        self.closed = True


INFO = dict(product_id=E.PID, usage_page=0xFF02, usage=2, path=b"receiver&col04")


class EndgameTests(unittest.TestCase):
    def test_windows_collection_suffixes_match_but_other_receivers_do_not(self):
        prefix = b"hid#vid_3367&pid_1961&mi_01"
        outgoing = prefix + b"&col07#7&receiver_a&0&0006#{hid-guid}"
        incoming = prefix + b"&col05#7&receiver_a&0&0004#{hid-guid}"
        other = prefix + b"&col05#7&receiver_b&0&0004#{hid-guid}"
        self.assertEqual(E.receiver_path(outgoing), E.receiver_path(incoming))
        self.assertNotEqual(E.receiver_path(outgoing), E.receiver_path(other))

    def poll(self, dev, infos=None):
        with patch.object(E, "hid") as hid, patch.object(E.hidlist, "enumerate", return_value=infos if infos is not None else [INFO, dict(INFO, usage_page=0xFF01, usage=0, path=b"receiver&col02")]):
            hid.device.return_value = dev
            return E.EndgameWEProvider().poll()

    def test_only_two_read_commands_and_correct_wire_bytes(self):
        dev = FakeDevice()
        result = self.poll(dev)
        self.assertEqual((result[0].level, result[0].charging, result[0].online), (75, False, True))
        self.assertEqual(dev.sent, [[8, 4] + [0] * 14 + [0x49],
                                    [8, 3] + [0] * 14 + [0x4A]])
        self.assertTrue(dev.closed)
        with self.assertRaises(ValueError):
            E.query_frame(7)  # EEPROM writes are not allowed

    def test_charging_survives_transient_link_drop(self):
        status = self.poll(FakeDevice(reply(4, [95, 1]), reply(3, [0])))[0]
        self.assertTrue(status.charging)
        self.assertTrue(status.online)

    def test_offline_receiver_does_not_present_cached_level_as_live(self):
        status = self.poll(FakeDevice(link=reply(3, [0])))[0]
        self.assertFalse(status.online)
        self.assertTrue(status.wake_on_input)

    def test_zero_is_a_valid_battery_level(self):
        self.assertEqual(self.poll(FakeDevice(reply(4, [0, 0])))[0].level, 0)

    def test_invalid_battery_and_timeout_are_not_zero(self):
        corrupt = reply(4, [75, 0])
        corrupt[-1] ^= 1
        for packet in ([], corrupt, reply(3, [75]), reply(4, [101, 0]),
                       [0] + reply(4, [75, 0])):
            with self.subTest(packet=packet):
                self.assertEqual(self.poll(FakeDevice(packet)), [])

    def test_unknown_or_missing_charging_keeps_level_without_claiming_status(self):
        for payload in ([75, 2], [75]):
            status = self.poll(FakeDevice(reply(4, payload)))[0]
            self.assertEqual(status.level, 75)
            self.assertFalse(status.charging_known)
            self.assertFalse(status.charging)

    def test_full_battery(self):
        self.assertEqual(self.poll(FakeDevice(reply(4, [100, 0])))[0].level, 100)

    def test_does_not_read_before_sending_first_query(self):
        dev = FakeDevice()
        read = dev.read
        def after_send(*args):
            if not dev.sent:
                raise OSError("read before request")
            return read(*args)
        dev.read = after_send
        self.assertEqual(self.poll(dev)[0].level, 75)

    def test_disappearing_device_does_not_escape_poll(self):
        dev = FakeDevice()
        dev.read = lambda *args: (_ for _ in ()).throw(OSError("unplugged"))
        self.assertEqual(self.poll(dev), [])
        self.assertTrue(dev.closed)

    def test_skips_unrelated_input_before_matching_reply(self):
        dev = FakeDevice()
        send = dev.send_feature_report
        def with_noise(data):
            count = send(data)
            dev.pending.insert(0, reply(0x0F, [1]))
            return count
        dev.send_feature_report = with_noise
        self.assertEqual(self.poll(dev)[0].level, 75)

    def test_other_collection_or_model_never_opened(self):
        for changes in ({"usage_page": 0xFF01}, {"usage": 0}, {"product_id": 0x1970}):
            dev = FakeDevice()
            self.assertEqual(self.poll(dev, [dict(INFO, **changes)]), [])
            self.assertEqual(dev.sent, [])

    def test_duplicate_collection_is_read_once(self):
        dev = FakeDevice()
        self.assertEqual(len(self.poll(dev, [INFO, INFO, dict(INFO, usage_page=0xFF01, usage=0, path=b"receiver&col02")])), 1)
        self.assertEqual(len(dev.sent), 2)

    def test_open_error_closes_handle(self):
        dev = FakeDevice()
        dev.open_path = lambda path: (_ for _ in ()).throw(OSError("busy"))
        self.assertEqual(self.poll(dev), [])
        self.assertTrue(dev.closed)

    def test_parser_rejects_bad_length_address_and_report_id(self):
        for index, value in ((0, 8), (3, 1), (5, 11)):
            data = reply(4, [75, 0])
            data[index] = value
            data[-1] = (0x55 - sum(data[:-1])) & 255
            self.assertIsNone(E.reply_payload(data, 4))

    def test_never_sends_without_matching_receiver_input_channel(self):
        dev = FakeDevice()
        other = dict(INFO, usage_page=0xFF01, usage=0, path=b"other&col02")
        self.assertEqual(self.poll(dev, [INFO, other]), [])
        self.assertEqual(dev.sent, [])

    def test_windows_uses_separate_send_and_receive_handles(self):
        sender = FakeDevice()
        reader = FakeDevice()
        sender.read = lambda *args: self.fail("feature-only channel cannot read input")
        reader.read = lambda *args: sender.pending.pop(0) if sender.pending else []
        reader.send_feature_report = lambda *args: self.fail("input channel cannot send feature")
        incoming = dict(INFO, usage_page=0xFF01, usage=0, path=b"receiver&col02")
        with patch.object(E, "hid") as hid, patch.object(E.hidlist, "enumerate", return_value=[INFO, incoming]):
            hid.device.side_effect = [sender, reader]
            result = E.EndgameWEProvider().poll()
        self.assertEqual(result[0].level, 75)
        self.assertEqual(sender.path, INFO["path"])
        self.assertEqual(reader.path, incoming["path"])
        self.assertTrue(sender.closed and reader.closed)
