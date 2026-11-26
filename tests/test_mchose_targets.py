"""Sourced 3837 mouse targets, independent literal identities and wire bytes."""
from types import SimpleNamespace
import unittest
from unittest import mock

from providers import mchose as M


def descriptor(pid, path, interface=2, page=0xFF01, usage=0x0001, vid=0x3837):
    return dict(vendor_id=vid, product_id=pid, path=path,
                interface_number=interface, usage_page=page, usage=usage,
                product_string='MCHOSE test device')


class MchoseTargetTests(unittest.TestCase):
    def setUp(self):
        self.opened, self.sent, self.closed = [], [], []
        self.reply = bytes.fromhex(
            '11 f9 c8 c7 0f ff fd fa f8 ff f6 bb ff bb ff ff ff ff ff ff ff')
        test = self

        class Device:
            def open_path(self, path):
                # Every valid fixture has one explicitly permitted path.
                if path != b'allowed':
                    raise AssertionError('opened an unrelated collection: ' + repr(path))
                self.path = path
                test.opened.append(path)

            def send_feature_report(self, data):
                test.sent.append((self.path, bytes(data)))
                return len(data)

            def get_feature_report(self, report, length):
                # Literal documented status layout: VID3837, model00F0, 68%,
                # not charging. No provider offsets/constants build this reply.
                return list(test.reply)

            def close(self):
                test.closed.append(self.path)

        self.device_factory = mock.Mock(side_effect=Device)
        self.patch = mock.patch.object(M, 'hid', SimpleNamespace(device=self.device_factory))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        timer = mock.patch.object(M, 'time', SimpleNamespace(time=lambda: 1000.0, sleep=lambda _: None))
        timer.start()
        self.addCleanup(timer.stop)
        self.provider = M.MchoseProvider()

    def poll(self, entries):
        with mock.patch.object(M.hidlist, 'enumerate',
                side_effect=lambda vid=0: [d for d in entries if d['vendor_id'] == vid]):
            return self.provider.poll()

    def test_keyboard_v7_and_unknown_3837_identities_are_never_opened(self):
        fixtures = [
            # Exact unrelated keyboard/mouse collections in #189.
            descriptor(0x3033, b'keyboard-ff70', 3, 0xFF70, 0x0071),
            descriptor(0x3033, b'keyboard-ff31', 2, 0xFF31, 0x0074),
            descriptor(0x1016, b'v7-config'),
            descriptor(0x1016, b'v7-ff60', 3, 0xFF60, 0x0061),
            descriptor(0x9999, b'unknown-model'),
        ]
        self.assertEqual(self.poll(fixtures), [])
        self.device_factory.assert_not_called()
        held = '\n'.join(self.provider.diagnostics())
        self.assertIn('pid=3033', held)
        self.assertIn('pid=1016', held)
        self.assertIn('pid=9999', held)
        self.assertIn('query held', held)

    def test_known_pid_with_wrong_usage_or_interface_has_no_vendor_fallback(self):
        fixtures = [
            descriptor(0x100B, b'wrong-interface', 1),
            descriptor(0x100B, b'wrong-usage', 2, 0xFF01, 0x0002),
            descriptor(0x100B, b'sibling-ff0b', 2, 0xFF0B, 0x0104),
            descriptor(0x100B, b'wrong-ff70', 3, 0xFF70, 0x0071),
            descriptor(0x100B, b'wrong-ff31', 2, 0xFF31, 0x0074),
            descriptor(0x100A, b'pro-wrong-usage', 2, 0xFF01, 0x0002),
            descriptor(0x100A, b'pro-sibling-ff0b', 2, 0xFF0B, 0x0104),
            descriptor(0x4018, b'wired-wrong-usage', 2, 0xFF01, 0x0002),
        ]
        self.assertEqual(self.poll(fixtures), [])
        self.device_factory.assert_not_called()
        self.assertIn('no sourced control collection', '\n'.join(self.provider.diagnostics()))

    def test_exact_100b_collection_gets_only_the_existing_status_frame(self):
        fixtures = [
            descriptor(0x100B, b'keyboard', 0, 0x0001, 0x0006),
            descriptor(0x100B, b'sibling-ff0b', 2, 0xFF0B, 0x0104),
            descriptor(0x100B, b'wrong-ff31', 2, 0xFF31, 0x0074),
            descriptor(0x100B, b'allowed'),
        ]
        result = self.poll(fixtures)
        self.assertEqual([(s.key, s.level, s.charging, s.online) for s in result],
                         [('mchose:3837', 68, False, True)])
        self.assertEqual(self.opened, [b'allowed'])
        self.assertEqual(self.closed, [b'allowed'])
        self.assertEqual(self.sent, [(b'allowed', b'\x11\xf9' + b'\xff' * 19)])

    def test_the_a7_v2_pro_answers_the_same_status_frame(self):
        # Measured on one unit: 3837:100A, interface 2, FF01:0001, 0x06 on report 0x11.
        result = self.poll([descriptor(0x100A, b'pro-sibling-ff0b', 2, 0xFF0B, 0x0104),
                            descriptor(0x100A, b'allowed')])
        self.assertEqual([(s.key, s.level, s.charging, s.online) for s in result],
                         [('mchose:3837', 68, False, True)])
        self.assertEqual(self.opened, [b'allowed'])
        self.assertEqual(self.sent, [(b'allowed', b'\x11\xf9' + b'\xff' * 19)])

    def test_the_a7_v2_pro_on_its_cable_reports_charging(self):
        # Measured while charging: 3837:4018 answers 85 %, charging, on 0x11.
        self.reply = bytes.fromhex(
            '11 f9 c8 c7 e7 bf fa d5 fd fb ff aa fe aa ff ff')
        result = self.poll([descriptor(0x4018, b'allowed')])
        self.assertEqual([(s.key, s.level, s.charging) for s in result],
                         [('mchose:3837', 85, True)])
        self.assertEqual(self.sent, [(b'allowed', b'\x11\xf9' + b'\xff' * 19)])

    def test_the_dongle_and_the_cable_share_one_icon(self):
        # On the cable the receiver goes quiet; only the mouse answers, and both
        # identities carry the same family key.
        entries = [descriptor(0x100A, b'dongle'), descriptor(0x4018, b'cable')]

        def read(path):
            return None if path == b'dongle' else (85, 1, 0x4018, 0)

        with mock.patch.object(self.provider, '_read_collection', side_effect=read):
            result = self.poll(entries)
        self.assertEqual([(s.key, s.level, s.charging, s.online) for s in result],
                         [('mchose:3837', 85, True, True)])
        self.device_factory.assert_not_called()

    def test_a_device_that_vanishes_mid_read_does_not_hide_the_others(self):
        # Unplugging one MCHOSE device used to raise out of poll() and trip the
        # provider's circuit, greying out every MCHOSE icon until a later poll.
        entries = [descriptor(0x100A, b'gone'), descriptor(0x1020, b'other', vid=0x5253)]

        def read(path):
            if path == b'gone':
                raise OSError(1167, 'The device is not connected.')
            return (68, False, 0x0031, 9)

        with mock.patch.object(self.provider, '_read_collection', side_effect=read):
            result = self.poll(entries)
        self.assertEqual([(s.key, s.level) for s in result], [('mchose', 68)])
        self.assertIn('OSError', '\n'.join(self.provider.diagnostics()))

    def test_other_3837_products_do_not_hide_the_sourced_mouse(self):
        result = self.poll([descriptor(0x3033, b'keyboard-ff70', 3, 0xFF70, 0x0071),
                            descriptor(0x1016, b'v7-config'), descriptor(0x100B, b'allowed')])
        self.assertEqual([s.level for s in result], [68])
        self.assertEqual(self.opened, [b'allowed'])

    def test_5253_family_still_uses_its_existing_path(self):
        self.reply = bytes.fromhex(
            '11 f9 ac ad ce ff fd fa f8 ff f6 bb ff bb ff ff ff ff ff ff ff')
        result = self.poll([descriptor(0x1020, b'allowed', vid=0x5253)])
        self.assertEqual([s.level for s in result], [68])
        self.assertEqual(self.opened, [b'allowed'])

    def test_confirmed_3837_headset_pair_keeps_its_separate_reader(self):
        entries = [descriptor(0x6008, b'dongle', 0, 0xFF00, 0x0001),
                   descriptor(0x600A, b'headset', 0, 0xFF90, 0x0001)]
        with mock.patch.object(self.provider, '_read_headset', return_value=(90, False)) as audio, \
                mock.patch.object(self.provider, '_read_collection',
                                  side_effect=AssertionError('audio received mouse frame')):
            result = self.poll(entries)
        self.assertEqual(audio.call_args_list, [mock.call(b'dongle', 0x6008), mock.call(b'headset', 0x600A)])
        self.assertEqual([(s.key, s.level, s.kind) for s in result],
                         [('mchose:3837:6008', 90, 'headset')])
        self.device_factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
