"""LAMZU Tachi Aurora battery exchange; held from normal release polling.

Manufacturer Aurora text, retrieved 2026-11-17 (inert source, not executed):
https://www.xvalleyinno.top/LAMZU/static/js/index-4fe8914c.js
SHA256 5895a882e4e3570131044fa610a6e300ee835446c9909721f73b1e09951dd354
getBatPer / setReport / getReport: report0 + 64-byte payload, 100ms wait.
Config/env-models.json SHA256 f931e5e7c11b041f74f938aeeeaf214046fc9a64af2ca822ad5afaa06cd9128b
maps TACHI's 8K receiver to 37B0:000C and the non-CompX Aurora handler.
Issue #262's HID inventory supplies interface2 FFFF:0000 on that receiver.
Dorsal 55f702fc70ce852228cf2c7d20173541e4f17420, src/dorsal/protocol.py
244-260 and tools/virtual_mouse/{mice,fakehid,checkup}.py independently
exercise actual Tachi mouse firmware, but bypass USB and the real receiver.
Its official stock image SHA256 is
c9bc05370515f893981f56af52c9d1aacabf6213fd94302ed3090b3de50163e4.
No real Tachi receiver, radio, charging or recovery validation in this run.
No Glorious collection fallback, wired path or alternative report-size probes.
"""
import time

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None

from . import hidlist
from .base import Provider

VID, PID = 0x37B0, 0x000C
NAME = "Lamzu Tachi"
ENABLED_PIDS = frozenset()
REPORT_LEN = 65
RF_DELAY_S = .100
TRANSACTION_BUDGET = 4.0


class _DiagnosticError(ValueError):
    pass


def make_request():
    """Report ID 0 followed by Aurora's 64-byte battery payload."""
    return bytes.fromhex("00 00 00 02 02 00 83") + bytes(58)


def parse_reply(data):
    """(percentage, charging-or-None, charging-known) from the sourced layouts."""
    if (not isinstance(data, (bytes, bytearray, list, tuple))
            or any(type(byte) is not int or not 0 <= byte <= 255 for byte in data)):
        return None
    if len(data) == 65 and data[0] == 0:
        body = data[1:]
    elif len(data) == 64 and data[0] == 0xA1:
        body = data
    else:
        return None
    # Aurora's tag/cmd checks, plus Dorsal's echoed mouse target check.
    if (body[0] != 0xA1 or body[2] != 2 or body[3] != 2
            or body[5] != 0x83 or not 0 <= body[7] <= 100):
        return None
    flag = body[6]
    return body[7], (flag == 1 if flag in (0, 1) else None), flag in (0, 1)


def select_endpoint(inventory):
    selected = {}
    for info in inventory:
        fields = ("vendor_id", "product_id", "interface_number", "usage_page", "usage")
        if (any(type(info.get(field)) is not int for field in fields)
                or tuple(info.get(field) for field in fields) != (VID, PID, 2, 0xFFFF, 0)):
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
        raise _DiagnosticError("expected exactly one Tachi receiver status endpoint")
    return next(iter(selected.values()))


class LamzuSinowealthProvider(Provider):
    name = "lamzu_sw"

    def __init__(self, *, clock=None, sleep=None):
        self._diag = []
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep

    def poll(self):
        self._diag = ["[Lamzu Tachi] release hold; not enumerating or opening"]
        return []

    def _check(self, deadline):
        if self._clock() >= deadline:
            raise _DiagnosticError("diagnostic transaction budget exhausted")

    def probe_experimental(self, *, acknowledged=False):
        self._diag = ["[Lamzu Tachi] experimental; real receiver behavior unverified"]
        if acknowledged is not True:
            raise ValueError("experimental acknowledgement required before device access")
        if hid is None:
            self._diag.append("HID backend unavailable")
            return None
        device = None
        deadline = self._clock() + TRANSACTION_BUDGET
        phase = "enumeration"
        try:
            endpoint = select_endpoint(hidlist.enumerate(VID))
            self._check(deadline)
            phase = "open"
            device = hid.device()
            device.open_path(endpoint["path"])
            self._check(deadline)
            caps = device.get_report_lengths()  # immutable cached native metadata, no I/O
            feature = caps.get("feature") if isinstance(caps, dict) else None
            # The native transport bounds buffers to 32 KiB. Max caps can cover
            # other report IDs; the sourced report 0 request remains 65 bytes.
            if type(feature) is not int or not REPORT_LEN <= feature <= 32768:
                raise _DiagnosticError("collection cannot carry the sourced 65-byte feature report")
            phase = "send"
            self._check(deadline)
            if device.send_feature_report(make_request()) != REPORT_LEN:
                raise _DiagnosticError("feature request was not written in full")
            self._check(deadline)
            if self._clock() + RF_DELAY_S >= deadline:
                raise _DiagnosticError("diagnostic transaction budget exhausted")
            self._sleep(RF_DELAY_S)
            self._check(deadline)
            phase = "read"
            # Windows requires the collection's cached feature buffer length.
            # https://learn.microsoft.com/windows-hardware/drivers/ddi/hidsdi/nf-hidsdi-hidd_getfeature
            # This is one GET, not a size probe. Parse only the actual returned
            # canonical report; never truncate a larger response into one.
            data = device.get_feature_report(0, feature)
            self._check(deadline)
            reading = parse_reply(data)
            if reading is None:
                raise _DiagnosticError("no valid Aurora battery reply; payload not used")
            level, charging, known = reading
            return {"model": NAME, "transport": "receiver", "percentage": level,
                    "charging": charging, "charging_known": known,
                    "charging_basis": "Aurora reply flag" if known else "unknown flag",
                    "freshness": "unverified"}
        except (OSError, ValueError, TypeError, AttributeError) as error:
            detail = str(error) if isinstance(error, _DiagnosticError) else type(error).__name__
            self._diag.append(f"phase={phase}: {detail}")
            return None
        finally:
            if device is not None:
                try:
                    device.close()
                except (OSError, ValueError):
                    self._diag.append("native close incomplete")
                    raise

    def diagnostics(self):
        return list(self._diag)
