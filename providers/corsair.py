"""Corsair wireless receivers (Void v2 Wireless and its siblings) over USB/HID,
without iCUE.

Protocol from Sapd/HeadsetControl's corsair_void_v2w device:

  * the vendor collection on the receiver, which HeadsetControl identifies by
    interface 4 (it gives no usage page/usage for it, so the interface number is
    the only handle on that collection; a probe logs whatever it actually finds).
  * commands are 65 bytes: [0] 0x00 report id, [1] 0x02, [2] endpoint,
    [3] 0x02, [4] command. Endpoint 0x08 is the receiver itself, 0x09 the
    headset behind it. Replies are 64 bytes.
  * talking to a sleeping headset needs the same minimal handshake
    HeadsetControl uses - receiver firmware query, receiver heartbeat, then a
    headset heartbeat - which is enough to read the battery without switching
    the headset into software mode (that switch is audible as a pop).
  * battery: command 0x0f to endpoint 0x09 -> reply[4] | reply[5] << 8 is a
    0..1000 value, ten times the percent. The receiver sometimes answers with
    the paired headset id instead of a level, so a reading of 0 or above 1000 is
    retried a few times and then refused rather than shown.

The reply carries no charging flag: HeadsetControl reports the level as
available (not charging) for this family, and so does this provider.

The `--probe` run (#28) digs deeper than the tray ever should: every write's
return value, every frame with the time it took to arrive, a listen-only pass
before writing anything (with the vendor app open it catches the vendor's own
exchange), and - when the usual sequence yields nothing - the vendor app's
fuller software-mode path. It also prints every collection's declared report
lengths and tries the battery frame in each framing on every collection,
because a write Windows refuses locally looks exactly like a switched-off
device. Normal polling keeps the short windows; a provider runs on the tray's
timer and must never block it.

The Dark Core / Ironclaw mice and their dongles speak a second, unrelated
protocol ("nxp" in ckb-next, which reads them): a single-field 64-byte packet
`{CMD_GET 0x0e, FIELD_BATTERY 0x50}` answered with the level as an index into a
five-step table `{0, 15, 30, 50, 100}` at byte 4 and a status byte at byte 5.
ckb-next's source is the reference (src/daemon/nxp_proto.h and device.c,
repo ckb-next/ckb-next; its protocol notes live in ckb-next/corsair-protocol).
The status byte's meaning is not written down in either, so no charging state
is reported, and the level is coarse, so it is shown as "about N%".
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

CORSAIR_VID = 0x1B1C

USAGE_PAGE = 0
USAGE = 0
CONTROL_INTERFACE = 4           # the collection that carries the protocol

RECEIVER_ENDPOINT = 0x08
HEADSET_ENDPOINT = 0x09
MSG_SIZE_WRITE = 65
MSG_SIZE_READ = 64

CMD_FIRMWARE = 0x13
CMD_HEARTBEAT = 0x12
CMD_BATTERY = 0x0F

FW_SUB = 0x02
HB_SUB = 0x02
BATTERY_SUB = 0x02

LEVEL_INDEX = 4                 # little-endian 16-bit, hundredths
LEVEL_MAX = 1000
ATTEMPTS = 3
READ_TIMEOUT_MS = 500
FLUSH_TIMEOUT_MS = 30

# The --probe run (#28): this family's answers can arrive late, and the vendor
# app's own traffic is part of the picture, so the probe listens first and then
# talks with longer windows. The tray keeps the short ones above.
PROBE_LISTEN_MS = 6000
PROBE_HB_TIMEOUT_MS = 3000
PROBE_ATTEMPT_TIMEOUT_MS = (2000, 4000, 4000)
PROBE_SOFTWARE_TIMEOUT_MS = (2000, 4000)


def in_probe() -> bool:
    """True inside `halo_battery.pyw --probe` (which sets HALO_PROBE)."""
    return os.environ.get("HALO_PROBE") == "1"


class _HIDP_CAPS(ctypes.Structure):
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


def caps_for(path) -> Optional[Tuple[int, int, int]]:
    """(input, output, feature) report byte lengths of one HID collection.

    The handle is opened with no access rights (query only), so nothing is sent
    to the device. Windows-only, like the rest of the probe machinery.
    """
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
                return (caps.InputReportByteLength, caps.OutputReportByteLength,
                        caps.FeatureReportByteLength)
            finally:
                hidd.HidD_FreePreparsedData(pp)
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:            # a probe must never take the provider down
        return None


def _error_note(dev, n) -> str:
    """The OS reason behind a refused write, when hidapi reports one."""
    if not isinstance(n, int) or n >= 0:
        return ""
    try:
        err = dev.error()
    except Exception:
        return ""
    return f" ({err})" if err else ""

PIDS = {
    0x2A08: "Corsair Void v2 Wireless",
    0x2A02: "Corsair Virtuoso Max Wireless",
    0x0A97: "Corsair HS80 Max Wireless",
}


def make_request(endpoint: int, sub: int, command: int) -> List[int]:
    frame = [0x00, 0x02, endpoint, sub, command]
    return frame + [0x00] * (MSG_SIZE_WRITE - len(frame))


def make_software_mode(endpoint: int) -> List[int]:
    """`01 03 00 02` to an endpoint - the vendor app's software-mode switch.

    HeadsetControl's initializeDevice() and OpenLinkHub's Connect() both send it
    before commands work on a cold device. The headset can pop audibly when the
    mode changes, which is why normal polling avoids it.
    """
    frame = [0x00, 0x02, endpoint, 0x01, 0x03, 0x00, 0x02]
    return frame + [0x00] * (MSG_SIZE_WRITE - len(frame))


# --- second family: the "nxp" protocol of the Dark Core / Ironclaw mice --------------------
# A wired mouse (1b1c:1b7e) exists too; nothing here can prove it answers, so only the
# dongle is read.
NXP_PIDS = {
    0x1B7F: "Corsair Dark Core RGB Pro SE",
}
NXP_USAGE_PAGE = 0xFF42          # the dongle's two vendor collections, from the #56 dump
NXP_CMD_GET = 0x0E               # ckb-next: CMD_GET
NXP_FIELD_BATTERY = 0x50         # ckb-next: FIELD_BATTERY
NXP_MSG_SIZE = 64                # ckb-next: MSG_SIZE (structures.h)
NXP_LEVEL_INDEX = 4
NXP_STATUS_INDEX = 5
NXP_LEVELS = (0, 15, 30, 50, 100)   # ckb-next's nxp_battery_lut


def nxp_request() -> bytes:
    """The 64-byte nxp packet; hidapi wants the report id (0) in front of it."""
    payload = bytearray(NXP_MSG_SIZE)
    payload[0] = NXP_CMD_GET
    payload[1] = NXP_FIELD_BATTERY
    return b"\x00" + bytes(payload)


def parse_nxp(reply) -> Optional[Tuple[int, str]]:
    """-> (level, label) from a battery reply, or None when it is not one."""
    if not reply:
        return None
    data = list(reply)
    if len(data) >= NXP_MSG_SIZE + 1:      # hidapi may hand the report id back
        data = data[1:]
    if len(data) < NXP_STATUS_INDEX + 1:
        return None
    idx = data[NXP_LEVEL_INDEX]
    if not 0 <= idx < len(NXP_LEVELS):
        return None
    return NXP_LEVELS[idx], f"about {NXP_LEVELS[idx]}%"


def parse_level(r) -> Optional[int]:
    """Percent from a battery reply, or None when there is no usable value."""
    if not r or len(r) <= LEVEL_INDEX + 1:
        return None
    vendor = r[LEVEL_INDEX] | (r[LEVEL_INDEX + 1] << 8)
    if vendor == 0 or vendor > LEVEL_MAX:
        return None
    return vendor // 10


class CorsairProvider(Provider):
    name = "corsair"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if d.get("interface_number") == CONTROL_INTERFACE:
                return d
        self._diag.append(f"  no interface {CONTROL_INTERFACE} collection; "
                          f"falling back to the first of {len(infos)}")
        return infos[0] if infos else None

    def _pick_nxp(self, infos: List[dict]) -> List[dict]:
        """The dongle's vendor collections, its own iface 1 first; the rest only
        when the dump's collection is missing (a wrong endpoint then costs one read)."""
        vend = [d for d in infos if d.get("usage_page") == NXP_USAGE_PAGE]
        if not vend:
            self._diag.append(f"  no {NXP_USAGE_PAGE:04x} collection; trying all "
                              f"{len(infos)}")
            vend = list(infos)
        return sorted(vend, key=lambda d: (0 if d.get("usage") == 0x0001 else 1,
                                           d.get("interface_number") or 99))

    def _query_nxp(self, path: bytes) -> Optional[List[int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            dev.write(nxp_request())
            r = dev.read(NXP_MSG_SIZE + 1, READ_TIMEOUT_MS)
            if not r:
                self._diag.append("  no reply")
                return None
            self._diag.append(f"  reply: {hexdump(r)}")
            return list(r)
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _write(self, dev, endpoint: int, sub: int, command: int) -> int:
        return self._write_frame(dev, make_request(endpoint, sub, command),
                                 f"{endpoint:02x}/{sub:02x}/{command:02x}")

    def _write_frame(self, dev, frame, label: str) -> int:
        """One write, in the shape this family's receiver accepts on Windows.

        Windows refuses a write whose length is not exactly the collection's
        OutputReportByteLength (STATUS_INVALID_PARAMETER, 0x57) before it ever
        reaches the device - headsetcontrol#521's wall, and #28's, where every
        65-byte write came back -1. The reporter's probe logs show the shape
        that is accepted: 64 bytes with no leading report id. The two shapes
        cannot both be accepted on one collection, so the fallback picks the
        right one by itself.
        """
        n = dev.write(frame[1:])          # 64 bytes: no leading report id
        if isinstance(n, int) and n < 0:
            n = dev.write(frame)          # 65 bytes: the old rid-prefixed form
        if in_probe():
            self._diag.append(f"  [w] {label} -> {n}{_error_note(dev, n)}")
        return n

    def _drain(self, dev, label: str = "") -> None:
        """Drop replies that are still queued, so the next read is ours."""
        for _ in range(4):
            r = dev.read(MSG_SIZE_READ, FLUSH_TIMEOUT_MS)
            if not r:
                return
            if in_probe():
                self._diag.append(f"  [drain{label}] {hexdump(r, MSG_SIZE_READ)}")

    def _listen(self, dev) -> None:
        """Probe only: read whatever arrives, without writing anything.

        With the vendor app open this catches the vendor's own exchange with the
        headset - the ground truth for what a working answer looks like.
        """
        self._diag.append(f"  listening {PROBE_LISTEN_MS // 1000} s without writing anything")
        t0 = time.monotonic()
        while (time.monotonic() - t0) * 1000 < PROBE_LISTEN_MS:
            r = dev.read(MSG_SIZE_READ, 250)
            if r:
                ms = (time.monotonic() - t0) * 1000
                self._diag.append(f"  [{ms / 1000:6.2f}s] {hexdump(r, MSG_SIZE_READ)}")

    def _probe_fallback(self, dev) -> Optional[List[int]]:
        """Probe only: what is left to try when the usual sequence yields nothing.

        The vendor app's Connect() takes the fuller path - software mode on the
        receiver and the headset first - and the desktop software may leave the
        device in a state only that path talks to. The headset can pop audibly
        when the mode changes; that is expected in this run.
        """
        self._diag.append("  probe: trying the vendor app's fuller sequence "
                          "(software mode; the headset may pop)")
        try:
            self._write(dev, RECEIVER_ENDPOINT, FW_SUB, CMD_FIRMWARE)
            self._write_frame(dev, make_software_mode(RECEIVER_ENDPOINT), "08|01/03/00/02")
            self._write(dev, RECEIVER_ENDPOINT, HB_SUB, CMD_HEARTBEAT)
            self._write_frame(dev, make_software_mode(HEADSET_ENDPOINT), "09|01/03/00/02")
            self._drain(dev)
            self._write(dev, HEADSET_ENDPOINT, HB_SUB, CMD_HEARTBEAT)
            t0 = time.monotonic()
            r = dev.read(MSG_SIZE_READ, PROBE_HB_TIMEOUT_MS)
            if r:
                self._diag.append(f"  probe: heartbeat answered "
                                  f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                  f"{hexdump(r, MSG_SIZE_READ)}")
            else:
                self._diag.append("  probe: no heartbeat after software mode either")
            for i, timeout in enumerate(PROBE_SOFTWARE_TIMEOUT_MS):
                self._write(dev, HEADSET_ENDPOINT, BATTERY_SUB, CMD_BATTERY)
                t0 = time.monotonic()
                r = dev.read(MSG_SIZE_READ, timeout)
                if not r:
                    self._diag.append(f"  probe: attempt {i + 1}: no reply after {timeout} ms")
                    continue
                self._diag.append(f"  probe: attempt {i + 1} reply "
                                  f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                  f"{hexdump(r, MSG_SIZE_READ)}")
                if parse_level(r) is not None:
                    return list(r)
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  probe: error in the fuller sequence: {e}")
        return None

    def _probe_caps(self, infos: List[dict]) -> Dict[bytes, Optional[Tuple[int, int, int]]]:
        """Probe only: what every collection of this receiver declares.

        #28's logs showed every write refused with -1 before it could reach the
        dongle - a refusal this layer makes locally, indistinguishable from a
        silent device until the caps are on the table.
        """
        caps_by_path = {}
        for d in infos:
            path = d.get("path")
            caps = caps_for(path)
            caps_by_path[path] = caps
            shown = "?" if caps is None else f"in/out/feat={caps[0]}/{caps[1]}/{caps[2]}"
            self._diag.append(f"  caps: iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} {shown}")
        return caps_by_path

    def _probe_matrix(self, infos: List[dict],
                      caps_by_path: Dict[bytes, Optional[Tuple[int, int, int]]]) -> None:
        """Probe only: try the battery frame in each framing, on every collection.

        The first collection that takes a write is reused for one more ask, so the
        next build can start from it. A write Windows refuses never reaches the
        dongle, which is the state #28's logs were stuck in.
        """
        self._diag.append("  probe: write shapes, collection by collection")
        order = sorted(infos,
                       key=lambda d: (0 if d.get("interface_number") == CONTROL_INTERFACE else 1,
                                      0 if (d.get("usage_page") or 0) >= 0xFF00 else 1))
        frame = make_request(HEADSET_ENDPOINT, BATTERY_SUB, CMD_BATTERY)
        for d in order:
            key = d.get("path")
            key = bytes(key) if isinstance(key, (bytes, bytearray)) else None
            caps = caps_by_path.get(key) if key is not None else None
            where = (f"iface={d.get('interface_number')} "
                     f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            shapes = [("write65-rid0", frame), ("write64-norid", frame[1:])]
            if caps and caps[1] and caps[1] != len(frame):
                shapes.append((f"write{caps[1]}-rid0", (frame + [0x00] * caps[1])[:caps[1]]))
            dev = hid.device()
            try:
                dev.open_path(d.get("path"))
            except (OSError, IOError) as e:
                self._diag.append(f"  probe: open {where}: {e}")
                continue
            try:
                accepted = False
                accepted_buf = None
                for label, buf in shapes:
                    try:
                        n = dev.write(bytes(buf))
                    except (OSError, IOError, ValueError) as e:
                        n, note = -1, f" ({e})"
                    else:
                        note = _error_note(dev, n)
                    self._diag.append(f"  probe: {label} {where} -> {n}{note}")
                    if isinstance(n, int) and n >= 0:
                        accepted = True
                        accepted_buf = bytes(buf)
                        break
                if not accepted:
                    continue
                t0 = time.monotonic()
                r = dev.read(MSG_SIZE_READ, PROBE_HB_TIMEOUT_MS)
                self._diag.append(f"  probe: first answer "
                                  f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                  f"{hexdump(r, MSG_SIZE_READ) if r else 'none'}")
                n2 = dev.write(accepted_buf)
                self._diag.append(f"  probe: re-ask -> {n2}{_error_note(dev, n2)}")
                t0 = time.monotonic()
                r = dev.read(MSG_SIZE_READ, PROBE_HB_TIMEOUT_MS)
                self._diag.append(f"  probe: ask again "
                                  f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                  f"{hexdump(r, MSG_SIZE_READ) if r else 'none'}")
                self._diag.append("  probe: this collection takes the write - build on it")
                break
            finally:
                try:
                    dev.close()
                except Exception:
                    pass

    def _query(self, path: bytes) -> Optional[List[int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            if in_probe():
                self._listen(dev)

            # The handshake that wakes a sleeping headset without an audible pop.
            self._write(dev, RECEIVER_ENDPOINT, FW_SUB, CMD_FIRMWARE)
            self._write(dev, RECEIVER_ENDPOINT, HB_SUB, CMD_HEARTBEAT)
            self._drain(dev)
            hb_n = self._write(dev, HEADSET_ENDPOINT, HB_SUB, CMD_HEARTBEAT)
            hb_timeout = PROBE_HB_TIMEOUT_MS if in_probe() else READ_TIMEOUT_MS
            t0 = time.monotonic()
            hb = dev.read(MSG_SIZE_READ, hb_timeout)
            if not hb:
                if isinstance(hb_n, int) and hb_n < 0:
                    self._diag.append("  headset heartbeat: the write was refused "
                                      "before it left (nothing was sent)")
                elif in_probe():
                    self._diag.append(f"  headset heartbeat: no reply after "
                                      f"{hb_timeout} ms (headset off or asleep)")
                else:
                    self._diag.append("  headset heartbeat: no reply "
                                      "(headset off or asleep)")
                if in_probe():
                    return self._probe_fallback(dev)
                return None
            if in_probe():
                self._diag.append(f"  headset heartbeat answered "
                                  f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                  f"{hexdump(hb, MSG_SIZE_READ)}")
            self._drain(dev)

            for attempt in range(ATTEMPTS):
                self._write(dev, HEADSET_ENDPOINT, BATTERY_SUB, CMD_BATTERY)
                if in_probe():
                    timeout = PROBE_ATTEMPT_TIMEOUT_MS[min(attempt, len(PROBE_ATTEMPT_TIMEOUT_MS) - 1)]
                else:
                    timeout = READ_TIMEOUT_MS
                t0 = time.monotonic()
                r = dev.read(MSG_SIZE_READ, timeout)
                if not r:
                    if in_probe():
                        self._diag.append(f"  attempt {attempt + 1}: no reply after {timeout} ms")
                    else:
                        self._diag.append(f"  attempt {attempt + 1}: no reply")
                    continue
                if in_probe():
                    self._diag.append(f"  attempt {attempt + 1} reply "
                                      f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                      f"{hexdump(r, MSG_SIZE_READ)}")
                else:
                    self._diag.append(f"  attempt {attempt + 1} reply: {hexdump(r)}")
                if parse_level(r) is not None:
                    return list(r)
                if in_probe():
                    self._drain(dev, " between attempts")
            self._diag.append("  no usable level in the replies")
            if in_probe():
                return self._probe_fallback(dev)
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(CORSAIR_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(corsair): %s", e)
            return []
        out = []
        seen = set()
        for pid in PIDS:
            mine = [d for d in infos if d["product_id"] == pid and d["path"] not in seen]
            if not mine:
                continue
            d = self._pick(mine)
            if d is None:
                continue
            seen.add(d["path"])
            name = PIDS[pid]
            self._diag.append(f"[Corsair] pid={pid:04x} '{name}' "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            caps_by_path: Dict[bytes, Optional[Tuple[int, int, int]]] = {}
            if in_probe():
                self._diag.append(f"  path: {d.get('path', b'')!r}")
                caps_by_path = self._probe_caps(mine)
            reply = self._query(d["path"])
            if reply is None and in_probe():
                self._probe_matrix(mine, caps_by_path)
            level = parse_level(reply)
            if level is None:
                continue
            out.append(DeviceStatus(f"corsair:{pid:04x}", name, level, False, True,
                                    "corsair", kind="headset"))
        for pid, name in NXP_PIDS.items():
            mine = [d for d in infos if d["product_id"] == pid and d["path"] not in seen]
            if not mine:
                continue
            self._diag.append(f"[Corsair nxp] pid={pid:04x} '{name}'")
            for d in self._pick_nxp(mine):
                self._diag.append(f"  iface={d.get('interface_number')} "
                                  f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                seen.add(d["path"])
                parsed = parse_nxp(self._query_nxp(d["path"]))
                if parsed is None:
                    continue
                level, label = parsed
                out.append(DeviceStatus(f"corsair:{pid:04x}", name, level, False, True,
                                        "corsair", approx=label, kind="mouse"))
                break
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
