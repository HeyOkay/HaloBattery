"""Status-only XM2w reader based on Vole PROTOCOL.md sections 1-3 at
4ba29dea54b4628df03cfe2f94f4576c124afd80 and mouse-battery-monitor
ENDGAME.md at 25871cce3a8266d8d2bc05b0ca81fbb74b3a38b4.
See docs/protocols.md for sources and model-specific evidence.

No configuration/blob, pairing, firmware, or WE-family commands are allowed.
"""
import threading
import time

VID = 0x3367
MODELS = {0x1968: 'XM2w 4k', 0x1982: 'XM2w 4k v2'}
RECEIVER = 0x1970


class ProtocolError(Exception):
    pass


class DeviceStatusError(ProtocolError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'device status 0x{status:02x}; payload not used')


def frame(command, target=0):
    if (command, target) not in {(0x0E, 0), (0xB4, 0)}:
        raise ValueError('command/target outside status-only allowlist')
    return bytes([0xA1, command, target] + [0] * 61)


def response(data):
    try:
        data = bytes(data)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProtocolError('invalid response bytes') from exc
    # Janlhin's 2026-11-05 Windows logs contain both 63 and 64 bytes WITH A1.
    # Accept only these observed lengths; never invent an ID or pad missing data.
    # Preserve the explicit report ID; do not infer one from a short buffer.
    if len(data) not in (63, 64) or data[0] != 0xA1:
        raise ProtocolError('expected observed 63/64-byte report with explicit 0xA1')
    if data[1] != 1:
        raise DeviceStatusError(data[1])
    return data


def identity(data):
    data = response(data)
    vid = int.from_bytes(data[16:18], 'little')
    pid = int.from_bytes(data[18:20], 'little')
    if vid != VID or pid not in MODELS:
        raise ProtocolError('unrecognized/held mouse identity; battery query refused')
    return {'model': MODELS[pid], 'mouse_pid': f'{pid:04x}',
            'firmware_bytes': data[22:24].hex(' ')}


def battery(data, wired):
    data = response(data)
    value = data[16]
    # No explicit charging flag is decoded here; voltage does not calibrate capacity.
    if not wired and value > 100:
        raise ProtocolError('wireless percentage outside 0..100')
    return {'raw_battery_byte': value,
            'percentage': None if wired else value,
            'charging': None,
            'charging_basis': 'unknown; no explicit flag decoded',
            'wired_presentation_hint': ('full' if value >= 100 else 'charging') if wired else None,
            'hint_basis': 'vendor wired presentation rule, not a measured charging flag',
            'undecoded_bytes': data[17:19].hex(' ')}


def candidates(inventory):
    """Only the named family's config collection, never WE or an FF02 input."""
    result, seen = [], set()
    for info in inventory:
        if (info.get('vendor_id') != VID or
                info.get('product_id') not in {*MODELS, RECEIVER} or
                (info.get('usage_page'), info.get('usage')) != (0xFF01, 2)):
            continue
        path = info.get('path')
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(info)
    return result


class Reader:
    def __init__(self, device, sleep=time.sleep):
        self.device, self.sleep = device, sleep
        self.events = []
        self.lock = threading.Lock()

    def exchange(self, command, target=0):
        request = frame(command, target)
        with self.lock:
            event = {'command': f'{command:02x}', 'target': target,
                     'request': request.hex(' '), 'replies': []}
            self.events.append(event)
            if self.device.send_feature_report(request) != len(request):
                raise ProtocolError('feature send failed/short; no read attempted')
            self.sleep(0.17)
            # Read the held response again on busy; do not pipeline/resend.
            for attempt in range(4):
                raw = bytes(self.device.get_feature_report(0xA1, 64))
                event['replies'].append(raw.hex(' '))
                try:
                    # No sourced command/target echo: success does not prove freshness.
                    return response(raw)
                except DeviceStatusError as exc:
                    if exc.status != 3 or attempt == 3:
                        raise
                    self.sleep(0.2 * (attempt + 1))
        raise ProtocolError('unreachable')
