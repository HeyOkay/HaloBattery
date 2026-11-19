"""JBL Quantum 350 Wireless on its 2.4 GHz receiver (0ecb:206b), without
QuantumENGINE.

Protocol from krisgfx9/Wireless-Device-Battery-Monitor's JBL driver:
the receiver's generic-desktop collection on interface 3 (usage 0x0001:0x0000,
feature report length >= 2) answers two numbered one-byte feature reports.

    0x68  link: 1 = headset on, 0 = headset off (0x69 mirrors it)
    0x49  battery percentage, in 5 % steps; it keeps the last value while
          the headset is off

The earlier HaloBattery diagnostics for #454 show exactly this collection on
the reporter's receiver. No vendor collection is exposed, and the protocol has
no charging byte: the level is shown as a plain reading, not as charging.
"""
from __future__ import annotations

from typing import List, Optional

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, log

VID = 0x0ECB
PID = 0x206B
INTERFACE = 3
PAGE, USAGE = 0x0001, 0x0000
REPORT_LINK = 0x68
REPORT_LEVEL = 0x49
REPORT_LEN = 2
ATTEMPTS = 2

NAME = "JBL Quantum 350 Wireless"
KEY = "jbl:206b"


def feature_byte(report, report_id: int) -> Optional[int]:
    """Value of a numbered one-byte feature report answered on Windows.

    hidapi returns the report id as byte 0. A shorter or mismatched reply is
    not accepted, so a stale/empty answer cannot become a level.
    """
    if not report or len(report) != REPORT_LEN:
        return None
    r = list(report)
    if r[0] != report_id:
        return None
    return r[1]


class JblQuantum350Provider(Provider):
    name = "jbl"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[int] = None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(jbl_quantum350): %s", e)
            return []

        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[JBL Quantum 350] pid={VID:04x}:{PID:04x} "
                          f"'{d.get('product_string') or ''}' "
                          f"iface={d.get('interface_number')} "
                          f"{d.get('usage_page'):04x}:{d.get('usage'):04x}")
        got = self._read(d["path"])
        if got is None:
            self._diag.append("  no answer (headset off or receiver asleep)")
            if self._last is not None:
                return [DeviceStatus(KEY, NAME, self._last, False, False, "jbl",
                                     kind="headset", charging_known=False)]
            return [DeviceStatus(KEY, NAME, None, False, False, "jbl",
                                 kind="headset", charging_known=False)]

        level = got
        self._diag.append(f"  -> {level}%")
        self._last = level
        return [DeviceStatus(KEY, NAME, level, False, True, "jbl",
                             kind="headset", charging_known=False)]

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and d.get("interface_number") == INTERFACE
                    and (d.get("usage_page"), d.get("usage")) == (PAGE, USAGE)):
                # Windows hidapi 0.15 does not return feature_report_byte_length,
                # so an absent value is unknown, not zero. A reported value below
                # the two-byte report is still a reason to skip the collection.
                feat = d.get("feature_report_byte_length")
                if feat is not None and feat < REPORT_LEN:
                    continue
                return d
        return None

    def _read(self, path) -> Optional[int]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            for _ in range(ATTEMPTS):
                link = self._get(dev, REPORT_LINK)
                if link is None:
                    continue
                if link != 1:
                    self._diag.append(f"  link={link} (headset off)")
                    return None
                level = self._get(dev, REPORT_LEVEL)
                if level is None:
                    continue
                if 0 <= level <= 100:
                    return level
                self._diag.append(f"  level out of range: {level}")
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def _get(self, dev, report_id: int) -> Optional[int]:
        try:
            r = dev.get_feature_report(report_id, REPORT_LEN)
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  feature {report_id:02x}: {e}")
            return None
        value = feature_byte(r, report_id)
        if value is None:
            self._diag.append(f"  feature {report_id:02x}: {r!r}")
        return value

    def diagnostics(self) -> List[str]:
        return list(self._diag)
