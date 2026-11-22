"""Skullcandy headsets on their 2.4 GHz dongles (#104): Airoha's RACE protocol.

Source: the Headroom project (patalbansishashank/Headroom, bin/headroom_race.py,
commit 87a9892 - recovered from the Airoha SDK sources Skull-HQ ships in its
Electron bundle, and confirmed on the Crusher PLYR 720 dongle 34F0:5310), cross
checked byte for byte against the Skull-HQ capture of the PLYR dongle (34F0:3210)
attached to issue #104, where Skull-HQ showed 70 % and the capture's indication
carries 0x46 = 70.

On the vendor collection (usage page 0xFF13, the only vendor page these dongles
present):

    ask      output report 6, 62 bytes: 06 07 80 05 5A 03 00 D6 0C 00
             -> [0x06] [frame length] [recipient] [race frame]
             recipient 0x80 addresses the headset behind the dongle. 0x00 would
             address the dongle itself, which has no battery and answers "no
             battery" - the trap Headroom documents; the byte is easy to misread
             as the high half of a 16-bit length, because it is zero for
             dongle-local traffic and only then breaks headset queries.
             race frame = 05 <type> <u16le length> <u16le opcode> <payload>;
             length counts the opcode plus the payload.

    reply    report 7, read with GET_REPORT: [0x07] [length] [recipient] [frames]
             -> a byte stream of frames. The answer to the battery ask (opcode
             0x0CD6) arrives as a 0x5D indication with payload
             [status, role, percent] - carried in the SAME report as its 0x5B
             acknowledgement, so a reader that looks only at the first frame
             sees the ack and reads it as a refusal.

Headroom's read_battery drains the interrupt stream before its on-demand query
and accepts a newly arrived indication, including an unchanged battery value.
Windows GET_REPORT is also retained when a known pre-query snapshot changes;
an unchanged snapshot alone is not evidence of a fresh reading.

The battery ask needs its role byte (0x00 = agent); an empty payload is rejected.
The reply carries no charging state. When nothing answers (the headset off or
asleep), the dongle keeps its icon, without a level, like the app's other
headsets. Not added: the SLYR Pro (34F0:2220) presents the same two collections
but no capture of its battery exchange exists.
"""
from __future__ import annotations

import time
from typing import List, Optional

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, log

SKULLCANDY_VID = 0x34F0
VENDOR_USAGE_PAGE = 0xFF13

RPT_OUT, RPT_IN = 0x06, 0x07
RPT_SIZE = 62

HEAD = 0x05
T_REQ, T_IND = 0x5A, 0x5D
RECIPIENT_HEADSET = 0x80
OP_BATTERY = 0x0CD6
ROLE_AGENT = 0x00

BATTERY_FRAME = bytes([HEAD, T_REQ, 0x03, 0x00]) + OP_BATTERY.to_bytes(2, "little") \
    + bytes([ROLE_AGENT])
ASK = bytes([RPT_OUT, len(BATTERY_FRAME), RECIPIENT_HEADSET]) + BATTERY_FRAME

READ_ATTEMPTS = 12               # the answer arrived within ~40 ms in the capture
READ_DELAY = 0.06
DRAIN_LIMIT = 16
DRAIN_SECONDS = 0.3
INTERRUPT_TIMEOUT_MS = 1

# pid -> name. Wired and cable pids of the same model would share a name, so they
# share one icon.
KNOWN = {
    0x3210: "Skullcandy PLYR",                     # capture, issue #104
    0x5310: "Skullcandy Crusher PLYR 720",         # Headroom's own unit
}


def battery_percent(report) -> Optional[int]:
    """The percent from a dongle report, or None when it carries no indication.

    The report starts [0x07] [length] [recipient]; after it runs a stream of
    frames 05 <type> <u16le len> <u16le opcode> <payload>. The frames are sliced
    and any 0x5D indication of opcode 0x0CD6 with status 0 yields its percent; a
    direct scan for the indication pattern backs the slicer up for reports whose
    slicing runs off the end."""
    if not isinstance(report, (bytes, bytearray, list, tuple)):
        return None
    try:
        report = bytes(report)
    except (TypeError, ValueError):
        return None
    if len(report) < 9:
        return None
    if report[0] == RPT_IN:
        if len(report) < 3 or report[2] != RECIPIENT_HEADSET:
            return None
        end = 3 + report[1]
        if end > len(report):
            return None
        report = report[3:end]
    elif report[0] != HEAD:
        return None
    i = 0
    while i + 6 <= len(report):
        if report[i] != HEAD:
            i += 1
            continue
        type_ = report[i + 1]
        length = report[i + 2] | (report[i + 3] << 8)
        if length < 2 or i + 4 + length > len(report):
            break
        opcode = report[i + 4] | (report[i + 5] << 8)
        payload = report[i + 6:i + 4 + length]
        if (type_ == T_IND and opcode == OP_BATTERY and len(payload) >= 3
                and payload[0] == 0 and payload[1] == ROLE_AGENT and payload[2] <= 100):
            return payload[2]
        i += 4 + length

    # Some captured streams need resynchronization after an unrelated bad frame.
    # Search only inside the declared headset report, never its padding or a
    # different recipient's data, and still verify status and requested role.
    pattern = bytes([HEAD, T_IND, 0x05, 0x00]) + OP_BATTERY.to_bytes(2, "little")
    k = report.find(pattern)
    if (k >= 0 and len(report) >= k + 9 and report[k + 6] == 0
            and report[k + 7] == ROLE_AGENT and report[k + 8] <= 100):
        return report[k + 8]
    return None


class SkullcandyProvider(Provider):
    name = "skullcandy"

    def __init__(self):
        self._diag: List[str] = []

    def _read(self, path) -> Optional[int]:
        """Drain old IRQ data, ask once, and require evidence of a fresh report."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, ValueError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            before = None
            try:
                pre = dev.get_input_report(RPT_IN, RPT_SIZE)
                if isinstance(pre, (bytes, bytearray, list, tuple)):
                    before = bytes(pre)
            except (OSError, ValueError, TypeError, AttributeError):
                pass

            interrupt = callable(getattr(dev, "read", None))
            if interrupt:
                # The reference uses a bounded pre-query drain. No USB flush or
                # extra command is issued; these are ordinary short input reads.
                deadline = time.monotonic() + DRAIN_SECONDS
                quiet = False
                for _ in range(DRAIN_LIMIT):
                    try:
                        old = dev.read(RPT_SIZE, INTERRUPT_TIMEOUT_MS)
                    except (OSError, ValueError):
                        interrupt = False
                        break
                    if not old:
                        quiet = True
                        break
                    if time.monotonic() >= deadline:
                        break
                if interrupt and not quiet:
                    self._diag.append("    input backlog did not drain; battery query deferred")
                    return None
            try:
                sent = dev.write(ASK + bytes(RPT_SIZE - len(ASK)))
            except (OSError, ValueError) as e:
                self._diag.append(f"    write: {e}")
                return None
            if sent != RPT_SIZE:
                self._diag.append(f"    write returned {sent} of {RPT_SIZE} bytes")
                return None
            skipped = 0
            for _attempt in range(READ_ATTEMPTS):
                time.sleep(READ_DELAY)
                if interrupt:
                    try:
                        incoming = dev.read(RPT_SIZE, INTERRUPT_TIMEOUT_MS)
                        level = battery_percent(incoming)
                        if level is not None:
                            self._diag.append(f"    -> {level}% (fresh interrupt report)")
                            return level
                    except (OSError, ValueError):
                        # Some Windows drivers only expose GET_REPORT. Its
                        # changed-snapshot fallback still needs a known baseline.
                        interrupt = False
                if before is None:
                    continue
                try:
                    report = bytes(dev.get_input_report(RPT_IN, RPT_SIZE))
                except (OSError, ValueError, TypeError, AttributeError):
                    before = None
                    continue
                if report == before:
                    skipped += 1
                    continue
                level = battery_percent(report)
                if level is not None:
                    self._diag.append(f"    -> {level}% (changed input snapshot)")
                    return level
            if skipped:
                self._diag.append(f"    ({skipped} unchanged snapshot(s) refused as stale)")
            self._diag.append("    no battery indication (headset off or asleep?)")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(SKULLCANDY_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(skullcandy): %s", e)
            return []

        out: List[DeviceStatus] = []
        for d in infos:
            pid = d["product_id"]
            if pid not in KNOWN:
                continue
            if d.get("usage_page", 0) != VENDOR_USAGE_PAGE:
                continue
            name = KNOWN[pid]
            self._diag.append(f"[Skullcandy] {name} pid={pid:04x} usage="
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            level = self._read(d["path"])
            out.append(DeviceStatus(f"skullcandy:{pid:04x}", name, level, False,
                                    True, "skullcandy", "", kind="headset"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
