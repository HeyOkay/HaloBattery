"""Synapse에서 캡처한 HID 보고서로 Hammerhead 수신기 경로를 검증한다."""

from functools import reduce
from operator import xor
from unittest import mock

import test_hide_rename as tray_test
from providers import hammerhead_v3x as H, razer as R


def frame(prefix, suffix):
    # 체크섬을 포함한 실기 벡터는 provider의 생성 코드와 독립적으로 둔다.
    head = bytes.fromhex(prefix)
    return head + bytes(62 - len(head)) + bytes.fromhex(suffix)


LINK_QUERY = frame("02 00 02 00 00 00 01 18 9a 01", "82 00")
LINK_REPLY = frame("02 02 02 00 00 00 03 18 9a 01 03 00", "83 00 00")
LEVEL_QUERY = frame("02 00 03 00 00 00 00 07 80", "87 00")
LEVEL_REPLY = frame("02 02 03 00 00 00 02 07 80 5e 5e", "85 00 01")


def changed_reply(reply, offset, *values):
    # 다른 정상 값이나 잘못된 헤더도 체크섬 검사를 통과하도록 다시 계산한다.
    result = bytearray(reply)
    result[offset:offset + len(values)] = values
    result[62] = reduce(xor, result[3:62], 0)
    return bytes(result)


class HammerheadV3XTests(tray_test.HideRenameTestCase):
    def setUp(self):
        super().setUp()
        # 실제 RazerProvider 진입점을 사용하되 HID 열기와 시간 지연을 대체한다.
        self.device = mock.Mock()
        self.device.send_feature_report.return_value = 64
        self.device.get_feature_report.side_effect = (LINK_REPLY, LEVEL_REPLY)
        command = {"product_id": H.PID, "serial_number": "test", "interface_number": 0,
                   "usage_page": 0xFF82, "usage": 1, "path": b"command"}
        self.enumerate = self.enterContext(mock.patch.object(R.hidlist, "enumerate", return_value=[
            dict(command, usage_page=0x000C, path=b"consumer"),
            dict(command, usage_page=0xFF90, path=b"other vendor"),
            command,
        ]))
        self.open_device = self.enterContext(mock.patch.object(H.hid, "device", return_value=self.device))

    def test_reports_captured_battery_from_the_exact_collection(self):
        [status] = R.RazerProvider().poll()
        self.assertEqual((status.name, status.level, status.kind), (H.NAME, 94, "headset"))
        self.assertEqual((status.key, status.source), ("razer:0a64:test", "razer"))
        self.assertEqual((status.charging, status.charging_known), (False, True))
        self.device.open_path.assert_called_once_with(b"command")
        self.device.close.assert_called_once_with()
        self.assertEqual(self.device.send_feature_report.call_args_list,
                         [mock.call(LINK_QUERY), mock.call(LEVEL_QUERY)])
        self.assertEqual(self.device.get_feature_report.call_args_list, [mock.call(2, 65)] * 2)

    def test_connected_earbuds_show_a_normal_tooltip_and_low_battery_alert(self):
        # 수신기의 잔량을 실제 트레이와 알림 처리 흐름에 넣는다.
        self.device.get_feature_report.side_effect = (LINK_REPLY, changed_reply(LEVEL_REPLY, 9, 19, 84))
        app = tray_test.make_app({"language": "en", "low": 20})
        app.apply(R.RazerProvider().poll())
        self.assertEqual(app.icons["razer:0a64:test"].titles[-1], f"{H.NAME}: 19%")
        self.assertEqual(app.notes, [f"{H.NAME}: 19% left. Time to charge."])

    def test_does_not_open_other_collections(self):
        self.enumerate.return_value.pop()
        self.assertEqual(R.RazerProvider().poll(), [])
        self.open_device.assert_not_called()

    def test_uses_only_connected_earbuds_and_accepts_zero_and_full(self):
        # FF는 잔량 미제공 값이다. 허용 범위를 벗어난 실제 값은 표시하지 않는다.
        for link, left, right, expected in (
            (3, 90, 82, 82), (1, 100, 15, 100), (2, 15, 0, 0),
            (3, 0xFF, 57, 57), (3, 0xFF, 0xFF, None), (3, 101, 57, None),
        ):
            with self.subTest(link=link, left=left, right=right):
                self.device.get_feature_report.side_effect = (
                    changed_reply(LINK_REPLY, 10, link), changed_reply(LEVEL_REPLY, 9, left, right))
                statuses = R.RazerProvider().poll()
                self.assertEqual([s.level for s in statuses], [] if expected is None else [expected])

    def test_disconnected_does_not_request_battery_or_keep_a_level(self):
        provider = R.RazerProvider()
        self.assertEqual(len(provider.poll()), 1)
        self.device.send_feature_report.reset_mock()
        self.device.get_feature_report.side_effect = (changed_reply(LINK_REPLY, 10, 0),)
        self.assertEqual(provider.poll(), [])
        self.device.send_feature_report.assert_called_once_with(LINK_QUERY)

    def test_refuses_short_or_unrelated_replies(self):
        # 정상 링크 뒤에도 식별자, 상태, 길이 또는 체크섬이 틀린 잔량을 무시한다.
        for reply in (None, b"\x02", LEVEL_REPLY[:63], bytes(65),
                      changed_reply(LEVEL_REPLY, 1, 1), changed_reply(LEVEL_REPLY, 2, 4),
                      changed_reply(LEVEL_REPLY, 6, 1), changed_reply(LEVEL_REPLY, 8, 0x83),
                      LEVEL_REPLY[:62] + b"\x00\x00\x00"):
            with self.subTest(reply=reply):
                self.device.get_feature_report.side_effect = (LINK_REPLY, reply, reply, reply)
                self.assertEqual(R.RazerProvider().poll(), [])

    def test_retries_a_busy_read_without_resending_the_command(self):
        self.device.get_feature_report.side_effect = (
            changed_reply(LINK_REPLY, 1, 1), LINK_REPLY, LEVEL_REPLY[:64])
        self.assertEqual([s.level for s in R.RazerProvider().poll()], [94])
        self.assertEqual(self.device.send_feature_report.call_args_list,
                         [mock.call(LINK_QUERY), mock.call(LEVEL_QUERY)])

    def test_device_removed_mid_poll_closes_handle(self):
        self.device.get_feature_report.side_effect = (LINK_REPLY, OSError("receiver removed"))
        self.assertEqual(R.RazerProvider().poll(), [])
        self.device.close.assert_called_once_with()
