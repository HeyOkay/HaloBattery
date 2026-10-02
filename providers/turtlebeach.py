"""Turtle Beach Stealth Pro II (10f5:229b): the wireless transmitter, which is also the dock.

The headset itself has no USB id; its battery reaches Windows only through the
transmitter. The transmitter speaks the same 62-byte vendor-report family as the Audeze
Maxwell - on the vendor collection (usage page 0xFF13, usage 0x0001) an output report 0x06
is answered on input report 0x07 - but its records are JSON documents with hex-numbered
keys instead of binary markers.

The reference is the vendor's own app: a capture of Swarm II talking to the transmitter
(issue #173) shows the app asking for each record by name with a 62-byte output report,
and the request for the "GSI" (general status info) record - its tail is the ASCII name
"a\\0SGSI" - is answered, split across the next input reports, with the document

    {"OR":"GSI","KVP":{"200":"1", ..., "220":"<name>", ..., "240":"<level>", ..., "2e0":"1"}}

Key "240" is the battery percentage: 86 in the capture, at the moment Swarm II's own
screen said "86%" for the same headset. Key "220" is the headset's name (what the user
renamed it to in Swarm II; "My Headset" out of the box). Each input report is framed
``07 <len> 00 <len bytes>``; the envelope around a record (the ``05 5b ..`` prefix and the
``61 .. 62 32 .. 21 01 ff`` marker before the JSON) sits between records and is skipped by
the JSON scan. The transmitter also pushes key updates by itself (``{"UP":"GSI",...}``
with only the changed keys), so a poll that reads a push carries it as well.

Reading sends the app's own GSI request, byte for byte; the request token after ``01 44``
(the capture's is ``14 04``) comes back echoed in the answer's envelope. The write is only
ever sent to the product ids in KNOWN and only to a vendor collection.

Unverified on hardware: no Stealth Pro II here, and the request is only known to have been
sent by the vendor's app so far. The tests pin the parser to the capture's frames.

The charging flag is not identified yet: the capture has the headset off the cable, and
none of the other GSI keys can be told apart as a charging state from one reading.
Diagnostics prints every GSI document received, so one run with the headset on the
charging cable pins it.
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

TURTLEBEACH_VID = 0x10F5

# PID -> name. 0x229B is the Stealth Pro II's transmitter; during a plug-in it enumerates
# twice (the first pass, dev 14 in the capture, has no ff13 collection yet), and only the
# full enumeration is read.
KNOWN = {
    0x229B: "Turtle Beach Stealth Pro II",
}

VENDOR_USAGE_PAGE = 0xFF13
VENDOR_USAGE = 0x0001

MSG_SIZE = 62
REPORT_ID_IN = 0x07
PACKET_DELAY = 0.060      # the cadence of the vendor app's own asks (and HeadsetControl's)
MAX_READS = 16            # the GSI record arrived within 4-5 replies in the capture

# Swarm II's request for the GSI record, as captured; the tail is the ASCII name the app
# addresses records by ("a\0SGSI" = ask SGSI). Only ever sent to the product ids in KNOWN.
ASK_GSI = bytes.fromhex(
    "061800055a1400029948014414040000"
    "00000000610053475349000000000000"
    "00000000000000000000000000000000"
    "0000000000000000000000000000"
)

GSI_KEY_LEVEL = "240"
GSI_KEY_NAME = "220"


def strip_frames(frames) -> bytes:
    """The payload bytes of the framed replies: ``07 <len> 00`` then ``len`` bytes.

    A reply that is not a framed input report (or shorter than its own length byte) is
    skipped rather than trusted.
    """
    out = bytearray()
    for f in frames:
        if len(f) < 3 or f[0] != REPORT_ID_IN:
            continue
        n = min(f[1], len(f) - 3)
        out += bytes(f[3:3 + n])
    return bytes(out)


def json_documents(blob) -> List[dict]:
    """Every JSON object in the payload stream, in order; envelope bytes are skipped.

    A bracket counter, not a regex: documents carry nested objects and the scan must stop
    at the document's own closing brace, not an inner one. Bytes outside printable ASCII
    cannot occur inside a document (the envelopes are always between them), so a control
    byte abandons that candidate and the scan moves on to the next opening brace.
    """
    text = bytes(blob)
    docs: List[dict] = []
    i = 0
    n = len(text)
    while True:
        s = text.find(b'{"', i)
        if s < 0:
            break
        depth = 0
        in_str = False
        esc = False
        end = None
        j = s
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == 0x5C:          # backslash
                    esc = True
                elif c == 0x22:          # closing quote
                    in_str = False
            elif c == 0x22:
                in_str = True
            elif c == 0x7B:              # {
                depth += 1
            elif c == 0x7D:              # }
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
            elif c < 0x20:
                break
            j += 1
        if end is None:
            i = s + 1
            continue
        try:
            docs.append(json.loads(text[s:end].decode("ascii")))
        except (ValueError, UnicodeDecodeError):
            pass
        i = end
    return docs


def status_from_documents(docs) -> Tuple[Optional[int], Optional[str]]:
    """-> (level, name): the newest GSI values for ``240``/``220`` seen, either may be None.

    A pushed update carries only the keys that changed (``{"UP":"GSI","KVP":{"290":"2"}}``
    was one in the capture), so the two keys are tracked separately and a document without
    one does not clear the other.
    """
    level: Optional[int] = None
    name: Optional[str] = None
    for d in docs:
        if (d.get("UP") or d.get("OR")) != "GSI":
            continue
        kvp = d.get("KVP")
        if not isinstance(kvp, dict):
            continue
        value = kvp.get(GSI_KEY_NAME)
        if isinstance(value, str) and value:
            name = value
        if GSI_KEY_LEVEL in kvp:
            try:
                v = int(kvp[GSI_KEY_LEVEL])
            except (TypeError, ValueError):
                v = None
            if v is not None and 0 <= v <= 100:
                level = v
    return level, name


def is_vendor_interface(d: dict) -> bool:
    return d.get("usage_page") == VENDOR_USAGE_PAGE and d.get("usage", 0) == VENDOR_USAGE


class TurtleBeachProvider(Provider):
    name = "turtlebeach"

    def __init__(self):
        self._diag: List[str] = []

    # ---- low level -------------------------------------------------------
    def _read_status(self, path) -> Tuple[Optional[int], Optional[str]]:
        """Ask the transmitter for the GSI record -> (level, name); either may be None."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    open: {e}")
            return None, None
        frames: List[bytes] = []
        level = None
        name = None
        try:
            try:
                dev.write(ASK_GSI)
            except (OSError, ValueError) as e:
                self._diag.append(f"    write: {e}")
                return None, None
            for _ in range(MAX_READS):
                time.sleep(PACKET_DELAY)
                try:
                    frame = dev.get_input_report(REPORT_ID_IN, MSG_SIZE)
                except (OSError, ValueError) as e:
                    self._diag.append(f"    read: {e}")
                    break
                if frame:
                    frames.append(bytes(frame))
                level, name = status_from_documents(json_documents(strip_frames(frames)))
                if level is not None:
                    break
            if level is not None:
                self._diag.append(f"    {len(frames)} replies, "
                                  f"{name or 'the headset'!r}: {level}%")
            else:
                self._diag.append(f"    no GSI record in {len(frames)} replies"
                                  + (f", last: {hexdump(frames[-1])}" if frames else ""))
            return level, name
        finally:
            try:
                dev.close()
            except Exception:
                pass

    # ---- high level ------------------------------------------------------
    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(TURTLEBEACH_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(turtlebeach): %s", e)
            return []
        groups: Dict[Tuple[int, str], List[dict]] = {}
        for d in infos:
            pid = d["product_id"]
            if pid not in KNOWN:
                name = (d.get("product_string") or "").strip()
                line = (f"[TurtleBeach] pid={pid:04x} '{name}' is not a known model, "
                        f"not talking to it")
                if line not in self._diag:
                    self._diag.append(line)
                continue
            groups.setdefault((pid, d.get("serial_number") or ""), []).append(d)

        out: List[DeviceStatus] = []
        for (pid, serial), ifaces in sorted(groups.items()):
            name = KNOWN.get(pid) or (ifaces[0].get("product_string") or
                                      f"TurtleBeach {pid:04x}").strip()
            self._diag.append(f"[TurtleBeach] {name} pid={pid:04x}, interfaces: {len(ifaces)}")
            # The vendor collection (usage page 0xFF13) is the only one that speaks this
            # protocol; the device's other collections (audio controls, a 000c:0001
            # consumer collection) answer nothing, so they are only tried when a device
            # has no 0xFF13 collection at all.
            cands = ([d for d in ifaces if is_vendor_interface(d)]
                     or [d for d in ifaces if d.get("usage_page") == VENDOR_USAGE_PAGE]
                     or [d for d in ifaces if (d.get("usage_page") or 0) >= 0xFF00])
            level = None
            if not cands:
                self._diag.append("    no vendor collection on this device "
                                  "(not writing to the standard collections)")
            else:
                for d in cands:
                    if len(cands) > 1:
                        self._diag.append(f"  iface={d.get('interface_number')} "
                                          f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                    level, _ = self._read_status(d["path"])
                    if level is not None:
                        break
            # No charging flag yet (see the module docstring), so the icon never claims a
            # charge. A None level shows the headset without a percentage - the signal
            # that it was seen but its level could not be read.
            out.append(DeviceStatus(f"turtlebeach:{serial}", name, level, False, True,
                                    "turtlebeach", kind="headset"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
