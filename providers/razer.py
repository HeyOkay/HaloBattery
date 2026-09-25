"""Razer devices (headsets, mice, keyboards with a wireless receiver).

Protocol: a 90-byte feature report (report id 0), the same one Synapse and
OpenRazer use.

    [0]  status         (0x00 in a request; 0x02 = OK, 0x01 = busy, 0x03 = failure,
                         0x04 = timeout / device not responding, 0x05 = not supported)
    [1]  transaction id (0x1f for most mice, 0x3f for headsets, 0xff for older ones)
    [2..3] remaining packets
    [4]  protocol type
    [5]  data size
    [6]  command class  (0x07 = power)
    [7]  command id     (0x80 = battery level, 0x84 = charging)
    [8..87] arguments   (arg[1] = value)
    [88] crc = XOR of bytes 2..87
    [89] reserved
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import hid

from . import blackshark
from .base import DeviceStatus, Provider, hexdump, log

RAZER_VID = 0x1532

# PID -> (name, preferred transaction id)
KNOWN = {
    0x0528: ("Razer BlackShark V2 Pro (2020)", 0x3F),
    0x0555: ("Razer BlackShark V2 Pro (2023)", 0x3F),
    0x0556: ("Razer BlackShark V2 Pro (2023)", 0x3F),
    0x0557: ("Razer BlackShark V2 Pro (2023)", 0x3F),
    0x00A4: ("Razer Mouse Dock Pro", 0x1F),
    0x00AA: ("Razer Basilisk V3 Pro", 0x1F),
    0x00AB: ("Razer Basilisk V3 Pro", 0x1F),
    0x00B9: ("Razer Basilisk V3 X HyperSpeed", 0x1F),
    0x007C: ("Razer DeathAdder V2 Pro", 0x3F),
    0x007D: ("Razer DeathAdder V2 Pro", 0x3F),
    0x009C: ("Razer DeathAdder V2 X HyperSpeed", 0x1F),
    0x00B3: ("Razer HyperPolling Dongle", 0x1F),
    0x00B6: ("Razer DeathAdder V3 Pro", 0x1F),
    0x00B7: ("Razer DeathAdder V3 Pro", 0x1F),
    0x00BE: ("Razer DeathAdder V4 Pro", 0x1F),
    0x00BF: ("Razer DeathAdder V4 Pro", 0x1F),
    0x0083: ("Razer Basilisk X HyperSpeed", 0x1F),
    0x0086: ("Razer Basilisk Ultimate", 0x1F),
    0x0088: ("Razer Basilisk Ultimate", 0x1F),
    0x008F: ("Razer Naga V2 Pro", 0x1F),
    0x0090: ("Razer Naga V2 Pro", 0x1F),
    0x00A5: ("Razer Viper V2 Pro", 0x1F),
    0x00A6: ("Razer Viper V2 Pro", 0x1F),
    0x00C0: ("Razer Viper V3 Pro", 0x1F),
    0x00C1: ("Razer Viper V3 Pro", 0x1F),
    0x007A: ("Razer Viper Ultimate", 0xFF),
    0x007B: ("Razer Viper Ultimate", 0xFF),
    0x0078: ("Razer Viper Ultimate", 0xFF),
    0x00AF: ("Razer Cobra Pro", 0x1F),
    0x00B0: ("Razer Cobra Pro", 0x1F),
    0x00CC: ("Razer Basilisk V3 Pro 35K", 0x1F),
    0x00CD: ("Razer Basilisk V3 Pro 35K", 0x1F),
}

TRANSACTION_IDS = (0x1F, 0x3F, 0xFF, 0x9F, 0x08)

STATUS_OK = 0x02
STATUS_BUSY = 0x01
STATUS_TIMEOUT = 0x04     # receiver present, device not responding (off / asleep)
STATUS_NOT_SUPPORTED = 0x05


def build_request(transaction_id: int, cmd_class: int, cmd_id: int, size: int = 0x02) -> bytes:
    msg = bytearray(90)
    msg[1] = transaction_id
    msg[5] = size
    msg[6] = cmd_class
    msg[7] = cmd_id
    crc = 0
    for b in msg[2:88]:
        crc ^= b
    msg[88] = crc
    return bytes(msg)


def normalize_reply(resp) -> Optional[List[int]]:
    """hidapi on Windows returns the reply with the report id as the first byte."""
    if not resp:
        return None
    data = list(resp)
    if len(data) >= 91:
        data = data[1:91]
    if len(data) < 90:
        return None
    return data


def parse_reply(data: List[int], cmd_class: int, cmd_id: int) -> Tuple[int, Optional[int]]:
    """-> (status, arg1). arg1 is None if the reply is not for our command."""
    status = data[0]
    if data[6] != cmd_class or data[7] != cmd_id:
        return status, None
    return status, data[9]


class _Cand:
    """Cached working HID interface and transaction id for a PID."""

    def __init__(self, path: bytes, tid: int):
        self.path = path
        self.tid = tid


class RazerProvider(Provider):
    name = "razer"

    def __init__(self):
        self._cache: Dict[Tuple[int, str], _Cand] = {}
        self._dead: Dict[bytes, float] = {}   # interfaces known not to respond
        self._diag: List[str] = []

    # ---- low level -------------------------------------------------------
    def _query(self, dev, tid: int, cmd_class: int, cmd_id: int) -> Tuple[Optional[int], Optional[int]]:
        req = build_request(tid, cmd_class, cmd_id)
        try:
            dev.send_feature_report(b"\x00" + req)
        except (OSError, ValueError) as e:
            self._diag.append(f"    send tid={tid:02x}: {e}")
            return None, None
        # The device does not reply instantly: poll for up to ~1 s while status is "busy".
        deadline = time.time() + 1.0
        time.sleep(0.06)
        while True:
            try:
                resp = dev.get_feature_report(0x00, 91)
            except (OSError, ValueError) as e:
                self._diag.append(f"    get tid={tid:02x}: {e}")
                return None, None
            data = normalize_reply(resp)
            if data is None:
                self._diag.append(f"    short reply: {hexdump(resp)}")
                return None, None
            status, value = parse_reply(data, cmd_class, cmd_id)
            self._diag.append(
                f"    tid={tid:02x} cmd={cmd_class:02x}:{cmd_id:02x} -> status={status:02x} "
                f"tid'={data[1]:02x} raw={hexdump(data, 12)}")
            if status == STATUS_BUSY and time.time() < deadline:
                time.sleep(0.08)
                continue
            return status, value

    def _read(self, path: bytes, tid: int) -> Tuple[Optional[int], Optional[int], Optional[bool]]:
        """-> (status, level%, charging)"""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None, None, None
        try:
            status, raw = self._query(dev, tid, 0x07, 0x80)
            if status != STATUS_OK or raw is None:
                return status, None, None
            level = round(raw / 255 * 100)
            cst, craw = self._query(dev, tid, 0x07, 0x84)
            charging = bool(craw) if cst == STATUS_OK and craw is not None else False
            return status, level, charging
        finally:
            try:
                dev.close()
            except Exception:
                pass

    # ---- high level ------------------------------------------------------
    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hid.enumerate(RAZER_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(razer): %s", e)
            return []

        # group interfaces by device (PID + serial / location)
        groups: Dict[Tuple[int, str], List[dict]] = {}
        for d in infos:
            pid = d["product_id"]
            serial = d.get("serial_number") or ""
            groups.setdefault((pid, serial), []).append(d)

        out: List[DeviceStatus] = []
        for (pid, serial), ifaces in groups.items():
            name, pref_tid = KNOWN.get(pid, (None, None))
            if not name:
                name = (ifaces[0].get("product_string") or f"Razer {pid:04x}").strip()
            key = f"razer:{pid:04x}:{serial}"
            diag_from = len(self._diag)
            self._diag.append(f"[Razer] {name} pid={pid:04x}, interfaces: {len(ifaces)}")
            is_headset = pid in blackshark.PA_PIDS or "blackshark" in name.lower()
            if pid in blackshark.PA_PIDS:
                # 2023 headset: its own protocol first
                st = self._poll_pa((pid, serial), ifaces)
                if st is None:
                    st = self._poll_group((pid, serial), ifaces, pref_tid)
            else:
                st = self._poll_group((pid, serial), ifaces, pref_tid)
                if is_headset and (st is None or st[0] != STATUS_OK):
                    pa = self._poll_pa((pid, serial), ifaces)
                    if pa is not None and (st is None or pa[0] == STATUS_OK):
                        st = pa
            if st is None or st[0] != STATUS_OK:
                # failed poll: details go to the log so the failure history is visible
                log.info("[Razer] %s: poll failed (%s)", name,
                         "no reply" if st is None else f"status {st[0]:02x}")
                for line in self._diag[diag_from:]:
                    log.info("%s", line)
            if st is None:
                continue
            status, level, charging = st
            if status == STATUS_OK:
                out.append(DeviceStatus(key, name, level, bool(charging), True, "razer"))
            elif status == STATUS_TIMEOUT:
                out.append(DeviceStatus(key, name, None, False, False, "razer"))
        return out

    def _poll_group(self, gkey, ifaces, pref_tid):
        cached = self._cache.get(gkey)
        if cached:
            status, level, charging = self._read(cached.path, cached.tid)
            if status in (STATUS_OK, STATUS_TIMEOUT):
                return status, level, charging
            self._cache.pop(gkey, None)

        # Probe order: vendor / main collection interfaces first, then the rest.
        def rank(d):
            up = d.get("usage_page", 0)
            return (0 if up in (0x0001, 0xFF00) else 1, d.get("interface_number", 0))

        tids = [pref_tid] if pref_tid else []
        tids += [t for t in TRANSACTION_IDS if t not in tids]
        now = time.time()
        timeout_hit = None
        for d in sorted(ifaces, key=rank):
            path = d["path"]
            if self._dead.get(path, 0) > now:
                continue
            self._diag.append(
                f"  iface={d.get('interface_number')} usage={d.get('usage_page', 0):04x}:"
                f"{d.get('usage', 0):04x}")
            answered = False
            for tid in tids:
                status, level, charging = self._read(path, tid)
                if status is None:
                    break          # the interface rejects feature reports, next one
                answered = True
                if status == STATUS_OK:
                    self._cache[gkey] = _Cand(path, tid)
                    return status, level, charging
                if status == STATUS_TIMEOUT:
                    timeout_hit = (status, None, None)
                    self._cache[gkey] = _Cand(path, tid)
                    break
            if not answered:
                self._dead[path] = now + 300   # leave this interface alone for 5 minutes
        return timeout_hit

    def _poll_pa(self, gkey, ifaces):
        """BlackShark V2 Pro 2023: the "PA" protocol over output/input reports."""
        cache_key = ("pa",) + tuple(gkey)
        cached = self._cache.get(cache_key)
        cands = [d for d in ifaces if blackshark.is_candidate(d)] or list(ifaces)
        if cached:
            cands.sort(key=lambda d: 0 if d["path"] == cached.path else 1)
        offline = None
        for d in cands:
            self._diag.append(
                f"  [PA] iface={d.get('interface_number')} usage={d.get('usage_page', 0):04x}:"
                f"{d.get('usage', 0):04x}")
            res, level, charging = blackshark.read_battery(d["path"], self._diag)
            if res == "ok":
                self._cache[cache_key] = _Cand(d["path"], 0)
                return STATUS_OK, level, charging
            if res == "offline" and offline is None:
                offline = (STATUS_TIMEOUT, None, None)
                if cached and d["path"] == cached.path:
                    break      # working interface is known, the headset is just off
        return offline

    def diagnostics(self) -> List[str]:
        return list(self._diag)
