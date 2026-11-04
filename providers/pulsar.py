"""Pulsar, ATK, VXE, Hitscan and Redragon wireless mice over USB/HID, without software.

Protocol from andrewrabert/python-pulsar-mouse-tool, which also backs the
"HID: pulsar" driver in review for the Linux kernel and lists these ids:

  * 3554:f508  Pulsar X2 V2 Mini (1 kHz dongle)      3554:f507  the same mouse on the cable
  * 3554:f58f  ATK VXE R1 SE+ (wired)                373b:1085  ATK VXE R1 SE+ (2.4 GHz)
  * 373b:11b6  ATK A9 Ultimate (wired)               373b:11d9  ATK A9 Ultimate (2.4 GHz dongle)
  * 3554:f58a  VXE R1 Pro Max (1 kHz dongle, #87)    3554:f58c  the same mouse on its cable
  * 3710:5406  Pulsar X2 CrazyLight Mini (2.4 GHz)   3710:3414  the same mouse on the cable
  * the Kysona M600 and the VXE Dragonfly R1 Pro use the same protocol (their ids are
    not in the tool, so they are not claimed here).

G-Wolves' shared 8K receiver (33e4:3854) is claimed from its owner's own probe (#82,
#297): the power query below, sent to its ff02:0002 collection, got the reply
08 04 00 00 00 02 50 00 0f ad .. 3b (80 %, not charging, 4013 mV, valid checksum).
G-Wolves' web driver lists this receiver for its feature-report exchange
(providers/gwolves.py), but this unit has no collection with that feature report.

The cable id (3554:f58c) is claimed too, from the reporter's second report in #87: the
wired mouse lists the same eight collections as the receiver, and the panel below reads it
with the same command 0x04 frame. Both transports are confirmed on that reporter's
hardware - the receiver read the mouse's level, and on the cable the level agreed with
ATK's own panel (hub.atk.pro) and the charging flag followed the cable.

The ATK and Compx builds are also handled by the OpenMouse project's ATK/VXE panel
(@openmouse/protocol, drivers/atk): it lists 3554:f58a for the R1 Pro Max receiver
and 3554:f58c for the same mouse on its cable, and reads the battery with command
0x04, taking the level, the flag and the millivolts from the same three places this
file does. It also opens the collection with usage page 0xFF02 and usage 0x0002,
which is why that one is preferred below.

The same 17-byte framing is in G-Wolves' own web driver (mouse.xyz), which also handles
these Compx-based receivers: its get_Crc() is 0x55 minus the sum of the first fifteen
payload bytes, the result goes in byte 15, and the frame is sent with sendReport(8, ...) -
so the frame on the wire sums to 0x55, the rule this file uses. (Its battery read is for
the G-Wolves protocol; the level offsets here rest on the two sources above.)

The Hitscan Hyperlight speaks the same frame, on the same kind of vendor collection
(ff02:0002): sopparus/hitscan-battery mapped it from USBPcap captures of Hitscan Utility
1.0.2 in both cable (3770:0100) and receiver (3770:0200) mode, and Hitscan Utility's
own configuration (Wireless8_PID = MDMwMA== in Sys64/usersetting/Config.ini from
hitscan.com/download) confirms the 8K receiver (3770:0300), verified on hardware in
#158. It reads it with a plain
write()/read(), command 0x04, the level in byte 6, the flag in byte 7, checksum 0x55 minus
the sum. Its notes left byte 8 and byte 9 unresolved; across its captures those read
0x1129 (4393 mV) while charging and 0x1073 (4211 mV) on battery - the big-endian millivolt
field this file already reads. Its own warning also applies: the vendor application's
battery indicator is broken (it showed 100 % while the device answered 75), so the raw
byte is the truth, which is what this file reports.

A mouse that is idle stops answering - the receiver goes quiet a few seconds after the
last movement, and the wired collection does the same while the mouse sits on its
cable - so a silent poll is a normal state and not a device that went away. The last
reading is kept greyed out for as long as the mouse's receiver stays in the USB tree,
however long the mouse sleeps, and it refreshes at the first poll the mouse answers
again; only a receiver that leaves the tree drops the device, icon included. #87
first showed the icon vanishing around the mouse's cable, then that after the mouse's
own deep stop-mode (10-15 idle minutes) the reading took a long time to come back; a
later screen-off report showed that dropping the icon also cost it its tray position
(Windows puts a re-created icon at a new spot), so the greyed value no longer
expires on a timer.

The Redragon M991 (#183) speaks the same stack: vendor id 0x3554 is Compx's ODM
id, shared across ATK/VXE, GravaStar, Lamzu's Atlantis generation, Teevolution/VGN
and the Pulsar receivers - the OpenMouse package documents them as one report-8
framing - and the reporter's dump lists the same collection shape as the R1 Pro Max
receiver, ff02:0002 included. Both of his transports are confirmed on hardware: the
receiver showed the level (95 %, matching his own software) and, after the cable fix,
the icon switches to the wired mouse with the charging ring at the same level while
the cable is in, and returns to the receiver's reading when it is unplugged. On its
cable the mouse appears as 3554:f55e ('3-mode mouse') and is claimed the way the
R1 Pro Max cable id is: it lists the same vendor collections (the charging dump adds
ff05:0000) and answers the same command 0x04 frame, which is where the charging flag
comes from - on the cable the mouse leaves the 2.4 GHz link and the receiver goes
quiet, so without the wired id the icon simply disappeared (#183).

The same receiver also carries the Redragon K1NG Max (M918) (#197), and it answers
the same frames: both the level and the charging flag come through the receiver
itself while its cable is in (45 % wireless and 100 % charging, both on the
reporter's hardware), so this row's name now covers both mice.

Frames are 17 bytes, big-endian, report id 0x08:

    [0] 0x08      header, which is also the report id
    [1] command   0x04 = power details
    [2..15]        arguments, zero for a power query
    [16] checksum 0x55 - the sum of bytes 0..15, mod 256

A power reply carries the level in byte 6, the power flag in byte 7 (the mouse is on
its cable) and millivolts big-endian in bytes 8-9. The same endpoint also pushes
events (command 0x0a), so a reply is only accepted when the header and the checksum
both check out, the command is the one we asked for, and the level is 0..100.

Additional measured CompX IDs: Scyrox 3554:F5F7/F5F6 (#182), ATK A9 SE
373B:10C9 (#347), Cougar Revenger Pro 3554:F5DF/F5DE/F5DC (#354).
Pulsar X2 V3 Mini uses the Sonix feature protocol from jonkristian/pulsar-x3-python
(#213). NK NANO 373B:1216 is dispatched separately to atk_bitmouse (#350).
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import hid

from . import atk_bitmouse, hidlist
from .base import DeviceStatus, Provider, hexdump, log
from .compx import (CHECKSUM_BASE, CMD_POWER, CONTROL_USAGE, PAYLOAD_HEADER, PAYLOAD_LEN,
                    POWER_INDEX, LEVEL_INDEX, VOLTAGE_SLICE, READ_ATTEMPTS, READ_TIMEOUT_MS,
                    checksum, make_request, output_length, parse_power, voltage_mv)

CONTROL_INTERFACE = 1           # the tool reads its 17-byte replies on interface 1
FLUSH_TIMEOUT_MS = 30

SONIX_VID = 0x3710
SONIX_PIDS = {
    0x5403: ("Pulsar X2 V3 Mini (8K wireless)", False),
    0x3402: ("Pulsar X2 V3 Mini (wired)", True),
}
SONIX_INTERFACE = 3
SONIX_WIRE_LEN = 64
SONIX_REPORT_LEN = 65          # hidapi report id + the 64-byte USB control payload
SONIX_BATTERY_COMMAND = (0x08, 0x81, 0x01)
SONIX_LEVEL_INDEX = 7          # wire byte 6, shifted by hidapi's report-id byte


# vendor id -> product ids
PIDS: Dict[int, Dict[int, str]] = {
    0x3554: {
        0xF5F7: "Scyrox 8K Dongle",
        0xF5F6: "Scyrox (wired)",
        0xF5DF: "Cougar Revenger Pro 4K",
        0xF5DE: "Cougar Revenger Pro 4K (wired)",
        0xF5DC: "Cougar Revenger Pro (2.4 GHz)",
        0xF508: "Pulsar X2 V2 Mini (wireless)",
        0xF507: "Pulsar X2 V2 Mini (wired)",
        0xF58F: "ATK VXE R1 SE+ (wired)",
        0xF58A: "VXE R1 Pro / R1 Pro Max (2.4 GHz)",
        0xF58C: "VXE R1 Pro Max (wired)",
        0xF505: "VGN Dragonfly 4K (receiver)",
        0xF506: "VGN Dragonfly 4K (wired)",
        0xF5D5: "Redragon M991 / K1NG Max (2.4 GHz)",
        0xF55E: "Redragon M991 (wired)",
        0xFA09: "Compx 2.4 GHz keyboard",
        0xF523: "Teevolution Terra Pro (2.4 GHz)",
    },
    0x1B1C: {
        0x2B2A: "Corsair Sabre v2 Pro Ultralight",
    },
    0x373B: {
        0x11B6: "ATK A9 Ultimate (wired)",
        0x11D9: "ATK A9 Ultimate (2.4 GHz dongle)",
        0x10C9: "ATK A9 SE (2.4 GHz)",
        0x1085: "ATK VXE R1 SE+ (2.4 GHz)",
        0x101A: "ATK X1 Pro (2.4 GHz)",
        0x101B: "ATK Fire X 8K (2.4 GHz dongle)",
    },
    0x3770: {
        0x0300: "Hitscan Hyperlight (8K receiver)",
        0x0200: "Hitscan Hyperlight (2.4 GHz)",
        0x0100: "Hitscan Hyperlight (wired)",
    },
    0x3710: {
        0x5406: "Pulsar X2 CrazyLight Mini (2.4 GHz)",
        0x3414: "Pulsar X2 CrazyLight Mini (wired)",
    },
    # G-Wolves' shared 8K receiver: the WARG's owner ran the read-only probe from #82
    # on his unit (#297). This frame on ff02:0002 got a valid power reply (80 %, 4013
    # mV); the receiver has no 64-byte feature report, so gwolves.py sends nothing to it.
    0x33E4: {
        0x3854: "G-Wolves mouse (8K receiver)",
    },
}

# ids whose pictogram is not a mouse: the Compx exchange is shared with keyboards,
# and the AULA F75's receiver publishes the same ff02:0002 collection (#247)
KINDS: Dict[Tuple[int, int], str] = {(0x3554, 0xFA09): "keyboard",
                                     (0x3554, 0xF523): "keyboard",
                                     (0x1B1C, 0x2B2A): "mouse"}


def make_sonix_request() -> List[int]:
    """hidapi form of the X2 V3's 64-byte report-0 feature request.

    The first zero is hidapi's report id; the second starts the 64-byte packet that
    the libusb reference sends. Its checksum stays at wire bytes 62-63.
    """
    wire = ([0x00] + list(SONIX_BATTERY_COMMAND) +
            [0x00] * (SONIX_WIRE_LEN - len(SONIX_BATTERY_COMMAND) - 3))
    check = sum(wire) & 0xFFFF
    wire += [check & 0xFF, check >> 8]
    return [0x00] + wire


def parse_sonix_power(response) -> Optional[int]:
    """Battery percentage from a Sonix response; reject missing/bogus values."""
    if not response or len(response) <= SONIX_LEVEL_INDEX:
        return None
    if tuple(response[2:5]) != SONIX_BATTERY_COMMAND:
        return None
    level = response[SONIX_LEVEL_INDEX]
    return level if 0 <= level <= 100 else None


class PulsarProvider(Provider):
    name = "pulsar"

    def __init__(self):
        self._diag: List[str] = []
        # key -> (name, level, charging, when it was last read, pictogram)
        self._last: Dict[str, Tuple[str, int, bool, float, str]] = {}

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        """The collection to open.

        The ATK/VXE panel of the OpenMouse project opens the collection with usage
        page 0xFF02 and usage 0x0002 and sends its report-0x08 frames there, so that
        one is tried first - the R1 Pro Max dongle has it next to four other
        interface-1 collections, and the first of those is not the one it answers on.
        Without it, the first interface-1 collection is used, which is where the
        reference tool reads its replies.

        A collection whose output report cannot carry the 17-byte frame is skipped
        even when it has the right usage: Windows refuses that write and no reply
        comes, which is indistinguishable from a device that is off. The length is
        only known when Windows says so, and an unknown length never disqualifies a
        collection. The probe is the same read-only HidP_GetCaps query that
        providers/gwolves.py uses for its feature length, cached per collection path.
        (Spotted by ahmedkhursheed23 in #87, from mouse.xyz's writeFile().)
        """
        control = [d for d in infos
                   if (d.get("usage_page"), d.get("usage")) == CONTROL_USAGE]
        for d in control + infos:
            if output_length(d["path"]) in (None, PAYLOAD_LEN):
                if not control:
                    self._diag.append("  no control collection; using "
                                      f"iface={d.get('interface_number')} "
                                      f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
                                      f"(output={output_length(d['path'])})")
                return d
            self._diag.append(f"  {d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
                              f"output={output_length(d['path'])} cannot take a "
                              f"{PAYLOAD_LEN}-byte frame; skipped")
        if control:
            return control[0]
        self._diag.append(f"  no interface {CONTROL_INTERFACE} collection; "
                          f"falling back to the first of {len(infos)}")
        return infos[0] if infos else None

    def _query(self, path: bytes) -> Optional[List[int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            self._drain(dev)
            dev.write(make_request())
            for attempt in range(READ_ATTEMPTS):
                r = dev.read(PAYLOAD_LEN, READ_TIMEOUT_MS)
                if not r:
                    break
                self._diag.append(f"  reply {attempt + 1}: {hexdump(r)}")
                if parse_power(r) is not None:
                    return list(r)
                time.sleep(0.02)
            self._diag.append("  no power reply (events and short frames are ignored)")
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _drain(self, dev) -> None:
        for _ in range(4):
            if not dev.read(PAYLOAD_LEN, FLUSH_TIMEOUT_MS):
                return

    def _query_sonix(self, path: bytes) -> Optional[int]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            try:
                n = dev.send_feature_report(make_sonix_request())
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  feature send: {e}")
                return None
            if n is not None and n < 0:
                self._diag.append("  feature send: refused")
                return None
            time.sleep(0.05)
            try:
                response = dev.get_feature_report(0, SONIX_REPORT_LEN)
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  feature read: {e}")
                return None
            self._diag.append(f"  feature reply: {hexdump(response, 16)}")
            return parse_sonix_power(response)
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _poll_sonix(self) -> List[DeviceStatus]:
        out = []
        try:
            infos = hidlist.enumerate(SONIX_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(%04x): %s", SONIX_VID, e)
            return out
        for pid, (name, wired) in SONIX_PIDS.items():
            mine = [d for d in infos if d.get("product_id") == pid]
            if not mine:
                continue
            controls = [d for d in mine if d.get("interface_number") == SONIX_INTERFACE]
            if not controls:
                self._diag.append(f"[Pulsar Sonix] pid={SONIX_VID:04x}:{pid:04x} "
                                  f"'{name}': no interface {SONIX_INTERFACE}")
                continue
            d = next((x for x in controls if x.get("usage_page", 0) >= 0xFF00), controls[0])
            self._diag.append(f"[Pulsar Sonix] pid={SONIX_VID:04x}:{pid:04x} '{name}' "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            level = self._query_sonix(d["path"])
            if level is not None:
                out.append(DeviceStatus(f"pulsar:{SONIX_VID:04x}{pid:04x}", name, level,
                                        wired, True, "pulsar", kind="mouse"))
        return out

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        out = self._poll_sonix()
        now = time.time()
        found: set = set()      # answered this poll
        present: set = set()    # enumerated this poll, answering or not
        protocols = {vid: dict(pids) for vid, pids in PIDS.items()}
        for (vid, pid), name in atk_bitmouse.PIDS.items():
            protocols.setdefault(vid, {})[pid] = name
        for vid, pids in protocols.items():
            try:
                infos = hidlist.enumerate(vid)
            except Exception as e:  # pragma: no cover
                log.warning("hid.enumerate(%04x): %s", vid, e)
                continue
            for pid in pids:
                mine = [d for d in infos if d["product_id"] == pid]
                if not mine:
                    continue
                key = f"pulsar:{vid:04x}{pid:04x}"
                present.add(key)
                bitmouse = (vid, pid) in atk_bitmouse.PIDS
                d = atk_bitmouse.pick(mine) if bitmouse else self._pick(mine)
                if d is None:
                    self._diag.append(f"[Pulsar] pid={vid:04x}:{pid:04x}: no collection "
                                      f"to read among {len(mine)}")
                    continue
                name = pids[pid]
                self._diag.append(f"[Pulsar] pid={vid:04x}:{pid:04x} '{name}' "
                                  f"iface={d.get('interface_number')} "
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}"
                                  f" output={output_length(d['path'])}")
                if bitmouse:
                    reply = None
                    parsed = atk_bitmouse.read_power(d["path"], self._diag)
                else:
                    reply = self._query(d["path"])
                    parsed = parse_power(reply)
                if parsed is None:
                    if not reply:
                        self._diag.append("    no reply (asleep, or the mouse is idle)")
                    continue
                level, on_cable = parsed
                mv = voltage_mv(reply)
                if mv:
                    self._diag.append(f"  {mv} mV")
                kind = KINDS.get((vid, pid), "mouse")
                self._last[key] = (name, level, on_cable, now, kind)
                found.add(key)
                out.append(DeviceStatus(key, name, level, on_cable, True, "pulsar",
                                        kind=kind))
        # A mouse that stopped answering is not necessarily a mouse that went away
        # (idle it stops answering - see the docstring), so its last reading stays
        # greyed out for as long as its receiver is still in the USB tree, however
        # long it sleeps. Only a receiver pulled out drops the device: Windows
        # re-creates a removed icon at a new tray position, which is what the
        # 2026-10-03 screen-off report was about.
        for key in list(self._last):
            name, level, charging, seen, kind = self._last[key]
            if key in found:
                continue
            if key in present:
                self._diag.append(f"[Pulsar] {name} did not answer; keeping {level}%"
                                  f"{' (charging)' if charging else ''} greyed "
                                  f"(its receiver is still plugged in)")
                out.append(DeviceStatus(key, name, level, charging, False, "pulsar",
                                        kind=kind, wake_on_input=True))
            else:
                del self._last[key]
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
