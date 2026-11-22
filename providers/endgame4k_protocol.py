"""Held XM2w A1 status exchange, not the XM2we 17-byte protocol.

Sources: johanneszab/vole re/PROTOCOL.md sections 1-3 at
4ba29dea54b4628df03cfe2f94f4576c124afd80:
https://github.com/johanneszab/vole/blob/4ba29dea54b4628df03cfe2f94f4576c124afd80/re/PROTOCOL.md
and stuffz/mouse-battery-monitor docs/ENDGAME.md at
25871cce3a8266d8d2bc05b0ca81fbb74b3a38b4.
Only 0E identity and B4 battery are allowed; no probe or recovery sequence.
Success has no sourced command/target echo. Agreement cannot prove freshness.
"""
import time

VID, RECEIVER = 0x3367, 0x1970
MODELS = {0x1968: "XM2w 4k", 0x1982: "XM2w 4k v2"}
REPORT, REPORT_LEN = 0xA1, 64
IDENTITY, BATTERY = 0x0E, 0xB4
# Pacing policy: the B4 reference waits 350 ms; Vole documents command sleeps
# of 150-360 ms. This is not a guarantee of response freshness or recovery.
COMMAND_WAIT = 0.350
BUSY_READS = 4
TRANSACTION_BUDGET = 6.0


class ProtocolError(ValueError):
    pass


class StatusError(ProtocolError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"device status 0x{status:02x}; payload not used")


def request(command, target=0):
    if (type(command) is not int or type(target) is not int
            or command not in (IDENTITY, BATTERY) or target != 0):
        raise ValueError("only identity/battery requests with target zero are allowed")
    return bytes((REPORT, command, 0, 0)) + bytes(60)


def response(data):
    # Both lengths including explicit A1 are observed layouts; never pad or
    # insert an ID. Status validation cannot correlate a successful response.
    if (not isinstance(data, (bytes, bytearray, list, tuple)) or len(data) not in (63, 64)
            or any(type(byte) is not int or not 0 <= byte <= 255 for byte in data)
            or data[0] != REPORT):
        raise ProtocolError("expected explicit A1 report of 63 or 64 bytes")
    data = bytes(data)
    if data[1] != 1:
        raise StatusError(data[1])
    return data


def identity(data):
    data = response(data)
    vid = int.from_bytes(data[16:18], "little")
    pid = int.from_bytes(data[18:20], "little")
    if vid != VID or pid not in MODELS:
        raise ProtocolError("unsupported mouse identity; battery not requested")
    return pid, data[22:24].hex(" ")


def battery(data):
    level = response(data)[16]
    if not 0 <= level <= 100:
        raise ProtocolError("battery byte outside 0..100")
    return level


class Reader:
    """One sequential owner with bounded busy reads and a transaction budget."""
    def __init__(self, device, *, clock=None, sleep=None):
        self.device = device
        self.clock = clock if clock is not None else time.monotonic
        self.sleep = sleep if sleep is not None else time.sleep
        self.deadline = self.clock() + TRANSACTION_BUDGET
        self.command = None
        self.reply_count = 0
        self.last_length = 0
        self.last_status = None

    def _check(self):
        if self.clock() >= self.deadline:
            raise ProtocolError("status transaction budget exhausted")

    def _wait(self, delay):
        if self.clock() + delay >= self.deadline:
            raise ProtocolError("status transaction budget exhausted")
        self.sleep(delay)
        self._check()

    def exchange(self, command):
        frame = request(command)
        self._check()
        self.command, self.reply_count = command, 0
        self.last_length, self.last_status = 0, None
        if self.device.send_feature_report(frame) != REPORT_LEN:
            raise ProtocolError("feature request was not written in full")
        self._wait(COMMAND_WAIT)
        for attempt in range(BUSY_READS):
            self._check()
            data = self.device.get_feature_report(REPORT, REPORT_LEN)
            self.reply_count += 1
            self.last_length = len(data) if isinstance(data, (bytes, bytearray, list, tuple)) else 0
            self.last_status = (data[1] if self.last_length > 1
                                and type(data[1]) is int and 0 <= data[1] <= 255 else None)
            self._check()
            try:
                return response(data)
            except StatusError as error:
                if error.status != 3 or attempt + 1 == BUSY_READS:
                    raise
                # The source re-reads busy responses, without re-sending.
                self._wait(.2 * (attempt + 1))
        raise ProtocolError("no usable response")
