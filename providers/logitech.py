"""Logitech wireless mice and keyboards over HID++ 2.0 (Lightspeed / Unifying
receivers, or the device itself on the cable). Works alongside G HUB.

Protocol (documented by Logitech, implemented in Solaar):
  * long request on the receiver's vendor interface ff00:0002:
      11 <device index> <feature index> <function << 4 | swid> <params...>
    device index 1..6 behind a receiver, 0xFF for a device on the cable
  * root feature (index 0): fn 0 maps a feature id to its index, fn 1 is a ping
  * battery, first one the device supports:
      0x1004 unified battery, fn 1: <percent> <level flags> <charging status> ...
      0x1000 battery status,  fn 0: <percent> <next level> <status>
      0x1001 battery voltage, fn 0: <mV hi> <mV lo> <flags>   (G502 Lightspeed)
  * 0x0005 device name (fn 0 length, fn 1 characters, fn 2 device type)
  * 0x0003 device information, fn 0: <entities> <unit id: 4 bytes> ...
  * an error reply (10 <idx> 8f ... / 11 <idx> ff ...) comes at once for an
    empty slot; a paired device that is asleep or switched off does not answer
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Set, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

LOGITECH_VID = 0x046D
SWID = 0x0A
TIMEOUT = 0.6
PING_TIMEOUT = 2.0       # a dozing radio takes up to ~0.5 s to answer the first request
ASLEEP_KEEP = 300        # how long a silent device keeps its (greyed-out) icon, s

F_ROOT, F_INFO, F_NAME = 0x0000, 0x0003, 0x0005
F_UNIFIED, F_STATUS, F_VOLTAGE = 0x1004, 0x1000, 0x1001

# 0x0005 device type -> DeviceStatus.kind
KINDS = {0: "keyboard", 2: "keyboard", 3: "mouse", 4: "mouse", 5: "mouse"}

# Li-ion discharge curve used by Solaar, mV -> %
VOLTAGE_CURVE = ((4186, 100), (4067, 90), (3989, 80), (3922, 70), (3859, 60), (3811, 50),
                 (3778, 40), (3751, 30), (3717, 20), (3671, 10), (3646, 5), (3579, 2), (3500, 0))


def voltage_to_percent(mv: int) -> int:
    if mv >= VOLTAGE_CURVE[0][0]:
        return 100
    for (hi_mv, hi_p), (lo_mv, lo_p) in zip(VOLTAGE_CURVE, VOLTAGE_CURVE[1:]):
        if mv >= lo_mv:
            return round(lo_p + (mv - lo_mv) * (hi_p - lo_p) / (hi_mv - lo_mv))
    return 0


def parse_battery(feature: int, p) -> Tuple[Optional[int], bool]:
    """Battery level and "on external power" from the params of a battery reply.
    A full battery still on the charger counts as charging."""
    if feature == F_UNIFIED:     # 1 charging, 2 slow charging, 3 complete
        return (p[0] if p[0] <= 100 else None), p[2] in (1, 2, 3)
    if feature == F_STATUS:      # 1 recharging, 2 almost full, 3 full
        return (p[0] if 0 < p[0] <= 100 else None), p[2] in (1, 2, 3)
    if feature == F_VOLTAGE:     # bit 7: external power (Solaar's rule)
        mv = (p[0] << 8) | p[1]
        if mv < 2500:
            return None, False
        return voltage_to_percent(mv), bool(p[2] & 0x80)
    return None, False


class _Channel:
    """The receiver's (or cabled device's) HID++ short and long interfaces."""

    def __init__(self, short_path: Optional[bytes], long_path: bytes):
        self.devs = []
        self.error = False       # the last request got an error reply (not a timeout)
        self.long = hid.device()
        self.long.open_path(long_path)
        self.long.set_nonblocking(True)
        self.devs.append(self.long)
        if short_path:           # errors come back as short reports
            try:
                s = hid.device()
                s.open_path(short_path)
                s.set_nonblocking(True)
                self.devs.append(s)
            except (OSError, IOError):
                pass

    def close(self):
        for d in self.devs:
            try:
                d.close()
            except Exception:
                pass

    def request(self, idx: int, feat: int, func: int, params=(),
                timeout: float = TIMEOUT) -> Optional[List[int]]:
        """Params of the reply, or None on an error reply (self.error set) or timeout."""
        self.error = False
        req = [0x11, idx, feat, (func << 4) | SWID] + list(params)
        self.long.write(req + [0] * (20 - len(req)))
        end = time.time() + timeout
        while time.time() < end:
            for d in self.devs:
                r = d.read(64)
                if not r or len(r) < 4 or r[1] != idx:
                    continue
                if r[2] in (0x8F, 0xFF) and r[3] == feat:
                    self.error = True
                    return None
                if r[2] == feat and r[3] == (func << 4) | SWID:
                    return list(r[4:]) + [0] * 16
            time.sleep(0.005)
        return None

    def feature_index(self, idx: int, feature_id: int) -> int:
        r = self.request(idx, 0, 0, [feature_id >> 8, feature_id & 0xFF])
        return r[0] if r else 0

    def identity(self, idx: int) -> Tuple[str, str, str]:
        """(name, kind, unit id) of the device at idx; parts it cannot read are empty."""
        name = kind = unit = ""
        fi = self.feature_index(idx, F_NAME)
        if fi:
            r = self.request(idx, fi, 0)
            length = r[0] if r else 0
            raw = b""
            while len(raw) < length:
                r = self.request(idx, fi, 1, [len(raw)])
                if not r:
                    break
                raw += bytes(r[:16])
            name = raw[:length].decode("utf-8", "replace").strip()
            r = self.request(idx, fi, 2)
            kind = KINDS.get(r[0], "") if r else ""
        fi = self.feature_index(idx, F_INFO)
        if fi:
            r = self.request(idx, fi, 0)
            if r and any(r[1:5]):
                unit = bytes(r[1:5]).hex().upper()
        return name, kind, unit


class LogitechProvider(Provider):
    name = "logitech"

    def __init__(self):
        self._diag: List[str] = []
        self._ids: Dict[Tuple[int, int], Tuple[str, str, str]] = {}   # (pid, idx) -> identity
        self._asleep: Set[Tuple[int, int]] = set()   # paired slots that stopped answering
        self._last: Dict[str, Tuple[DeviceStatus, float]] = {}

    def _read(self, ch: _Channel, pid: int, idx: int) -> Optional[DeviceStatus]:
        slot = (pid, idx)
        # A paired device that is asleep does not answer at all, which would cost the
        # full ping timeout on every poll (all night long). Once a slot has gone
        # silent, ping it with the short timeout until it answers again.
        timeout = TIMEOUT if slot in self._asleep else PING_TIMEOUT
        if ch.request(idx, F_ROOT, 1, timeout=timeout) is None:
            if ch.error:                      # empty slot
                self._asleep.discard(slot)
            else:                             # paired, but silent
                self._asleep.add(slot)
                name = self._ids.get(slot, ("",))[0] or "paired device"
                self._diag.append(f"  idx={idx} '{name}': no answer (asleep or off)")
            return None
        self._asleep.discard(slot)
        if slot not in self._ids:
            self._ids[slot] = ch.identity(idx)
        name, kind, unit = self._ids[slot]
        name = name or "Logitech device"
        for feature in (F_UNIFIED, F_STATUS, F_VOLTAGE):
            fi = ch.feature_index(idx, feature)
            if not fi:
                continue
            r = ch.request(idx, fi, 1 if feature == F_UNIFIED else 0)
            if r is None:
                continue
            level, chg = parse_battery(feature, r)
            self._diag.append(f"  idx={idx} '{name}' unit={unit or '?'} feature {feature:04x}: "
                              f"{hexdump(r, 4)} -> {level}%{' (charging)' if chg else ''}")
            if level is not None:
                # the unit id is stable across receiver and cable and tells identical
                # devices apart; without one, fall back to the receiver slot
                key = f"logitech:{unit}" if unit else f"logitech:{pid:04x}:{idx}"
                return DeviceStatus(key, name, level, chg, True, "logitech", kind=kind)
        self._diag.append(f"  idx={idx} '{name}': no battery feature answered")
        return None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(LOGITECH_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(logitech): %s", e)
            infos = []

        groups: Dict[int, Dict[int, bytes]] = {}          # pid -> {usage: path}
        for d in infos:
            if d.get("usage_page") == 0xFF00 and d.get("usage") in (1, 2):
                groups.setdefault(d["product_id"], {})[d["usage"]] = d["path"]

        found: Dict[str, DeviceStatus] = {}
        for pid, paths in groups.items():
            if 2 not in paths:
                continue
            product = next((d.get("product_string") or "" for d in infos if d["product_id"] == pid), "")
            receiver = "receiver" in product.lower()
            self._diag.append(f"[Logitech] pid={pid:04x} '{product}'")
            try:
                ch = _Channel(paths.get(1), paths[2])
            except (OSError, IOError) as e:
                self._diag.append(f"  open: {e}")
                continue
            try:
                for idx in (range(1, 7) if receiver else (0xFF,)):
                    st = self._read(ch, pid, idx)
                    # the same device on the cable and through the receiver: charging wins
                    if st and (st.key not in found or st.charging):
                        found[st.key] = st
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  error: {e}")
            finally:
                ch.close()

        now = time.time()
        out = list(found.values())
        for st in out:
            self._last[st.key] = (st, now)
        # asleep or switched off: keep the last value greyed out for a while
        for key, (st, t) in list(self._last.items()):
            if key in found:
                continue
            if now - t < ASLEEP_KEEP and groups:
                out.append(DeviceStatus(key, st.name, st.level, st.charging, False, "logitech",
                                        kind=st.kind))
            else:
                del self._last[key]
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
