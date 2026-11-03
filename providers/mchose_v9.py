"""MCHOSE V9 Pro headset (291D:385D) over the 2.4 GHz receiver or the USB cable,
without M HUB.

This is a *different* protocol from the one in ``mchose.py``: that file covers MCHOSE
mice (the 0x5253 family, the G7 and the 0x3837 mice), which answer the inverted 0x06
frame on a 0xFF01 collection. The V9 Pro is a C-Media receiver (0x291D:0x385D) that
speaks the frame below instead, so it gets its own provider (the rule in
CONTRIBUTING.md: a device goes in its protocol family's file, and a new protocol gets a
new file). The mouse provider never enumerates 0x291D, and still leaves the 0x3837
headset pair alone - see ``HEADSET_PIDS``.

The 3837:6008/600A siblings (V9 Turbo / Turbo+) use the same 65 01 frame but are read
another way, and are covered by the open PR #215 (passive AA 0x0B read + 65 01 fallback,
confirmed on hardware); this provider does not claim them, so nothing is duplicated or
fights over the same ids.

Source: github.com/JoaoKSS/MCHOSE_v9_PRO_Controller, ``mchose_qt.py``, class
``HIDService``, method ``query_status`` (lines 146-179 of the revision this was written
from). The project opens the headset with ``hid_open(0x291D, 0x385D)`` and does:

  1. build a 64-byte buffer, the first three bytes ``55 65 01`` and the rest zero;
  2. ``hid_write(handle, buf, 64)`` - an output report;
  3. ``hid_read_timeout(handle, rbuf, 64, 500)`` - wait up to 500 ms for a reply;
  4. if ``rbuf[0] == 0x55`` and ``rbuf[1] == 0x65``, then ``rbuf[2]`` is the battery
     percent and ``rbuf[3]`` a status code (the reference names it ``status_code``);
  5. then a second request ``55 11`` reads the EQ mode from byte 2. Halo does not send
     it: only the level and the status are needed, and the EQ mode is not a battery.

Connection and id:

  * ``291D:385D`` - "MCHOSE V9 PRO" in soundcard/dongle mode (a C-Media receiver). This
    is the id the source opens, and the one in the project's udev rule; linux-hardware.org
    lists the same USB id. Confirmed on hardware: real polls on the reporter's machine
    go 20% -> 60% while the headset charges, tracking M HUB.

Only this (vid, pid) pair is ever opened, and only its vendor collections
(``usage_page >= 0xFF00``) are ever written to: the audio (0x000B / 0x000C) and other
standard collections are never touched, and with no vendor collection nothing is
written at all - a wrong endpoint could send a frame the device does not expect.

Charging: the reference's UI ignores ``status_code`` (JoaoKSS never reads it), so its
meaning is not documented anywhere. Measured on the reporter's own V9 Pro
(291D:385D): status 0x02 while running off the cable, status 0x00 while on the
cable at 60% - so 0x00 is mapped to "charging" and anything else to "not
charging". (The sibling 3837 ids use different codes - 2 discharging, 3 charging,
4 full per M HUB's driver as decoded in open PR #215 - so this mapping applies
to 291D:385D only.) The diagnostics always print the raw status byte.

Unverified: the collection to use on Windows and the meaning of the status byte are
taken from the reference; the level is now confirmed on hardware, but has not been
cross-checked against M HUB on a second unit. The reply itself is exactly the
reference's, so a wrong collection will show no icon rather than a made-up level: a
reply whose header is not ``55 65`` or whose level is outside 0..100 is refused.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

# One key for the V9 Pro: the 2.4 GHz receiver and the USB cable present as the same
# C-Media id, and a stable key keeps the icon from flapping while it moves between them.
KEY = "mchose_v9"
DEFAULT_NAME = "MCHOSE V9 Pro"

# The one confirmed headset. 0x3837 (the V9 Turbo/Turbo+ and the MCHOSE mice) is not
# enumerated here on purpose: the mice belong to mchose.py and the 3837 Turbo pair to
# the open PR #215. See the module docstring.
V9_VIDS = (0x291D,)
V9_PIDS = {
    0x291D: (0x385D,),            # "MCHOSE V9 PRO", soundcard/dongle mode (C-Media)
}
ALLOWED = frozenset((vid, pid) for vid, pids in V9_PIDS.items() for pid in pids)

# The battery request: 64 bytes, 55 65 01 then zeros. ``dev.write`` takes the first byte
# as the report id (0x55 here), exactly as the reference's hid_write does.
REQUEST = bytes([0x55, 0x65, 0x01]).ljust(64, b"\x00")
REPLY_HEADER = (0x55, 0x65)

READ_LEN = 64
READ_TIMEOUT_MS = 500            # the reference's hid_read_timeout budget
READ_ATTEMPTS = 3
READ_PAUSE = 0.05

VENDOR_PAGE_MIN = 0xFF00         # collections below this are audio/HID and are left alone

# Windows gives this text when an output report has to go as a feature report instead
# (the same fallback hyperx_cloud3.py makes).
_FEATURE_ONLY = ("incorrect function", "0x00000001")


def make_request() -> bytes:
    """The battery request, 64 bytes starting ``55 65 01``."""
    return REQUEST


def make_feature_request() -> bytes:
    """The same frame for the feature-report fallback: no extra report-id prefix.

    ``hyperx_cloud3.py`` (``_write``, lines 117-142) re-sends the exact same packet
    through ``send_feature_report`` that it sent through ``write``: 0x55 is the report
    id in both paths, and hidapi takes it from the frame's first byte either way.
    Prepending a 0x00 report id here would send 65 bytes and a different report.
    """
    return REQUEST


def parse_status(resp) -> Optional[Tuple[int, int]]:
    """(level, status) from a battery reply, or None when it is not one.

    The reference reads ``d[0] == 0x55 and d[1] == 0x65``; byte 2 is the percent and
    byte 3 the status code. A level outside 0..100 is refused rather than shown as a
    made-up number, and anything whose header is not 55 65 is not a reply to this
    request at all.
    """
    if not resp or len(resp) < 4:
        return None
    r = list(resp)
    if r[0] != REPLY_HEADER[0] or r[1] != REPLY_HEADER[1]:
        return None
    level = r[2]
    if not 0 <= level <= 100:
        return None
    return level, r[3]


class MchoseV9Provider(Provider):
    name = "mchose_v9"

    def __init__(self):
        self._diag: List[str] = []

    @staticmethod
    def _error(dev) -> str:
        """hidapi's text for the last failure (on Windows, the system error message)."""
        try:
            return str(dev.error() or "")
        except Exception:
            return ""

    def _write(self, dev) -> bool:
        """write() first, then the feature-report fallback some dongles need on Windows.

        cython-hidapi returns -1 from write()/send_feature_report() on failure rather
        than raising, so a negative result counts as a failure, with dev.error() as its
        text; some builds raise instead and both paths end up in the same check.
        """
        try:
            n = dev.write(make_request())
            if n is not None and n >= 0:
                return True
            err = f"-> {n} {self._error(dev)}".rstrip()
        except (OSError, IOError, ValueError) as e:
            err = str(e)
        if not any(s in err.lower() for s in _FEATURE_ONLY):
            self._diag.append(f"    write: {err}")
            return False
        self._diag.append(f"    write: {err} -> retrying as a feature report")
        try:
            n = dev.send_feature_report(make_feature_request())
            if n is not None and n < 0:
                self._diag.append(f"    feature report -> {n} {self._error(dev)}".rstrip())
                return False
            self._diag.append("    feature report accepted")
            return True
        except (OSError, IOError, ValueError) as e2:
            self._diag.append(f"    feature report: {e2}")
            return False

    def _read_collection(self, path) -> Optional[Tuple[int, int]]:
        """Ask one collection for the battery; blocking read with a timeout, as jbl.py does."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            if not self._write(dev):
                return None
            for attempt in range(READ_ATTEMPTS):
                time.sleep(READ_PAUSE)
                try:
                    resp = dev.read(READ_LEN, READ_TIMEOUT_MS)
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"    read: {e}")
                    break
                if not resp:
                    continue
                self._diag.append(f"    attempt {attempt + 1}: {hexdump(resp, 16)}")
                got = parse_status(resp)
                if got:
                    return got
            self._diag.append("    no 55 65 reply")
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
        infos: List[dict] = []
        for vid in V9_VIDS:
            try:
                infos += hidlist.enumerate(vid)
            except Exception as e:  # pragma: no cover
                log.warning("hid.enumerate(mchose v9): %s", e)
        if not infos:
            return []

        groups: Dict[Tuple[int, int], List[dict]] = {}
        for d in infos:
            groups.setdefault((d.get("vendor_id"), d.get("product_id")), []).append(d)

        for (vid, pid), ifaces in groups.items():
            if (vid, pid) not in ALLOWED:
                # Defensive: only 0x291D is enumerated, but nothing outside the V9 Pro
                # allowlist is opened, let alone written to.
                self._diag.append(f"[MCHOSE-V9] vid={vid:04x} pid={pid:04x}: not the V9 "
                                  f"Pro, leaving it alone")
                continue
            product = (ifaces[0].get("product_string") or "").strip()
            cols = [d for d in ifaces if (d.get("usage_page") or 0) >= VENDOR_PAGE_MIN]
            if not cols:
                offered = ", ".join(f"{(d.get('usage_page') or 0):04x}:{(d.get('usage') or 0):04x}"
                                    for d in ifaces)
                self._diag.append(f"[MCHOSE-V9] vid={vid:04x} pid={pid:04x} product='{product}': "
                                  f"no vendor collection (found: {offered}); not writing")
                continue
            for d in cols:
                self._diag.append(f"[MCHOSE-V9] vid={vid:04x} pid={pid:04x} product='{product}' "
                                  f"iface={d.get('interface_number')} "
                                  f"usage={(d.get('usage_page') or 0):04x}:"
                                  f"{(d.get('usage') or 0):04x}")
                got = self._read_collection(d["path"])
                if not got:
                    continue
                level, status = got
                # Measured on the reporter's V9 Pro: 0x02 off the cable
                # (discharging), 0x00 on the cable at 60% (charging).
                # Anything else (e.g. never-seen 0x01) counts as not charging
                # rather than a made-up state - see the module docstring.
                charging = (status == 0)
                self._diag.append(f"  -> {KEY} {level}% status=0x{status:02x}"
                                  + (" (charging)" if charging else ""))
                name = product or DEFAULT_NAME
                return [DeviceStatus(KEY, name, level, charging, True,
                                     "mchose_v9", kind="headset")]
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
