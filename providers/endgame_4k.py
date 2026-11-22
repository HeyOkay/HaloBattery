"""Held XM2w 4k/v2 reader; explicit local testing only.

Protocol: A1 feature report on FF01:0002, sourced in endgame4k_protocol.py.
Public model evidence: https://github.com/nqble/HaloBattery/blob/a9e0ad7cd159f0445653d2d66e5f2b76c7ad0a65/docs/endgame/4k-findings.md
Those positive observations concern earlier builds and specific units.
Successful reply freshness and persistent status-08 recovery remain unresolved.
No process boundary, recovery command or normal polling enablement is added.
"""
try:
    import hid
except ImportError:  # pragma: no cover
    hid = None

from . import hidlist
from .base import Provider
from .endgame4k_protocol import (
    VID, RECEIVER, MODELS, IDENTITY, BATTERY, Reader, ProtocolError, identity, battery,
)

PIDS = {RECEIVER: "Endgame Gear 4K receiver",
        **{pid: "Endgame Gear " + model for pid, model in MODELS.items()}}
ENABLED_PIDS = frozenset()


def select_endpoint(inventory, transport):
    """Choose exactly one explicitly requested transport, never associate units."""
    if transport not in ("receiver", "wired"):
        raise ValueError("select receiver or wired transport explicitly")
    pids = (RECEIVER,) if transport == "receiver" else MODELS
    selected = {}
    for info in inventory:
        if not isinstance(info, dict):
            continue
        if (any(type(info.get(field)) is not int for field in
                ("vendor_id", "product_id", "usage_page", "usage"))
                or info.get("vendor_id") != VID or info.get("product_id") not in pids
                or (info.get("usage_page"), info.get("usage")) != (0xFF01, 2)):
            continue
        path = info.get("path")
        if not isinstance(path, (bytes, str)):
            continue
        try:
            normalized = path.decode("utf-8") if isinstance(path, bytes) else path
            normalized.encode("utf-8")
        except UnicodeError:
            continue
        if normalized and "\0" not in normalized:
            selected[normalized.casefold()] = info
    if len(selected) != 1:
        raise ProtocolError("expected exactly one endpoint on the selected transport")
    return next(iter(selected.values()))


class Endgame4kProvider(Provider):
    name = "endgame_4k"

    def __init__(self):
        self._diag = []

    def poll(self):
        # Deliberately no normal I/O path, even through generic --probe. The
        # release allowlist remains empty until a separate enabling review.
        self._diag = ["[Endgame 4K] release hold; not enumerating or opening"]
        return []

    def probe_experimental(self, transport, *, acknowledged=False):
        """One diagnostic sample, never cached or published as normal app status."""
        self._diag = ["[Endgame 4K] experimental; reply freshness unverified"]
        if acknowledged is not True:
            raise ValueError("experimental acknowledgement required before device access")
        if transport not in ("receiver", "wired"):
            raise ValueError("select receiver or wired transport explicitly")
        if hid is None:
            self._diag.append("HID backend unavailable")
            return None
        device = reader = None
        phase = "enumeration"
        try:
            endpoint = select_endpoint(hidlist.enumerate(VID), transport)
            phase = "open"
            device = hid.device()
            device.open_path(endpoint["path"])
            reader = Reader(device)
            phase = "identity"
            pid, firmware = identity(reader.exchange(IDENTITY))
            wired = transport == "wired"
            if wired and pid != endpoint["product_id"]:
                raise ProtocolError("cabled identity does not match selected device")
            phase = "battery"
            # One explicitly acknowledged B4 only. Repeated stale replies cannot
            # establish freshness; never resend or guess a recovery exchange.
            samples = [battery(reader.exchange(BATTERY))]
            return {"model": MODELS[pid], "mouse_pid": f"{pid:04x}",
                    "firmware_bytes": firmware, "transport": transport,
                    "percentage": None if wired else samples[-1],
                    "charging": True if wired else None, "charging_known": False,
                    "charging_basis": "USB connection inference" if wired else "unknown",
                    "samples": [] if wired else samples,
                    "freshness": "unverified"}
        except (OSError, ValueError, TypeError) as error:
            # Never echo device exception text, HID paths, serials or raw reports.
            detail = str(error) if isinstance(error, ProtocolError) else type(error).__name__
            self._diag.append(f"phase={phase}: {detail}")
            return None
        finally:
            if reader is not None:
                status = ("unknown" if reader.last_status is None
                          else f"0x{reader.last_status:02x}")
                self._diag.append(f"command={reader.command} reply_count={reader.reply_count} "
                                  f"length={reader.last_length} status={status}")
            if device is not None:
                try:
                    device.close()
                except (OSError, ValueError):
                    self._diag.append("native close incomplete")
                    # A native provider scope rejects publication on this error.
                    raise

    def diagnostics(self):
        return list(self._diag)
