"""Wraith W1 / W1 Freestyle (PixArt ids 093A:522C receiver, 622C cable).

The W1 shares those ids with Incott mice but uses a different command map.
Device-info read: feature report 09, command 8F; reply byte 3 is 18 (W1)
or 20 (Freestyle). The status is pushed as input report 09 after the
read-only 86 button reads the Wraith web driver uses; battery is bit 7
charging plus the low 7 bits percentage.

Only feature reads and the 86/8F queries are sent; no settings are written.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x093A
RECEIVER = 0x522C
CABLE = 0x622C
PIDS = (RECEIVER, CABLE)
PAGE = 0xFF05
REPORT = 0x09
MODEL_CMD = 0x8F
STATUS_CMD = 0x86
BUTTONS = (0, 1, 2, 4, 3)
READ_TIMEOUT_MS = 50
LISTEN_MS = 400


def model_name(model: int) -> str:
    return "Wraith W1 Freestyle" if model == 20 else "Wraith W1"


def parse_status(r) -> Optional[Tuple[int, bool]]:
    if not r or len(r) < 2:
        return None
    b = list(r)
    if b[0] != REPORT:
        return None
    raw = b[1]
    level = raw & 0x7F
    if level > 100:
        return None
    return level, bool(raw & 0x80)


class WraithProvider(Provider):
    name = "wraith"

    def __init__(self):
        self._diag: List[str] = []
        self._models = {}

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(wraith): %s", e)
            return []

        out: List[DeviceStatus] = []
        for pid in PIDS:
            entries = [d for d in infos if d.get("product_id") == pid]
            colls = [d for d in entries if d.get("usage_page") == PAGE
                     and (d.get("feature_report_byte_length") or 0) >= 9]
            if not colls:
                continue
            model = self._probe(colls)
            if not model:
                continue
            self._models[pid] = model
            cable = pid == CABLE
            got = self._read_status(entries, colls, cable)
            out.append(DeviceStatus(f"wraithw1:{pid:04x}", model_name(model),
                                    got[0] if got else None,
                                    got[1] if got else False,
                                    bool(got), "wraith", kind="mouse",
                                    via="USB cable" if cable else "2.4 GHz receiver"))
        return out

    def _probe(self, colls) -> Optional[int]:
        for d in reversed(colls):
            dev = hid.device()  # type: ignore[union-attr]
            try:
                dev.open_path(d["path"])
                dev.send_feature_report(bytes([REPORT, MODEL_CMD]))
                end = time.time() + 0.5
                while time.time() < end:
                    r = dev.get_feature_report(REPORT, 9)
                    if r and len(r) >= 4 and r[0] == REPORT and r[1] == MODEL_CMD:
                        if r[3] in (18, 20):
                            self._diag.append(f"  model {r[3]} on {d['path']}")
                            return r[3]
                        return None
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  model probe: {e}")
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
        return None

    def _read_status(self, entries, colls, cable: bool) -> Optional[Tuple[int, bool]]:
        # Keep every FF05 input collection open for the pushed 09 status.
        readers = []
        for d in entries:
            if d.get("usage_page") != PAGE or (d.get("input_report_byte_length") or 0) < 2:
                continue
            try:
                dev = hid.device()  # type: ignore[union-attr]
                dev.open_path(d["path"])
                readers.append(dev)
            except (OSError, IOError):
                pass
        command = None
        try:
            command = hid.device()  # type: ignore[union-attr]
            command.open_path(colls[-1]["path"])
            if not readers:
                return None
            time.sleep(0.2)
            found = None
            for button in BUTTONS:
                if not self._button(command, button):
                    continue
                found = self._listen(readers, LISTEN_MS)
                if found:
                    break
            for _ in range(4):
                if found:
                    break
                if not self._button(command, 0):
                    break
                found = self._listen(readers, 550)
            if found:
                self._diag.append(f"  -> {found[0]}% charging={found[1]}")
            return found
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  status error: {e}")
            return None
        finally:
            for dev in readers + ([command] if command is not None else []):
                try:
                    dev.close()
                except Exception:
                    pass

    def _button(self, dev, button: int) -> bool:
        try:
            dev.send_feature_report(bytes([REPORT, STATUS_CMD, button]))
            for t in (0.15, 0.02):
                time.sleep(t)
                try:
                    dev.get_feature_report(REPORT, 9)
                except (OSError, IOError, ValueError):
                    pass
            return True
        except (OSError, IOError, ValueError):
            return False

    def _listen(self, readers, ms: int) -> Optional[Tuple[int, bool]]:
        end = time.time() + ms / 1000.0
        while time.time() < end:
            for dev in readers:
                r = dev.read(64, READ_TIMEOUT_MS)
                if not r:
                    continue
                self._diag.append(f"  {hexdump(r, 8)}")
                parsed = parse_status(r)
                if parsed is not None:
                    return parsed
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
