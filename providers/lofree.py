"""Lofree keyboards on their 2.4 GHz dongles: the Hyzen and the Flow Lite84.

The Hyzen part comes from Lofree's own web driver (hyzen.lofree.tech). Its code is
obfuscated; the exchange below comes from its device list and from its battery
routine, with the strings decoded by the page's own decoder functions.

Device list: vendor 0x388D, collection usage page 0xFF1C, usage 0x92:
    0x0024  Hyzen on the USB cable     (connection type 1, device id 101)
    0x0025  Hyzen on the 2.4 GHz dongle (connection type 2, device id 101)
    0x0029  a second model on the cable (device id 102)

Every command is a small transaction on HID report 0x04. After the report id, a
frame is 31 bytes on the dongle (63 on the cable):

    host   00 00 01                       start
    kbd    .. .. 01 ...                   byte 2 = 01: start acknowledged
    host   00 00 <cmd> 00 00 00 00        the command, no data, offset 0
    kbd    .. .. <cmd> .. 00 00 .. <d0>   byte 2 = cmd (or 00), bytes 4-5 = offset 0,
                                          the answer starts at byte 7
    host   00 00 02                       end
    kbd    .. .. 02 ...                   byte 2 = 02: end acknowledged

The web driver reads the battery only over the dongle, and not when the same
keyboard is also on its cable:
    command 0xAA  "is the keyboard online": d0 = 0 means offline
    command 0x1A  battery: d0 = the level in %
The web driver shows no charging state, so this provider does not either.

The Flow Lite84 (#165) is on the same driver's newer Control HUB page
(lofree.tech/home, bundle index-BTVblIUr.js). It speaks the 17-byte frame family
providers/pulsar.py already reads for the Pulsar/ATK/VXE/Hitscan mice: report id 0x08,
command 0x04, the level in byte 6, the charging flag in byte 7, millivolts big-endian
in bytes 8-9, and the checksum that leaves the whole frame - report id included -
summing to 0x55. The driver marks keyboard commands with bit 7 of the payload's byte
4, which is byte 5 of the frame on the wire, and it asks command 0x03 ("is the
keyboard online": byte 6 is 1) before command 0x04. Both are answered on the family's
control collection
0xFF02:0x0002, the one this provider opens. Only the 2.4 GHz dongle (05AC:024F,
manufacturer 'CX' - a copy of Apple's vendor id) is claimed: no wired id was
reported. Confirmed on a Flow Lite84 on its 2.4 GHz dongle (#165): the level and the
charging state both showed.

AULA V9 mice - the same exchange, measured on a V9 Max+ (dongle 320F:22A0, cable
320F:22B7) against the maker's own app (AULA HUB, an Electron app whose HID calls were
hooked over CDP) and then on the hardware:

  * the mouse acknowledges START and answers command 0x1A with the level at the same
    DATA_AT offset 7 - measured `00 00 1a 00 00 00 00 59 00` = 89 % - so the Lofree
    transaction works on it unchanged. It also answers the same command without the
    START/END handshake, but the documented way is used here.
  * one byte past the level is the charge state, which Lofree's keyboards do not send:
    0 = not charging, 1 = charging, 2 = charged. Measured: `.. 64 00`, `.. 63 01` and
    `.. 64 02` while the same mouse sat idle, on the cable and fully charged. The maker's
    own app treats 1 and 2 as "charging", and so does this provider.
  * a level of 0 is not a level: a receiver that has just been re-plugged answers zeros
    until the mouse reports, and 0 would also fire a false low-battery alert.
  * the receiver answers from its cache whether or not the mouse is switched on, so the
    level alone never proves it is there. The link state comes from a frame the receiver
    pushes every ~0.5 s while the link is up: `04 AA 00 AA 00 00 00 00 <state>` with
    0xFF while the mouse is on and 0x00 while it is off (measured five seconds apart, same
    session). The frame is not used to *find* the mouse - it is absent when the mouse is
    idle - only to correct a reading that arrives while the link is known to be down.
  * the mouse sleeps when idle and the next poll wakes it, so one 0x00 is not conclusive.
"""
from __future__ import annotations

import ctypes
import sys
import time
from typing import Dict, List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

LOFREE_VID = 0x388D
USAGE_PAGE, USAGE = 0xFF1C, 0x0092

DONGLES = {0x0025: "Lofree Hyzen"}           # the pids that are read (2.4 GHz)
WIRED_OF = {0x0025: 0x0024}                  # the same keyboard on its cable

# AULA V9 mice on the same collection and the same command; the cable and the dongle of
# one mouse are two ids of one device and share one icon.
AULA_VID = 0x320F
AULA_PIDS = {0x22A0: "AULA V9 Max+ (2.4 GHz receiver)",
             0x22B7: "AULA V9 Max+ (USB cable)"}

REPORT_ID = 0x04
FRAME = 31                                   # bytes after the report id, on the dongle
START, END = 0x01, 0x02
CMD_ONLINE, CMD_BATTERY = 0xAA, 0x1A
DATA_AT = 7                                  # the answer starts here (after the report id)
TIMEOUT = 3.0                                # s, the web driver's minimum timeout
CHARGE_AT = DATA_AT + 1
HEARTBEAT_AT = DATA_AT
LINK_ON = 0xFF
READ_MS = 100

# the Flow Lite84: the Compx frame of providers/pulsar.py with the keyboard flag
FLOW_VID, FLOW_PID = 0x05AC, 0x024F          # 'CX' '2.4G Wireless Receiver'
FLOW_NAME = "Lofree Flow Lite84"
FLOW_CONTROL_USAGE = (0xFF02, 0x0002)        # where the family answers (pulsar.py opens it too)
FLOW_HEADER = 0x08                           # byte 0, which is also the report id
FLOW_LEN = 17
FLOW_CHECKSUM_BASE = 0x55
FLOW_CMD_ONLINE, FLOW_CMD_BATTERY = 0x03, 0x04
FLOW_FLAG_KEYBOARD = 0x80                    # bit 7 of the payload's byte 4: frame byte 5
FLOW_ONLINE_INDEX = 6                        # the answer byte of both commands
FLOW_LEVEL_INDEX = 6
FLOW_CHARGING_INDEX = 7                      # 1 = charging
FLOW_VOLTAGE_SLICE = (8, 10)                 # millivolts, big-endian
FLOW_READ_ATTEMPTS = 4                       # as in providers/pulsar.py
FLOW_READ_MS = 250
FLOW_FLUSH_MS = 30


def frame(b2: int) -> List[int]:
    """Report 0x04 with byte 2 set: 00 00 <b2> 00 ..., 31 bytes after the id."""
    return [REPORT_ID, 0x00, 0x00, b2] + [0x00] * (FRAME - 3)


def payload(r) -> Optional[List[int]]:
    """The bytes after the report id of an input report 0x04, or None."""
    if not r or len(r) < 2 or r[0] != REPORT_ID:
        return None
    return list(r[1:])


def flow_checksum(frame) -> int:
    """The family checksum: 0x55 minus the sum of the bytes it is given, mod 256.
    On the wire the checksum is computed over the first sixteen bytes, so the whole
    frame - report id included - sums to 0x55."""
    return (FLOW_CHECKSUM_BASE - sum(frame)) % 256


def flow_request(cmd: int) -> List[int]:
    """A 17-byte request frame: the command, and the keyboard flag in byte 5."""
    frame = [FLOW_HEADER, cmd, 0x00, 0x00, 0x00, FLOW_FLAG_KEYBOARD]
    frame += [0x00] * (FLOW_LEN - len(frame) - 1)
    frame.append(flow_checksum(frame))
    return frame


def flow_parse(r, cmd: int) -> Optional[List[int]]:
    """The 17-byte reply to `cmd`, or None when this is some other frame.

    The dongle pushes events as well, so a frame counts only when the header, the
    status byte, the acknowledged command and the checksum all agree - the checks the
    web driver makes before it reads a value.
    """
    if not r or len(r) < FLOW_LEN:
        return None
    frame = list(r[:FLOW_LEN])
    if frame[0] != FLOW_HEADER or frame[1] != cmd or frame[2] != 0x00:
        return None
    if frame[FLOW_LEN - 1] != flow_checksum(frame[:FLOW_LEN - 1]):
        return None
    return frame


class _HIDP_CAPS(ctypes.Structure):
    """The part of HIDP_CAPS this file needs (same shape as providers/pulsar.py)."""
    _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
                ("InputReportByteLength", ctypes.c_ushort),
                ("OutputReportByteLength", ctypes.c_ushort),
                ("FeatureReportByteLength", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort * 17),
                ("NumberLinkCollectionNodes", ctypes.c_ushort),
                ("NumberInputButtonCaps", ctypes.c_ushort),
                ("NumberInputValueCaps", ctypes.c_ushort),
                ("NumberInputDataIndices", ctypes.c_ushort),
                ("NumberOutputButtonCaps", ctypes.c_ushort),
                ("NumberOutputValueCaps", ctypes.c_ushort),
                ("NumberOutputDataIndices", ctypes.c_ushort),
                ("NumberFeatureButtonCaps", ctypes.c_ushort),
                ("NumberFeatureValueCaps", ctypes.c_ushort),
                ("NumberFeatureDataIndices", ctypes.c_ushort)]


def _flow_query_output_length(path) -> Optional[int]:
    """OutputReportByteLength of one HID collection, or None when Windows does not
    say. The handle is opened with no access rights, so nothing is sent to the
    device - the same read-only probe providers/pulsar.py uses."""
    if sys.platform != "win32":
        return None
    try:
        p = path.decode("utf-8", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
        k32, hidd = ctypes.windll.kernel32, ctypes.windll.hid
        k32.CreateFileW.restype = ctypes.c_void_p
        handle = k32.CreateFileW(p, 0, 3, None, 3, 0, None)   # no access, share r/w, open existing
        if handle in (None, ctypes.c_void_p(-1).value):
            return None
        try:
            pp = ctypes.c_void_p()
            if not hidd.HidD_GetPreparsedData(ctypes.c_void_p(handle), ctypes.byref(pp)):
                return None
            try:
                caps = _HIDP_CAPS()
                if hidd.HidP_GetCaps(pp, ctypes.byref(caps)) != 0x00110000:   # HIDP_STATUS_SUCCESS
                    return None
                return caps.OutputReportByteLength
            finally:
                hidd.HidD_FreePreparsedData(pp)
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:            # a probe must never take the provider down
        return None


_FLOW_CAPS: Dict[bytes, Optional[int]] = {}   # collection path -> output report length


def flow_output_length(path) -> Optional[int]:
    """Cached _flow_query_output_length. The paths change when a receiver is
    re-plugged, so the cache never outlives the collection it describes."""
    if path not in _FLOW_CAPS:
        _FLOW_CAPS[path] = _flow_query_output_length(path)
    return _FLOW_CAPS[path]


class LofreeProvider(Provider):
    name = "lofree"

    def __init__(self):
        self._diag: List[str] = []
        self._link_down = False

    def _wait(self, dev, want, deadline: float, watch_link: bool = False) -> Optional[List[int]]:
        """Read reports 0x04 until `want(payload)` is true, or until the deadline.

        With `watch_link`, AULA's pushed link-state frame (`04 AA 00 AA ... <state>`) is
        picked up on the way: 0x00 means the mouse is switched off, and a reading that
        arrives then is the receiver's cache, not the mouse.
        """
        while time.time() < deadline:
            try:
                r = dev.read(64, READ_MS)
            except (OSError, ValueError) as e:
                self._diag.append(f"    read: {e}")
                return None
            p = payload(r)
            if p is None:
                continue
            if watch_link and len(p) > HEARTBEAT_AT and p[0] == 0xAA and p[2] == 0xAA:
                if p[HEARTBEAT_AT] != LINK_ON:
                    self._link_down = True
                continue
            if want(p):
                return p
        return None

    def _command(self, dev, cmd: int, watch_link: bool = False) -> Optional[List[int]]:
        """One transaction; the payload of the answer, or None."""
        deadline = time.time() + TIMEOUT
        dev.write(frame(START))
        if self._wait(dev, lambda p: len(p) >= 3 and p[2] == START, deadline,
                      watch_link) is None:
            self._diag.append(f"    cmd {cmd:02x}: no start acknowledgement")
            return None
        dev.write([REPORT_ID, 0x00, 0x00, cmd, 0x00, 0x00, 0x00, 0x00] + [0x00] * (FRAME - 7))
        p = self._wait(dev, lambda p: (len(p) > DATA_AT and p[2] in (cmd, 0x00)
                                       and p[4] == 0x00 and p[5] == 0x00), deadline,
                       watch_link)
        # close the transaction in any case, as the web driver does
        dev.write(frame(END))
        self._wait(dev, lambda p: len(p) >= 3 and p[2] == END, min(deadline, time.time() + 0.5))
        if p is None:
            self._diag.append(f"    cmd {cmd:02x}: no answer")
            return None
        self._diag.append(f"    cmd {cmd:02x}: {hexdump(p, 10)}")
        return p

    def _open(self, path):
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        return dev

    def _read(self, path) -> Optional[int]:
        """A Lofree keyboard's level: the online command first, as the web driver does."""
        dev = self._open(path)
        if dev is None:
            return None
        try:
            try:
                online = self._command(dev, CMD_ONLINE)
                if online is None:
                    return None
                if online[DATA_AT] == 0:
                    self._diag.append("    keyboard offline (off or asleep)")
                    return None
                answer = self._command(dev, CMD_BATTERY)
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"    write: {e}")
                return None
            if answer is None:
                return None
            level = answer[DATA_AT]
            if not 0 <= level <= 100:
                self._diag.append(f"    level {level} out of range, not shown")
                return None
            return level
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _flow_pick(self, infos: List[dict]) -> Optional[dict]:
        """The collection to open for the Flow Lite84.

        The family's control collection (0xFF02:0x0002) is REQUIRED: the dongle shares
        Apple's vendor and product id (05AC:024F), which Apple also uses for its wired
        Aluminium Keyboard, and that keyboard has no such collection - without this rule
        the frame went to its first collection on every poll (#169 review,
        @ahmedkhursheed23). Within the collection, one whose output report cannot carry
        the 17-byte frame is skipped: Windows refuses that write and no reply comes,
        which is indistinguishable from a keyboard that is off. The length is only known
        when Windows says so, and an unknown length never disqualifies a collection.
        """
        control = [d for d in infos
                   if (d.get("usage_page"), d.get("usage")) == FLOW_CONTROL_USAGE]
        if not control:
            self._diag.append("  no FF02:0002 control collection; nothing is written")
            return None
        # A control collection exists, so this is a Flow dongle: try it first, then the
        # rest (a Windows-reported length is a hint, and an unknown length is allowed).
        for d in control + [x for x in infos if x not in control]:
            if flow_output_length(d["path"]) in (None, FLOW_LEN):
                return d
            self._diag.append(f"  {d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
                              f"output={flow_output_length(d['path'])} cannot take a "
                              f"{FLOW_LEN}-byte frame; skipped")
        return None

    def _flow_command(self, dev, cmd: int) -> Optional[List[int]]:
        """One request, then reads until its reply - or nothing."""
        dev.write(flow_request(cmd))
        for _ in range(FLOW_READ_ATTEMPTS):
            r = dev.read(FLOW_LEN, FLOW_READ_MS)
            if not r:
                break
            frame = flow_parse(r, cmd)
            if frame is not None:
                self._diag.append(f"    cmd {cmd:02x}: {hexdump(frame)}")
                return frame
        self._diag.append(f"    cmd {cmd:02x}: no reply")
        return None

    def _flow_read(self, path) -> Optional[Tuple[int, bool]]:
        """(level, charging) from a Flow Lite84 dongle, or None.

        The web driver asks whether the keyboard is online before it asks for the
        level, and so does this: a keyboard that is off answers nothing useful.
        """
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            try:
                for _ in range(4):           # drop stale frames first, as the family does
                    if not dev.read(FLOW_LEN, FLOW_FLUSH_MS):
                        break
                online = self._flow_command(dev, FLOW_CMD_ONLINE)
                if online is None or online[FLOW_ONLINE_INDEX] != 1:
                    if online is not None:
                        self._diag.append("    keyboard offline (off or asleep)")
                    return None
                reply = self._flow_command(dev, FLOW_CMD_BATTERY)
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"    write: {e}")
                return None
            if reply is None:
                return None
            level = reply[FLOW_LEVEL_INDEX]
            if not 0 <= level <= 100:
                self._diag.append(f"    level {level} out of range, not shown")
                return None
            mv = int.from_bytes(bytes(reply[FLOW_VOLTAGE_SLICE[0]:FLOW_VOLTAGE_SLICE[1]]), "big")
            if mv:
                self._diag.append(f"    {mv} mV")
            return level, reply[FLOW_CHARGING_INDEX] == 1
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _read_mouse(self, path) -> Optional[Tuple[int, bool]]:
        """An AULA mouse's (level, charging); the battery command alone is enough."""
        self._link_down = False
        dev = self._open(path)
        if dev is None:
            return None
        try:
            try:
                answer = self._command(dev, CMD_BATTERY, watch_link=True)
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"    write: {e}")
                return None
            if answer is None:
                return None
            level = answer[DATA_AT]
            # 0 is not a level: a receiver that was just re-plugged answers zeros until the
            # mouse reports, and 0 % would also fire a false low-battery alert
            if not 1 <= level <= 100:
                self._diag.append(f"    level {level} out of range, not shown")
                return None
            charge = answer[CHARGE_AT] if len(answer) > CHARGE_AT else 0
            if charge not in (0, 1, 2):
                charge = 0
            return level, charge in (1, 2)
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _aula_mice(self, out: List[DeviceStatus]) -> None:
        try:
            infos = hidlist.enumerate(AULA_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(aula): %s", e)
            return
        cols = [d for d in infos
                if d["product_id"] in AULA_PIDS
                and (d.get("usage_page"), d.get("usage")) == (USAGE_PAGE, USAGE)]
        if not cols:
            return
        # the cable first: it is the live one when the mouse is plugged in
        cols.sort(key=lambda d: (d["product_id"] != 0x22B7,))
        name = ""
        got = None
        for d in cols:
            product = (d.get("product_string") or "").strip()
            if product:
                name = product
            pid = d["product_id"]
            self._diag.append(f"[AULA] {name or AULA_PIDS[pid]} pid={pid:04x}")
            got = self._read_mouse(d["path"])
            if got is not None:
                break
        if got is None:
            return
        level, charging = got
        online = not self._link_down
        self._diag.append(f"  -> {level}%{', charging' if charging else ''}"
                          + ("" if online else ", link reported down"))
        out.append(DeviceStatus("aula:mouse", name or AULA_PIDS[0x22A0], level, charging,
                                online, "lofree", kind="mouse"))

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(LOFREE_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(lofree): %s", e)
            infos = []
        present = {d["product_id"] for d in infos}
        out: List[DeviceStatus] = []
        for d in infos:
            pid = d["product_id"]
            if pid not in DONGLES or (d.get("usage_page"), d.get("usage")) != (USAGE_PAGE, USAGE):
                continue
            product = (d.get("product_string") or "").split("@")[0].strip()
            name = f"Lofree {product}" if product else DONGLES[pid]
            self._diag.append(f"[Lofree] {name} pid={pid:04x}")
            if WIRED_OF.get(pid) in present:
                self._diag.append("  the keyboard is on its cable: the dongle is not read")
                continue
            level = self._read(d["path"])
            if level is not None:
                self._diag.append(f"  -> {level}%")
                out.append(DeviceStatus(f"lofree:{pid:04x}", name, level, False, True, "lofree",
                                        kind="keyboard"))
        self._poll_flow(out)
        self._aula_mice(out)
        return out

    def _poll_flow(self, out: List[DeviceStatus]) -> None:
        """The Flow Lite84 dongle (#165): a second vendor id with its own exchange."""
        try:
            infos = hidlist.enumerate(FLOW_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(flow lite84): %s", e)
            return
        mine = [d for d in infos if d["product_id"] == FLOW_PID]
        if not mine:
            return
        d = self._flow_pick(mine)
        if d is None:
            return
        self._diag.append(f"[Lofree] {FLOW_NAME} pid={FLOW_PID:04x} "
                          f"iface={d.get('interface_number')} "
                          f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
        res = self._flow_read(d["path"])
        if res is None:
            return
        level, charging = res
        self._diag.append(f"  -> {level}%")
        out.append(DeviceStatus(f"lofree:{FLOW_PID:04x}", FLOW_NAME, level, charging, True,
                                "lofree", kind="keyboard"))

    def diagnostics(self) -> List[str]:
        return list(self._diag)
