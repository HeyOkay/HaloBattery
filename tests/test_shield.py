"""SHIELD response validation, noisy input, missing devices and separate pads."""
import unittest
from unittest.mock import patch

from providers import shield as S


def battery(percent=73):
    data = [3, 7, 0] + [0] * 62
    data[14] = percent
    return data


class Pad:
    def __init__(self, percent=73, charging=False, fail=False, serial=b"controller0001"):
        self.percent, self.charging, self.fail = percent, charging, fail
        self.queue, self.writes = [], []
        self.closed = False
        self.serial = serial

    def open_path(self, path):
        pass

    def write(self, data):
        self.writes.append(data)
        if self.fail:
            raise OSError('Disconnected')
        if data[1] == 7:
            reply = battery(self.percent)
        elif data[1] == 16:
            reply = [3, 16, 0, 0, 2] + list(self.serial) + [0] * 46
        else:
            reply = [3, 58, 0, int(self.charging), 2, 2 if self.charging else 1] + [0] * 59
        self.queue += [[1] + [0] * 64, reply]
        return len(data)

    def read(self, size, timeout):
        return self.queue.pop(0) if self.queue else []

    def close(self):
        self.closed = True


def info(serial='one', path=b'vid&00020955_pid&7214'):
    return dict(product_id=S.PID, serial_number=serial, path=path + b"#" + serial.encode())


class ShieldTests(unittest.TestCase):
    def test_rejects_invalid_or_unrelated_reports(self):
        self.assertEqual(S.parse_battery(battery(0)), 0)
        self.assertEqual(S.parse_battery(battery(100)[:33]), 100)
        for data in (battery(101), battery()[:14], [1] + battery()[1:],
                     [3, 7, 1] + battery()[3:]):
            self.assertIsNone(S.parse_battery(data))
        self.assertIsNone(S.parse_charger([3, 58, 0, 1, 2, 255] + [0] * 59))

    def test_queries_only_status_and_identity_and_skips_noise(self):
        pad = Pad(charging=True)
        with patch.object(S.hidlist, 'enumerate', return_value=[info()]), \
             patch.object(S.hid, 'device', return_value=pad):
            result = S.ShieldProvider().poll()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].level, 73)
        self.assertTrue(result[0].charging)
        self.assertTrue(result[0].charging_known)
        self.assertEqual(result[0].kind, 'gamepad')
        self.assertTrue(pad.closed)
        self.assertEqual(pad.writes, [[4, cmd, 0] + [0] * 30 for cmd in (7, 58, 16)])

    def test_disconnect_does_not_return_stale_percentage(self):
        provider = S.ShieldProvider()
        with patch.object(S.hidlist, 'enumerate', return_value=[info()]), \
             patch.object(S.hid, 'device', side_effect=[Pad(), Pad(fail=True)]):
            self.assertEqual(len(provider.poll()), 1)
            self.assertEqual(provider.poll(), [])

    def test_two_controllers_have_distinct_keys(self):
        with patch.object(S.hidlist, 'enumerate', return_value=[info('one'), info('two')]), \
             patch.object(S.hid, 'device', side_effect=[Pad(73), Pad(40, serial=b"controller0002")]):
            result = S.ShieldProvider().poll()
        self.assertEqual([s.level for s in result], [73, 40])
        self.assertNotEqual(result[0].key, result[1].key)

    def test_full_or_disconnected_charger_is_not_charging(self):
        for connected, state in ((1, 3), (0, 1), (0, 2)):
            data = [3, 58, 0, connected, 2, state] + [0] * 59
            self.assertFalse(S.parse_charger(data))

    def test_timeout_closes_handle_and_returns_no_reading(self):
        pad = Pad()
        with patch.object(S.hidlist, 'enumerate', return_value=[info()]), \
             patch.object(S.hid, 'device', return_value=pad), \
             patch.object(S.time, 'monotonic', side_effect=[0, 2]):
            self.assertEqual(S.ShieldProvider().poll(), [])
        self.assertTrue(pad.closed)

    def test_charger_failure_preserves_valid_battery_reading(self):
        pad = Pad()
        provider = S.ShieldProvider()
        with patch.object(S.hidlist, 'enumerate', return_value=[info()]), \
             patch.object(S.hid, 'device', return_value=pad), \
             patch.object(provider, '_query', side_effect=[battery(73), OSError('Timeout'), []]):
            result = provider.poll()
        self.assertEqual(result[0].level, 73)
        self.assertFalse(result[0].charging)
        self.assertFalse(result[0].charging_known)
        self.assertTrue(pad.closed)

    def test_explicit_unknown_charger_state(self):
        self.assertIsNone(S.parse_charger([3, 58, 0, 1, 2, 0] + [0] * 59))

    def test_missing_charger_reply_preserves_percentage_as_unknown(self):
        provider = S.ShieldProvider()
        with patch.object(S.hidlist, "enumerate", return_value=[info()]), \
             patch.object(S.hid, "device", return_value=Pad()), \
             patch.object(provider, "_query", side_effect=[battery(19), [], []]):
            result = provider.poll()
        self.assertEqual(result[0].level, 19)
        self.assertFalse(result[0].charging_known)

    def test_does_not_touch_unrelated_nvidia_devices(self):
        devices = [dict(product_id=123, path=b'other')]
        with patch.object(S.hidlist, 'enumerate', return_value=devices), \
             patch.object(S.hid, 'device') as device:
            self.assertEqual(S.ShieldProvider().poll(), [])
            device.assert_not_called()

    def test_usb_battery_and_charging(self):
        with patch.object(S.hidlist, "enumerate", return_value=[info(path=b"vid_0955&pid_7214")]), \
             patch.object(S.hid, "device", return_value=Pad(65, charging=True)):
            result = S.ShieldProvider().poll()
        self.assertEqual(result[0].level, 65)
        self.assertTrue(result[0].charging)
        self.assertEqual(result[0].via, "")

    def test_same_controller_keeps_key_across_usb_and_bluetooth(self):
        provider = S.ShieldProvider()
        with patch.object(S.hidlist, "enumerate", side_effect=[[info()], [info(path=b"vid_0955&pid_7214")]]), \
             patch.object(S.hid, "device", side_effect=[Pad(), Pad(charging=True)]):
            bt = provider.poll()[0]
            usb = provider.poll()[0]
        self.assertEqual(bt.key, usb.key)

    def test_duplicate_transports_prefer_usb_in_either_order(self):
        bt, usb = info(), info(path=b"vid_0955&pid_7214")
        for entries in ([bt, usb], [usb, bt]):
            with self.subTest(entries=entries), \
                 patch.object(S.hidlist, "enumerate", return_value=entries), \
                 patch.object(S.hid, "device", side_effect=[Pad(), Pad()]):
                result = S.ShieldProvider().poll()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].via, "")

    def test_missing_board_serial_uses_distinct_interface_fallbacks(self):
        usb = [info(serial="", path=b"vid_0955&pid_7214#one"),
               info(serial="", path=b"vid_0955&pid_7214#two")]
        with patch.object(S.hidlist, "enumerate", return_value=usb), \
             patch.object(S.hid, "device", side_effect=[Pad(serial=bytes(14)), Pad(serial=bytes(14))]):
            result = S.ShieldProvider().poll()
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0].key, result[1].key)

    def test_swapping_usb_pads_at_same_path_does_not_reuse_identity(self):
        provider = S.ShieldProvider()
        with patch.object(S.hidlist, "enumerate", return_value=[info(path=b"vid_0955&pid_7214")]), \
             patch.object(S.hid, "device", side_effect=[Pad(), Pad(serial=b"controller0002")]):
            first = provider.poll()[0]
            second = provider.poll()[0]
        self.assertNotEqual(first.key, second.key)

    def test_rejects_empty_or_unrelated_board_identity(self):
        self.assertIsNone(S.parse_serial([3, 16, 0] + [0] * 62))
        self.assertIsNone(S.parse_serial(battery()))
        self.assertIsNone(S.parse_serial([3, 16, 0, 0, 2, 1]))


if __name__ == '__main__':
    unittest.main()
