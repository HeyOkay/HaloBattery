"""Pulsar, ATK, VXE and Hitscan wireless mice over USB/HID, without vendor software.

Protocol from andrewrabert/python-pulsar-mouse-tool, which also backs the
"HID: pulsar" driver in review for the Linux kernel and lists these ids:

  * 3554:f508  Pulsar X2 V2 Mini (1 kHz dongle)      3554:f507  the same mouse on the cable
  * 3554:f58f  ATK VXE R1 SE+ (wired)                373b:1085  ATK VXE R1 SE+ (2.4 GHz)
  * 3554:f58a  VXE R1 Pro Max (1 kHz dongle, #87)    3554:f58c  the same mouse on its cable
  * 3710:5406  Pulsar X2 CrazyLight Mini (2.4 GHz)   3710:3414  the same mouse on the cable
  * the Kysona M600 and the VXE Dragonfly R1 Pro use the same protocol (their ids are
    not in the tool, so they are not claimed here).

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
1.0.2 in both cable (3770:0100) and receiver (3770:0200) mode and reads it with a plain
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

Frames are 17 bytes, big-endian, report id 0x08:

    [0] 0x08      header, which is also the report id
    [1] command   0x04 = power details
    [2..15]        arguments, zero for a power query
    [16] checksum 0x55 - the sum of bytes 0..15, mod 256

A power reply carries the level in byte 6, the power flag in byte 7 (the mouse is on
its cable) and millivolts big-endian in bytes 8-9. The same endpoint also pushes
events (command 0x0a), so a reply is only accepted when the header and the checksum
both check out, the command is the one we asked for, and the level is 0..100.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log
from .compx import (CHECKSUM_BASE, CMD_POWER, CONTROL_USAGE, PAYLOAD_HEADER, PAYLOAD_LEN,
                    POWER_INDEX, LEVEL_INDEX, VOLTAGE_SLICE, READ_ATTEMPTS, READ_TIMEOUT_MS,
                    checksum, make_request, output_length, parse_power, voltage_mv)

CONTROL_INTERFACE = 1           # the tool reads its 17-byte replies on interface 1
FLUSH_TIMEOUT_MS = 30

# vendor id -> product ids
PIDS: Dict[int, Dict[int, str]] = {
    0x3554: {
        0xF508: "Pulsar X2 V2 Mini (wireless)",
        0xF507: "Pulsar X2 V2 Mini (wired)",
        0xF58F: "ATK VXE R1 SE+ (wired)",
        0xF58A: "VXE R1 Pro Max (2.4 GHz)",
        0xF58C: "VXE R1 Pro Max (wired)",
    },
    0x373B: {
        0x1085: "ATK VXE R1 SE+ (2.4 GHz)",
    },
    0x3770: {
        0x0200: "Hitscan Hyperlight (2.4 GHz)",
        0x0100: "Hitscan Hyperlight (wired)",
    },
    0x3710: {
        0x5406: "Pulsar X2 CrazyLight Mini (2.4 GHz)",
        0x3414: "Pulsar X2 CrazyLight Mini (wired)",
    },
}


class PulsarProvider(Provider):
    name = "pulsar"

    def __init__(self):
        self._diag: List[str] = []
        # key -> (name, level, charging, when it was last read)
        self._last: Dict[str, Tuple[str, int, bool, float]] = {}

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

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        out = []
        now = time.time()
        found: set = set()      # answered this poll
        present: set = set()    # enumerated this poll, answering or not
        for vid, pids in PIDS.items():
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
                d = self._pick(mine)
                if d is None:
                    continue
                name = pids[pid]
                self._diag.append(f"[Pulsar] pid={vid:04x}:{pid:04x} '{name}' "
                                  f"iface={d.get('interface_number')} "
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}"
                                  f" output={output_length(d['path'])}")
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
                self._last[key] = (name, level, on_cable, now)
                found.add(key)
                out.append(DeviceStatus(key, name, level, on_cable, True, "pulsar",
                                        kind="mouse"))
        # A mouse that stopped answering is not necessarily a mouse that went away
        # (idle it stops answering - see the docstring), so its last reading stays
        # greyed out for as long as its receiver is still in the USB tree, however
        # long it sleeps. Only a receiver pulled out drops the device: Windows
        # re-creates a removed icon at a new tray position, which is what the
        # 2026-10-03 screen-off report was about.
        for key in list(self._last):
            name, level, charging, seen = self._last[key]
            if key in found:
                continue
            if key in present:
                self._diag.append(f"[Pulsar] {name} did not answer; keeping {level}%"
                                  f"{' (charging)' if charging else ''} greyed "
                                  f"(its receiver is still plugged in)")
                out.append(DeviceStatus(key, name, level, charging, False, "pulsar",
                                        kind="mouse", wake_on_input=True))
            else:
                del self._last[key]
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
