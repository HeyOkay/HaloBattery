"""MCHOSE wireless mice on the 2.4 GHz receiver (M7 Ultra 5253:1020 confirmed on hardware).

Protocol from MCHOSE's own driver, recovered and documented by @alexfrih in
github.com/alexfrih/mchose-linux (PROTOCOL.md, from the M HUB web bundle), which covers
the 0x5253 family: L7/L7 Pro/L7 Ultra, M7, A7, K7. What this mouse forced on top of that
document, all of it measured on the M7 Ultra with firmware 5.2.7.0:

  * the configuration collection is usage page 0xFF01. The sibling collection on the same
    interface (0xFF0B, usage 0x104) never answers anything, on any report id or length.
  * two report ids: 0x11 (20 bytes) and 0x12 (64 bytes), both *feature* reports. Reads are
    written as feature reports too; output reports are not used.
  * every payload byte is inverted (^ 0xFF), padding included, so a zero tail goes on the
    wire as FF FF FF.
  * a reply echoes cmd ^ 0xFF in byte 1 and inverts its payload from byte 2.
  * 0x06 returns: vid:u16, model:u16, firmware:u32, flags:u8, level:u8, charging:u8 and
    then a second level/charging pair with the same values. Charging is 1 while the mouse
    is on the cable, 0 otherwise. The flags byte reads as the radio link state (0x09 on
    the dongle, 0x00 while wired).
  * **it must be re-asked for every attempt.** One request followed by repeated reads only
    ever returns the request itself (raw, then decoded, alternating) - the reference
    driver's "retry the read" advice is what makes this work here, and about one exchange
    in two carries a real answer anyway.
  * **the receiver only answers while the mouse itself is awake.** Asleep, it still answers
    the frame, but with an all-zero payload: measured here as eight seconds of zeros
    followed by the real 100% the moment the mouse was moved. It is the mouse's idle timer
    that decides, so a silent mouse is a normal state - keep the last value on a greyed-out
    icon, exactly like a Razer mouse that has gone idle, rather than calling it a fault.
  * the receiver also pushes a device-info notice on input report 0x13 (01 01 00 <level>
    02 03 06 <..> "M7 Ultra"), which independently reported the same level.

Also measured on the cable: the mouse answers on its own wired PID (5253:0031 - the same
number it reports as the model id) with the same collection and the same command, while
the receiver goes quiet, so the charging source wins and the icon stays single.

The G7 is a different chip on a different vendor id (A8A5:2255, 'YJX-CHIP', while the
0x5253 receivers are RealTek) and speaks a different protocol, worked out by @kek353 from
their own monitor and the HID dump in issue #8: a 65-byte output report starting
``00 55 30 A5 0B 2E 01 01 01``, answered by an input report starting ``AA 30`` whose byte 8
is the level and byte 9 the charging flag. Its vendor collections are 0xFFA5:0x88,
0xFF05:0x88 and 0xFF01:0x10; only the last one is written to, as in @kek353's monitor.
**Confirmed on @kek353's own G7** (issue #8): the probe answers
``aa 30 a5 0b 0a 01 01 01 2e 00 00 00``, so byte 8 = 0x2E = 46% and byte 9 = 0 while the
mouse sits on its dongle, and that level is the one their tool shows. On its cable the same
read answers ``aa 30 a5 3c 0a 01 01 01 2e 01 00 00`` (byte 9 = 1 while charging) with the
PID unchanged, so a G7 keeps a single icon the way the M7 Ultra does.

The 0x3837 family is the same protocol on MCHOSE's newer vendor id: the reference driver
"treats both identically" and matches on the vendor id plus the vendor collection rather
than by model list, which is what this provider does as well. The diagnostics in issue #4
(a MCHOSE A7 V2 Ultra, 3837:100b, RealTek strings, collections 0xFF0B:0x104 and
0xFF01:0x01 on interface 2) have exactly that shape, and the reference documents the status
read on the *short* 0x11 report, so both report ids are tried before a poll gives up.

Not verified: the 0x3837 family (the device in issue #4 is not here), other models, and the
meaning of the second level/charging pair in the 0x5253 reply (it has matched the first pair
in every reading so far).
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

# 0x5253 is the family measured here and keeps the plain icon key; 0x3837 is the newer
# MCHOSE vendor id the reference driver treats identically (issue #4's A7 V2 Ultra).
MEASURED_VID = 0x5253
MCHOSE_VIDS = (MEASURED_VID, 0x3837)

# model ids seen in the 0x06 reply (the receiver's own PID does not identify the mouse:
# 0x1020 is used by the M7 Ultra and by the L7 Pro)
MODEL_NAMES = {
    0x0031: "MCHOSE M7 Ultra",
}

# The G7: another chip (0xA8A5, 'YJX-CHIP') and another protocol, from @kek353's monitor
# and the HID dump in issue #8 - see the module docstring. Unverified: no device here.
G7_VID = 0xA8A5
G7_PID = 0x2255
G7_REQUEST = bytes([0x00, 0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01, 0x01]).ljust(65, b"\x00")
G7_HEADER = b"\xaa\x30"
G7_LEVEL_BYTE = 8
G7_CHARGE_BYTE = 9
G7_READS = 25                 # a non-blocking read loop, ~0.5 s at G7_READ_GAP
G7_READ_GAP = 0.02

CONFIG_PAGE = 0xFF01          # the only collection that answers
SHORT_REPORT = 0x11
LONG_REPORT = 0x12
SHORT_LEN = 20                # payload bytes on 0x11, 64 on 0x12 (the frame adds the id)
LONG_LEN = 64
CMD_STATUS = 0x06
CMD_BOND = 0x03

# The status read is documented on the short report and was also captured there on the M7
# Ultra, so it is tried first (cheaper: 21 bytes on the wire instead of 65); the long
# report is the channel this provider polled on before, kept as the fallback.
CHANNELS = ((SHORT_REPORT, SHORT_LEN), (LONG_REPORT, LONG_LEN))

# Re-ask rather than re-read (see the module docstring): the real answer normally arrives
# on the second exchange. An awake mouse answers within a couple of exchanges; a sleeping
# one answers nothing at all, so a long budget would only slow the poll down.
ATTEMPTS = 4
ATTEMPT_GAP = 0.12

# how long a silent mouse keeps its (greyed-out) icon before it is hidden, s
ASLEEP_KEEP = 300


def _invert(data: bytes) -> bytes:
    return bytes(b ^ 0xFF for b in data)


def make_request(cmd: int, report: int = LONG_REPORT, length: int = 64) -> bytes:
    """A request frame: report id, then cmd and a zero tail, all payload bytes inverted."""
    body = bytes([cmd]).ljust(length, b"\x00")[:length]
    return bytes([report]) + _invert(body)


def parse_status(resp) -> Optional[Tuple[int, int, int, int]]:
    """(level, charging, model, flags) from a status reply, or None if it is not one.

    The same buffer answers with all zeros, or with the request echoed back, between real
    answers, so a reply counts only when it echoes cmd ^ 0xFF *and* its payload carries a
    MCHOSE vid *and* the level is in range.
    """
    if not resp or len(resp) < 13:
        return None
    r = list(resp)
    if r[0] not in (SHORT_REPORT, LONG_REPORT) or r[1] != (CMD_STATUS ^ 0xFF):
        return None
    pay = _invert(bytes(r[2:]))
    if len(pay) < 11:
        return None
    vid = int.from_bytes(pay[0:2], "little")
    if vid not in MCHOSE_VIDS:
        return None
    model = int.from_bytes(pay[2:4], "little")
    flags, level, charge = pay[8], pay[9], pay[10]
    if level > 100:
        return None
    return level, charge, model, flags


def parse_g7(resp) -> Optional[Tuple[int, bool]]:
    """(level, charging) from a G7 reply, or None if it is not one.

    Layout from @kek353's monitor and the HID dump in issue #8: the frame starts AA 30,
    the level is byte 8 and the charging flag byte 9. Confirmed on @kek353's G7, on the
    dongle and on its cable: `aa 30 a5 0b 0a 01 01 01 2e 00 00 00` (46%, not charging) and
    `aa 30 a5 3c 0a 01 01 01 2e 01 00 00` (46%, charging). Only the two-byte AA 30 header is
    relied on - byte 3 differs between those two (0x0b against 0x3c) - and the same PID
    (A8A5:2255) on the same 0xFF01 collection answers either way, which is what keeps one
    icon for a G7 on its dongle and on its cable. An out-of-range level is refused rather
    than reported as a made-up number.
    """
    if not resp or len(resp) <= G7_CHARGE_BYTE:
        return None
    r = bytes(resp)
    if not r.startswith(G7_HEADER):
        return None
    level = r[G7_LEVEL_BYTE]
    if level > 100:
        return None
    return level, bool(r[G7_CHARGE_BYTE])


def device_key(vid: int, pid: int) -> str:
    """One icon per device: the dongle and the cable of the same mouse share a key.

    Only the family measured here (0x5253) keeps the plain "mchose" key it has always
    used, so an existing icon does not move. Anything else gets a key of its own - the
    G7's 0xA8A5, and the newer 0x3837 receivers (the A7 V2 Ultra in issue #4) - which is
    what keeps two MCHOSE devices on one machine off a single shared icon.
    """
    return "mchose" if vid == MEASURED_VID else f"mchose:{vid:04x}"


class MchoseProvider(Provider):
    name = "mchose"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[str, Tuple[int, bool, float]] = {}
        self._names: Dict[str, str] = {}
        self._models: Dict[str, int] = {}

    def _read_collection(self, path: bytes) -> Optional[Tuple[int, int, int, int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for report, length in CHANNELS:
                # re-ask before every read (see the module docstring): one request
                # followed by repeated reads only ever returns the request itself.
                req = make_request(CMD_STATUS, report=report, length=length)
                for attempt in range(ATTEMPTS):
                    try:
                        dev.send_feature_report(req)
                    except (OSError, ValueError) as e:
                        self._diag.append(f"    send on report {report:#04x}: {e}")
                        break
                    time.sleep(ATTEMPT_GAP)
                    try:
                        resp = dev.get_feature_report(report, length + 1)
                    except (OSError, ValueError):
                        continue                  # the receiver answers with "read error"
                    got = parse_status(resp)      # until it has the value from the mouse
                    if got:
                        self._diag.append(f"    answered on report {report:#04x}, attempt "
                                          f"{attempt + 1}: {hexdump(resp, 16)}")
                        return got
                self._diag.append(f"    no fresh reply to 0x06 on report {report:#04x}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _read_g7(self, path: bytes) -> Optional[Tuple[int, bool]]:
        """The G7's own protocol: one output report, then wait for an AA 30 input report.

        @kek353's monitor writes the request once and reads until the answer turns up
        (non-blocking, in a loop), so that is what this does - with a bounded budget and
        a sleep between reads instead of a spin. Unverified against the hardware.
        """
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            try:
                dev.set_nonblocking(True)
            except Exception:                       # pragma: no cover
                pass
            try:
                dev.write(G7_REQUEST)
            except (OSError, ValueError) as e:
                self._diag.append(f"    write: {e}")
                return None
            for _ in range(G7_READS):
                time.sleep(G7_READ_GAP)
                try:
                    resp = dev.read(64)
                except (OSError, ValueError):
                    continue
                got = parse_g7(resp)
                if got:
                    self._diag.append(f"    AA 30 answer: {hexdump(resp, 12)}")
                    return got
            self._diag.append("    no AA 30 answer")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        """One icon per device, whether it is on the dongle, on the cable or on radio."""
        self._diag = []
        infos: List[dict] = []
        for vid in MCHOSE_VIDS + (G7_VID,):
            try:
                infos += hidlist.enumerate(vid)
            except Exception as e:  # pragma: no cover
                log.warning("hid.enumerate(mchose): %s", e)
        if not infos:
            return []

        # group the collections of one receiver (or one mouse) together, per vendor id:
        # a G7 and an M7 Ultra on one machine are two devices and get two icons
        groups: Dict[Tuple[int, int], List[dict]] = {}
        for d in infos:
            groups.setdefault((d["vendor_id"], d["product_id"]), []).append(d)

        found: Dict[str, List[Tuple[int, bool, int, int]]] = {}   # key -> readings
        for (vid, pid), ifaces in groups.items():
            key = device_key(vid, pid)
            product = (ifaces[0].get("product_string") or "").strip()
            if product:
                self._names[key] = product
            cols = [d for d in ifaces if (d.get("usage_page") or 0) >= 0xFF00]
            if vid == G7_VID:
                # the G7 answers on 0xFF01 only; the other vendor collections are left
                # alone (nothing off the documented path is written to)
                cols = [d for d in cols if (d.get("usage_page") or 0) == CONFIG_PAGE]
            else:
                # the configuration collection first; 0xFF0B is dead on the M7 Ultra
                cols.sort(key=lambda d: (d.get("usage_page") != CONFIG_PAGE, d.get("usage") != 1))
            if not cols:
                self._diag.append(f"[MCHOSE] vid={vid:04x} pid={pid:04x} product='{product}': "
                                  "no vendor collection")
                continue
            for d in cols:
                self._diag.append(f"[MCHOSE] vid={vid:04x} pid={pid:04x} product='{product}' "
                                  f"iface={d.get('interface_number')} "
                                  f"usage={(d.get('usage_page') or 0):04x}:"
                                  f"{(d.get('usage') or 0):04x}")
                if vid == G7_VID:
                    got_g7 = self._read_g7(d["path"])
                    if got_g7:
                        found.setdefault(key, []).append((got_g7[0], got_g7[1], pid, 0))
                else:
                    got = self._read_collection(d["path"])
                    if got:
                        self._models[key] = got[2]
                        found.setdefault(key, []).append((got[0], bool(got[1]), pid, got[2]))
                if found.get(key):
                    break

        out: List[DeviceStatus] = []
        for key, readings in found.items():
            # a wired mouse may be silent on the radio: prefer whichever source reports
            # charging, and one icon either way
            readings.sort(key=lambda r: (not r[1],))
            level, charge, pid, model = readings[0]
            self._diag.append(f"  -> {key} pid={pid:04x}"
                              + (f" model=0x{model:04x}" if model else "")
                              + f": {level}%{' (charging)' if charge else ''}")
            self._last[key] = (level, charge, time.time())
            out.append(DeviceStatus(key, self._display_name(key), level, charge, True,
                                    "mchose", kind="mouse"))

        # silent: a receiver cannot tell a switched-off mouse from one that went to sleep
        # a few seconds ago, so keep the last value greyed out for a while
        now = time.time()
        for key, last in self._last.items():
            if key in found or now - last[2] >= ASLEEP_KEEP:
                continue
            out.append(DeviceStatus(key, self._display_name(key), last[0], last[1],
                                    False, "mchose", kind="mouse"))
        return out

    def _display_name(self, key: str) -> str:
        """A measured model id first, then the device's own product string.

        The id in the reply names the model on the family measured here (0x0031 is the M7
        Ultra, the same number it uses as its wired PID), but it is a per-model number
        that is not listed per device anywhere, so a model this table does not know is
        named from the receiver's product string: for the A7 V2 Ultra in issue #4 that
        reads "MCHOSE A7 V2 Ultra", the name on the box.
        """
        model = self._models.get(key)
        if model and model in MODEL_NAMES:
            return MODEL_NAMES[model]
        name = (self._names.get(key) or "").strip()
        if name:
            return name
        if model:
            return f"MCHOSE mouse (0x{model:04x})"
        return "MCHOSE mouse"

    def diagnostics(self) -> List[str]:
        return list(self._diag)
