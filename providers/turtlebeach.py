"""Turtle Beach Stealth 700 Gen 3 over its USB transmitter, without Swarm II.

Drop this file into HaloBattery's ``providers/`` folder and register it in
``providers/__init__.py``::

    from .turtlebeach import TurtleBeachProvider  # noqa: F401

and add ``TurtleBeachProvider()`` to the provider list in the app.

Protocol (reverse-engineered from Swarm II 1.0.0.57 — full write-up in the
stealth700-battery repo):

  * The transmitter is an Airoha chip exposing the RACE protocol on its vendor
    HID collection (usage page 0xFF13 / usage 0x0001). It is picked by usage,
    never by interface number.
  * Battery lives behind a CoAP server on the chip. Swarm II reads it with CoAP
    GET requests tunnelled in RACE command 0x9902.
  * Transport: HID output report id 0x06 = ``06 <len16> <race...>`` (62 bytes);
    replies come back as input report id 0x07 with the same prefix.
  * RACE command = ``05 5A <len16> <id16> <payload>`` (0x5A expects a response,
    length counts the 2 id bytes). Response starts ``05 5B``.
  * Read status:
        1. enable the CoAP client:  RACE id 0x9942  (05 5A 02 00 42 99)
        2. CoAP GET /GSI in RACE id 0x9902:
               CoAP  40 01 00 01 B3 'GSI'
               RACE  05 5A 05 00 02 99 <coap>
        3. reply payload is JSON: {"OR":"GSI","KVP":{"240":"100", ...}}
           key 240 = battery %, 230 = connected, 220 = name.

Only the CoAP client is enabled (exactly what Swarm II does); nothing on the
device is written or reconfigured.
"""
from __future__ import annotations

import json
import re
import time
from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VENDOR_ID = 0x10F5
TRANSMITTER_PIDS = {0x2250, 0x2251, 0x2253}   # Stealth 700 Gen 3 transmitters
USAGE_PAGE = 0xFF13
USAGE = 0x0001

OUT_REPORT_ID = 0x06
IN_REPORT_ID = 0x07
REPORT_SIZE = 62

RACE_ID_COAP_CLIENT_ENABLE = 0x9942
RACE_ID_USB_COAP = 0x9902
STATUS_RESOURCE = "GSI"

KEY_NAME = "220"
KEY_CONNECTED = "230"
KEY_BATTERY = "240"
KEY_CHARGING = "250"

READ_ATTEMPTS = 30
POLL_DELAY = 0.03


def _race(cmd_id: int, payload: bytes = b"") -> bytes:
    length = len(payload) + 2
    return bytes([0x05, 0x5A, length & 0xFF, (length >> 8) & 0xFF,
                  cmd_id & 0xFF, (cmd_id >> 8) & 0xFF]) + payload


def _coap_get(path: str) -> bytes:
    option = bytes([0xB0 | len(path)]) + path.encode("ascii")
    return bytes([0x40, 0x01, 0x00, 0x01]) + option


class TurtleBeachProvider(Provider):
    name = "turtlebeach"

    def __init__(self):
        self._diag: List[str] = []
        self._last_level: Optional[int] = None
        self._last_name: str = "Turtle Beach headset"

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE:
                return d
        return None

    def _read_report(self, dev) -> bytes:
        rep = dev.get_input_report(IN_REPORT_ID, REPORT_SIZE)
        if not rep:
            return b""
        rep = bytes(rep)
        length = int.from_bytes(rep[1:3], "little")
        return rep[3:3 + length] if length else b""

    def _write(self, dev, race: bytes) -> None:
        buf = [OUT_REPORT_ID, len(race) & 0xFF, (len(race) >> 8) & 0xFF] + list(race)
        buf += [0] * (REPORT_SIZE - len(buf))
        dev.write(buf)

    def _get_gsi(self, path: bytes) -> Optional[dict]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            for _ in range(5):
                self._read_report(dev)
            self._write(dev, _race(RACE_ID_COAP_CLIENT_ENABLE))
            time.sleep(0.3)
            for _ in range(5):
                self._read_report(dev)
            self._write(dev, _race(RACE_ID_USB_COAP, _coap_get(STATUS_RESOURCE)))
            blob = b""
            for _ in range(READ_ATTEMPTS):
                blob += self._read_report(dev)
                time.sleep(POLL_DELAY)
        finally:
            dev.close()
        match = re.search(rb"\{.*\}", blob, re.S)
        if not match:
            self._diag.append(f"  no JSON in reply: {hexdump(blob)}")
            return None
        try:
            return json.loads(match.group().decode("utf-8", "replace"))
        except ValueError:
            return None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        infos = hidlist.enumerate(VENDOR_ID)
        info = self._pick(infos)
        if info is None:
            return []
        if info.get("product_id") not in TRANSMITTER_PIDS:
            # a Turtle Beach vendor collection we don't recognise; let another
            # provider (or none) handle it rather than guessing.
            self._diag.append(f"  unknown pid {info.get('product_id'):#06x}, skipping")
            return []

        key = f"turtlebeach:{info.get('product_id'):04x}"
        doc = self._get_gsi(info["path"])
        kvp = (doc or {}).get("KVP", {})

        # Prefer the transmitter's real USB product string (e.g. "Stealth 700X
        # Gen 3") over the on-device nickname (GSI key 220), which defaults to the
        # generic "My Headset".
        product = (info.get("product_string") or "").strip()
        name = product or kvp.get(KEY_NAME) or self._last_name
        self._last_name = name

        level: Optional[int] = None
        if KEY_BATTERY in kvp:
            try:
                level = max(0, min(100, int(kvp[KEY_BATTERY])))
                self._last_level = level
            except (TypeError, ValueError):
                level = None

        connected = kvp.get(KEY_CONNECTED) == "1"
        charging = kvp.get(KEY_CHARGING) == "1"

        if level is None:
            # transmitter present but no fresh reading: show the last level,
            # greyed out, like the other providers do for a sleeping device.
            level = self._last_level
            connected = False

        return [DeviceStatus(
            key=key,
            name=name,
            level=level,
            charging=charging,
            online=connected,
            source="turtlebeach",
            kind="headset",
        )]

    def diagnostics(self) -> List[str]:
        return self._diag
