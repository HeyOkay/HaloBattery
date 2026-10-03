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

Reading replays the app's own asks, byte for byte: first the session opener (the "SInf"
ask, capture packet 150) - the device answers it with its whole state dump within about
30 ms (packets 151-168), and a record ask outside that session is met with idle frames
only, which is exactly what the first test builds saw on real hardware. Then the GSI ask
(packet 616): its length byte counts the 24 content bytes from byte 3 up to and
including the record name at bytes 21-26, and the request token after ``01 44`` comes
back echoed in the answer's envelope. The write is only ever sent to the product ids in
KNOWN and only to a vendor collection.

The headset itself, plugged in by its USB cable, enumerates as 10f5:229e with the same
vendor collection and is read the same way; both are one icon (FAMILY_KEY).

Unverified on hardware: no Stealth Pro II here. Two test builds on the reporter's unit
changed what is known: build 1's request was one byte short of the capture's, and build
2's byte-identical ask still got idle frames only - because it went out cold, without the
SInf opener. Both asks are now byte-identical to the capture, and a test pins their
offsets and tokens.

The charging state is the record's key `250`. The reporter's runs in #173 settled it by
diffing: on battery the full record reads {"230":"2","250":"0","280":"1","290":"2"},
on the cable {"230":"0","250":"1","280":"2","290":"4"} - the only strict 0/1 key
among the changes is 250, so that is the charging flag (230/280/290 move in enum-like
steps, transport or power source). A record without 250 counts as not charging.
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
# full enumeration is read. 0x229E is the headset itself on its USB cable - the same
# records over the same collection.
KNOWN = {
    0x229B: "Turtle Beach Stealth Pro II",
    0x229E: "Turtle Beach Stealth Pro II",
}

# One icon for the model: the transmitter and the headset on its own cable are the same
# headset (the rule the Cloud II / Cloud III work set), and either source alone is enough.
FAMILY_KEY = "turtlebeach:stealthpro2"

VENDOR_USAGE_PAGE = 0xFF13
VENDOR_USAGE = 0x0001

MSG_SIZE = 62
REPORT_ID_IN = 0x07
PACKET_DELAY = 0.060      # the cadence of the vendor app's own asks (and HeadsetControl's)
MAX_READS = 16            # the GSI record arrived within 4-5 replies in the capture
SINF_READS = 10           # the state dump came as 8 frames within 30 ms in the capture

# Swarm II's request for the GSI record, as captured (packet 616): output report 6, 62
# bytes. Byte 1 is the content length (24), counted from byte 3 up to and including the
# record name; the name sits at bytes 21-26 ("a\0SGSI" = ask SGSI) and byte 27 is 0.
# Only ever sent to the product ids in KNOWN.
ASK_GSI = bytes.fromhex(
    "061800055a1400029948014414040000"
    "00000000006100534753490000000000"
    "00000000000000000000000000000000"
    "0000000000000000000000000000"
)

# The same ask with the token a fresh session would use: the capture's 44-type asks
# carry (0x10 + n, n) with n counting them (280 is the first, `11 01`; packet 616 is
# the fourth, `14 04`), so when the captured token is not the one a new session takes,
# this is the shape it starts at. Both were tried against the device in turn.
ASK_GSI_FRESH = ASK_GSI[:12] + bytes([0x11, 0x01]) + ASK_GSI[14:]

# Swarm II's first ask, "SInf" (session info), byte for byte from capture packet 150;
# the device answers it with its whole state dump, and without it a record ask is met
# with idle frames only (#173, test builds 1 and 2).
ASK_SINF = bytes.fromhex(
    "061800055a140002994801c04a010000"
    "0000000000610053496e660000000000"
    "00000000000000000000000000000000"
    "0000000000000000000000000000"
)

GSI_KEY_LEVEL = "240"
GSI_KEY_NAME = "220"
GSI_KEY_CHARGING = "250"             # the charging state (see the module docstring)


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


def charging_from_documents(docs) -> Optional[bool]:
    """The charging state from a status record, or None when no document carried
    key 250. The newest document with the key wins; partial updates without it leave
    the state as Not-answered (the icon then simply does not claim a charge)."""
    for doc in reversed(docs):
        if (doc.get("UP") or doc.get("OR")) != "GSI":
            continue
        kvp = doc.get("KVP")
        if not isinstance(kvp, dict):
            continue
        value = kvp.get(GSI_KEY_CHARGING)
        if value is not None:
            return str(value) == "1"
    return None


def _status_record(frames) -> Optional[dict]:
    """The newest status document of a read, for the diagnostics: printing the whole
    record on a successful read is what lets two runs - on battery and on the cable -
    be compared key by key, so the charging key can be told apart from the rest."""
    docs = json_documents(strip_frames(frames))
    for doc in reversed(docs):
        kvp = doc.get("KVP")
        if isinstance(kvp, dict) and (GSI_KEY_LEVEL in kvp or GSI_KEY_NAME in kvp):
            return doc
    return docs[-1] if docs else None


def is_vendor_interface(d: dict) -> bool:
    return d.get("usage_page") == VENDOR_USAGE_PAGE and d.get("usage", 0) == VENDOR_USAGE


class TurtleBeachProvider(Provider):
    name = "turtlebeach"

    def __init__(self):
        self._diag: List[str] = []

    # ---- low level -------------------------------------------------------
    def _read_status(self, path) -> Tuple[Optional[int], Optional[str], Optional[bool]]:
        """Ask the device for the GSI record -> (level, name, charging); any may be
        None (the level and the name are what a read is taken for)."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    open: {e}")
            return None, None, None
        frames: List[bytes] = []
        level = None
        name = None
        try:
            # the session opener first, then the GSI ask; the dump may already carry
            # the level (a pushed 240), in which case no record ask is needed
            for tag, request, reads in (("SInf", ASK_SINF, SINF_READS),
                                        ("GSI", ASK_GSI, MAX_READS),
                                        ("GSI fresh token", ASK_GSI_FRESH, MAX_READS)):
                if level is not None:
                    break
                try:
                    sent = dev.write(request)
                except (OSError, ValueError) as e:
                    self._diag.append(f"    {tag} write: {e}")
                    return level, name, None
                if sent != len(request):
                    self._diag.append(f"    {tag} write returned {sent} of {len(request)} bytes")
                    continue
                got = 0
                for _ in range(reads):
                    time.sleep(PACKET_DELAY)
                    try:
                        frame = dev.get_input_report(REPORT_ID_IN, MSG_SIZE)
                    except (OSError, ValueError) as e:
                        self._diag.append(f"    {tag} read: {e}")
                        break
                    if frame:
                        frames.append(bytes(frame))
                        got += 1
                    level, name = status_from_documents(json_documents(strip_frames(frames)))
                    if level is not None:
                        break
                self._diag.append(f"    {tag}: {got} replies")
            charging = charging_from_documents(json_documents(strip_frames(frames)))
            if level is not None:
                self._diag.append(f"    -> {name or 'the headset'!r}: {level}%"
                                  + (", charging" if charging else ""))
                record = _status_record(frames)
                if record is not None:
                    self._diag.append("    status record: "
                                      + json.dumps(record, separators=(",", ":")))
            else:
                self._diag.append(f"    no GSI record in {len(frames)} replies"
                                  + (f", last: {hexdump(frames[-1])}" if frames else ""))
            return level, name, charging
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

        results: List[Tuple[Optional[int], Optional[bool]]] = []
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
            charging = None
            if not cands:
                self._diag.append("    no vendor collection on this device "
                                  "(not writing to the standard collections)")
            else:
                for d in cands:
                    if len(cands) > 1:
                        self._diag.append(f"  iface={d.get('interface_number')} "
                                          f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                    level, _, charging = self._read_status(d["path"])
                    if level is not None:
                        break
            results.append((level, charging))

        if not results:
            return []
        # One icon for the model (#155/#192): whether the transmitter or the headset's own
        # cable answers, it is the same headset; the first (in pid order) that carries a
        # level wins, with its charging state (key 250, module docstring). A None level
        # shows the headset without a percentage - the signal that it was seen but its
        # level could not be read.
        shown = next((lv for lv, _ in results if lv is not None), None)
        charging = next((ch for lv, ch in results if lv is not None), None)
        self._diag.append("  -> " + FAMILY_KEY + (f": {shown}%" if shown is not None
                                                   else ": no level from any source")
                          + (", charging" if charging else ""))
        return [DeviceStatus(FAMILY_KEY, "Turtle Beach Stealth Pro II", shown,
                             bool(charging), True, "turtlebeach", kind="headset")]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
