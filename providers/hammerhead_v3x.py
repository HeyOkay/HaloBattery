"""Hammerhead V3 X HyperSpeed의 2.4 GHz 수신기(1532:0A64).

2026-11-25 Synapse 4 `--logProtocol` 실기 캡처에서 interface 0의
`ff82:0001`(Col04)만 Feature Report 0x02를 처리했다.
Razer 미들웨어의 `getEarbudsConnectionStatus`와 `getBatteryLevel`을 참고했다.
https://apps.razer.com/synapse/products/2660/mw/main.68a2d0ed664661d98c57.js
"""
from __future__ import annotations

import time

import hid


PID = 0x0A64
NAME = "Razer Hammerhead V3 X HyperSpeed"
REPORT_ID = 0x02
REPORT_SIZE = 64
CHECKSUM_INDEX = 62
LINK = (0x18, 0x9A)
BATTERY = (0x07, 0x80)
MAX_SENDS = 3  # 다른 요청의 정상 응답이 반복될 때만 같은 조회를 최대 세 번 전송한다.


def _checksum(frame: bytes | bytearray) -> int:
    # Report ID, 상태, transaction ID 뒤부터 checksum 앞까지 XOR한다.
    result = 0
    for value in frame[3:CHECKSUM_INDEX]:
        result ^= value
    return result


def _is_foreign_reply(reply: bytes, tid: int, command: tuple[int, int]) -> bool:
    # 손상되거나 busy인 응답과 구분해, 다른 transaction 또는 명령의 정상 응답만 찾는다.
    return (len(reply) in (REPORT_SIZE, REPORT_SIZE + 1)
            and reply[:2] == bytes((REPORT_ID, 0x02))
            and reply[CHECKSUM_INDEX] == _checksum(reply)
            and (reply[2] != tid or reply[7:9] != bytes(command)))


def _query(dev, tid: int, command: tuple[int, int], args: bytes, reply_size: int) -> bytes | None:
    # 64바이트 SET에 이어 같은 report ID로 GET하고, 요청과 짝이 맞는 응답만 읽는다.
    request = bytearray(REPORT_SIZE)
    request[0] = REPORT_ID
    request[2] = tid
    request[6:9] = (len(args), *command)
    request[9:9 + len(args)] = args
    request[CHECKSUM_INDEX] = _checksum(request)
    header = bytes((REPORT_ID, 0x02, tid))
    command_header = bytes((reply_size, *command))
    for _ in range(MAX_SENDS):
        # 기존 Razer 경로처럼 응답 버퍼 경합 시 캡처된 동일한 읽기 요청만 재전송한다.
        if dev.send_feature_report(bytes(request)) < REPORT_SIZE:
            return None
        foreign = False
        for _ in range(3):
            time.sleep(0.11)  # Synapse 캡처에서 SET과 GET 사이 약 100 ms를 기다렸다.
            reply = bytes(dev.get_feature_report(REPORT_ID, REPORT_SIZE + 1) or ())
            if (len(reply) in (REPORT_SIZE, REPORT_SIZE + 1)
                    and reply[:3] == header and reply[6:9] == command_header
                    and reply[CHECKSUM_INDEX] == _checksum(reply)):
                return reply[9:9 + reply_size]
            foreign = foreign or _is_foreign_reply(reply, tid, command)
        if not foreign:
            return None  # 무응답, busy 또는 손상된 응답만으로는 다시 전송하지 않는다.
    return None


def poll_level(ifaces: list[dict], diag: list[str]) -> int | None:
    # Synapse에서 응답한 수신기 컬렉션만 열고 연결 상태를 배터리보다 먼저 조회한다.
    path = next((d["path"] for d in ifaces if d.get("interface_number") == 0
                 and d.get("usage_page") == 0xFF82 and d.get("usage") == 1), None)
    if path is None:
        return None
    dev = hid.device()
    try:
        dev.open_path(path)
        link = _query(dev, 0x02, LINK, b"\x01", 3)
        if link is None or link[0] != 1 or link[1] not in (1, 2, 3):
            return None
        levels = _query(dev, 0x03, BATTERY, b"", 2)
    except (OSError, ValueError) as exc:
        diag.append(f"  Hammerhead HID: {exc}")
        return None
    finally:
        dev.close()
    if levels is None:
        return None
    # 연결 비트 0/1은 좌/우에 대응한다. 0xFF는 해당 이어폰의 잔량이 없다는 뜻이다.
    active = [level for index, level in enumerate(levels)
              if link[1] & (1 << index) and level != 0xFF]
    if not active or max(active) > 100:
        return None
    level = min(active)
    diag.append(f"  Hammerhead earbuds: link={link[1]:02x}, level={level}%")
    return level
