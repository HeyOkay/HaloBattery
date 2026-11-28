"""Synapse에서 캡처한 HID 보고서로 Hammerhead 수신기 경로를 검증한다."""

from functools import reduce
from itertools import chain, repeat
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
# 출처: Synapse 4 --logProtocol의 protocol.log, 2026-11-25 14:55:59.450 응답.
# 좌/우 0x5E는 각각 94%이며, PR #579의 93%는 Synapse 종료 후 별도로 측정한 값이다.
LEVEL_REPLY = frame("02 02 03 00 00 00 02 07 80 5e 5e", "85 00 01")


def changed_reply(reply, offset, *values):
    # 다른 정상 값이나 잘못된 헤더도 체크섬 검사를 통과하도록 다시 계산한다.
    result = bytearray(reply)
    result[offset:offset + len(values)] = values
    result[62] = reduce(xor, result[3:62], 0)
    return bytes(result)


class ContendedReceiver:
    # 첫 요청의 응답이 덮어써져, 동일한 요청을 다시 보내야 응답하는 수신기를 재현한다.
    def __init__(self, answer_on=2, foreign_offset=2, foreign_value=0x7F):
        self.answer_on = answer_on
        self.foreign_offset = foreign_offset
        self.foreign_value = foreign_value
        self.requests = []

    def send_feature_report(self, request):
        # 연결 조회와 잔량 조회의 재전송 횟수를 각각 기록한다.
        self.requests.append(request)
        return len(request)

    def get_feature_report(self, report_id, size):
        # 재전송 전에는 checksum이 유효한 다른 요청의 응답만 반환한다.
        request = self.requests[-1]
        reply = LINK_REPLY if request == LINK_QUERY else LEVEL_REPLY
        if self.answer_on and self.requests.count(request) >= self.answer_on:
            return reply
        return changed_reply(reply, self.foreign_offset, self.foreign_value)


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
                # 다른 요청의 응답이 계속 오더라도 유효한 잔량으로 받아들이지 않는다.
                self.device.get_feature_report.side_effect = chain((LINK_REPLY,), repeat(reply))
                self.assertEqual(R.RazerProvider().poll(), [])

    def test_retries_a_busy_read_without_resending_the_command(self):
        self.device.get_feature_report.side_effect = (
            changed_reply(LINK_REPLY, 1, 1), LINK_REPLY, LEVEL_REPLY[:64])
        self.assertEqual([s.level for s in R.RazerProvider().poll()], [94])
        self.assertEqual(self.device.send_feature_report.call_args_list,
                         [mock.call(LINK_QUERY), mock.call(LEVEL_QUERY)])

    def test_resends_after_three_foreign_replies(self):
        # transaction, command class, command id가 다른 경우를 두 조회에서 모두 검증한다.
        for offset, value in ((2, 0x7F), (7, 0x0F), (8, 0x03)):
            with self.subTest(offset=offset):
                receiver = ContendedReceiver(foreign_offset=offset, foreign_value=value)
                self.device.send_feature_report.side_effect = receiver.send_feature_report
                self.device.get_feature_report.side_effect = receiver.get_feature_report
                self.device.get_feature_report.reset_mock()
                self.assertEqual([s.level for s in R.RazerProvider().poll()], [94])
                self.assertEqual(receiver.requests, [LINK_QUERY] * 2 + [LEVEL_QUERY] * 2)
                self.assertEqual(self.device.get_feature_report.call_count, 8)

    def test_accepts_a_reply_to_the_last_allowed_send(self):
        # 마지막 허용 재전송에 응답해도 두 조회가 모두 성공해야 한다.
        receiver = ContendedReceiver(answer_on=3)
        self.device.send_feature_report.side_effect = receiver.send_feature_report
        self.device.get_feature_report.side_effect = receiver.get_feature_report
        self.assertEqual([s.level for s in R.RazerProvider().poll()], [94])
        self.assertEqual(receiver.requests, [LINK_QUERY] * 3 + [LEVEL_QUERY] * 3)
        self.assertEqual(self.device.get_feature_report.call_count, 14)

    def test_foreign_replies_cannot_cause_unbounded_resends(self):
        # 다른 앱이 계속 응답 버퍼를 덮어쓰더라도 전송 세 번, 읽기 아홉 번에서 끝낸다.
        receiver = ContendedReceiver(answer_on=0)
        self.device.send_feature_report.side_effect = receiver.send_feature_report
        self.device.get_feature_report.side_effect = receiver.get_feature_report
        self.assertEqual(R.RazerProvider().poll(), [])
        self.assertEqual(receiver.requests, [LINK_QUERY] * 3)
        self.assertEqual(self.device.get_feature_report.call_count, 9)
        self.device.close.assert_called_once_with()

    def test_busy_or_invalid_replies_do_not_cause_resends(self):
        # busy, 무응답, 짧은 패킷, 길이 오류, checksum 오류는 경합의 증거가 아니다.
        for reply in (changed_reply(LINK_REPLY, 1, 1), None, LINK_REPLY[:63],
                      changed_reply(LINK_REPLY, 6, 1), LINK_REPLY[:62] + b"\x00\x00\x00"):
            with self.subTest(reply=reply):
                self.device.send_feature_report.reset_mock()
                self.device.get_feature_report.reset_mock()
                self.device.get_feature_report.side_effect = repeat(reply)
                self.assertEqual(R.RazerProvider().poll(), [])
                self.device.send_feature_report.assert_called_once_with(LINK_QUERY)
                self.assertEqual(self.device.get_feature_report.call_count, 3)

    def test_short_write_stops_initial_send_or_resend(self):
        # 짧은 쓰기는 즉시 중단하며, 실패한 재전송 뒤에 추가 읽기를 하지 않는다.
        for sends, reads in (((63,), 0), ((64, 63), 3)):
            with self.subTest(sends=sends):
                self.device.send_feature_report.reset_mock()
                self.device.get_feature_report.reset_mock()
                self.device.send_feature_report.side_effect = sends
                self.device.get_feature_report.side_effect = repeat(changed_reply(LINK_REPLY, 2, 4))
                self.assertEqual(R.RazerProvider().poll(), [])
                self.assertEqual(self.device.send_feature_report.call_args_list,
                                 [mock.call(LINK_QUERY)] * len(sends))
                self.assertEqual(self.device.get_feature_report.call_count, reads)

    def test_device_removed_during_resend_closes_handle(self):
        # 재전송 중 장치가 사라져도 예외를 처리하고 핸들을 닫는다.
        self.device.send_feature_report.side_effect = (64, OSError("receiver removed"))
        self.device.get_feature_report.side_effect = repeat(changed_reply(LINK_REPLY, 2, 4))
        self.assertEqual(R.RazerProvider().poll(), [])
        self.assertEqual(self.device.send_feature_report.call_count, 2)
        self.assertEqual(self.device.get_feature_report.call_count, 3)
        self.device.close.assert_called_once_with()

    def test_device_removed_mid_poll_closes_handle(self):
        self.device.get_feature_report.side_effect = (LINK_REPLY, OSError("receiver removed"))
        self.assertEqual(R.RazerProvider().poll(), [])
        self.device.close.assert_called_once_with()
