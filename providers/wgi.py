"""Controller battery through Windows.Gaming.Input (the API the Xbox Accessories
app uses). Many third-party Xbox-protocol controllers, e.g. the GameSir G7 Pro
on its 2.4 GHz receiver, never report their battery through XInput
(BatteryType stays "disconnected"), but Windows.Gaming.Input returns a real
BatteryReport: remaining / full charge capacity and the charging status.

The WinRT API is reached through PowerShell (like the Bluetooth provider), so
no extra Python packages are needed. In a fresh process the controller list
fills asynchronously, so the script waits briefly for it.

GameSir Nova 2 Lite receiver 3537:1098 has one hardware-confirmed false full
report: Discharging, remain=full=1000, rate=None. The owner compared Windows'
100% with GameSir Connect's 69% (#142, comment 5879245780); probe output in
comment 5885746260 records the exact IDs/status/capacities. The narrow fix was
already published in casparas123/HaloBattery at
f2e6a834dde1aa829f708a16f473b5f18718f752 (gamesir-nova2-test1):
https://github.com/HeyOkay/HaloBattery/issues/142#issuecomment-5879245780
https://github.com/casparas123/HaloBattery/commit/f2e6a834dde1aa829f708a16f473b5f18718f752
Refuse only that report on its own WGI identity, without assigning it to an
XInput slot. Other vendors' 1000/1000 can be genuine full capacity (#216 review).
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import List, Optional

PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
  $null = [Windows.Gaming.Input.RawGameController, Windows.Gaming.Input, ContentType = WindowsRuntime]
  $null = [Windows.Gaming.Input.Gamepad, Windows.Gaming.Input, ContentType = WindowsRuntime]
  # the controller list fills asynchronously in a new process
  for ($i = 0; $i -lt 30; $i++) {
    if ([Windows.Gaming.Input.RawGameController]::RawGameControllers.Count -gt 0) { break }
    Start-Sleep -Milliseconds 100
  }
  Start-Sleep -Milliseconds 300
  $list = @()
  foreach ($c in [Windows.Gaming.Input.RawGameController]::RawGameControllers) {
    # RawGameControllers lists every controller Windows knows, including wheels, flight
    # sticks and other devices XInput cannot see. Only the ones Windows can also give out
    # as a Gamepad are XInput pads and belong in this list.
    try { if (-not [Windows.Gaming.Input.Gamepad]::FromGameController($c)) { continue } } catch { }
    $b = $null
    try { $b = $c.TryGetBatteryReport() } catch { }
    $list += [pscustomobject]@{
      name     = "$($c.DisplayName)"
      vid      = [int]$c.HardwareVendorId
      pid      = [int]$c.HardwareProductId
      wireless = [bool]$c.IsWireless
      status   = $(if ($b) { "$($b.Status)" } else { $null })
      remain   = $(if ($b) { $b.RemainingCapacityInMilliwattHours } else { $null })
      full     = $(if ($b) { $b.FullChargeCapacityInMilliwattHours } else { $null })
      rate     = $(if ($b) { $b.ChargeRateInMilliwatts } else { $null })
    }
  }
  ConvertTo-Json -InputObject ([pscustomobject]@{ controllers = @($list) }) -Compress -Depth 4
} catch {
  ConvertTo-Json -InputObject ([pscustomobject]@{ error = "$_" }) -Compress
}
"""

# generic names Windows gives most Xbox-protocol controllers; a better name is
# derived from the hardware vendor id instead
GENERIC_NAMES = {"", "xbox controller", "xbox one controller", "controller (xbox one for windows)",
                 "xbox wireless controller"}
VENDOR_NAMES = {0x3537: "GameSir controller", 0x045E: "Xbox controller"}


def capacity_level(remaining, full) -> Optional[int]:
    """Validate WinRT's nullable Int32 capacities before computing a percentage.

    BatteryReport.RemainingCapacityInMilliwattHours and FullChargeCapacityInMilliwattHours:
    https://learn.microsoft.com/uwp/api/windows.devices.power.batteryreport
    Never turn damaged driver/protocol values into a plausible full battery.
    """
    if (type(remaining) is not int or type(full) is not int
            or not 0 <= remaining <= full <= 0x7FFFFFFF or full == 0):
        return None
    return round(remaining * 100 / full)


class WgiController:
    def __init__(self, raw: dict):
        self.raw = raw
        self.vid = raw.get("vid") if type(raw.get("vid")) is int and 0 <= raw["vid"] <= 0xFFFF else 0
        self.pid = raw.get("pid") if type(raw.get("pid")) is int and 0 <= raw["pid"] <= 0xFFFF else 0
        self.status = raw["status"].lower() if isinstance(raw.get("status"), str) else ""
        self.charging = self.status == "charging"
        remain, full = raw.get("remain"), raw.get("full")
        self.level = capacity_level(remain, full) if self.status != "notpresent" else None
        # Windows synthesizes remain=full=1000 mWh for a pad whose battery it only knows
        # as "full": the #110 dock and the Vader 4 Pro (#191, really low battery) both
        # reported exactly this. This shape is ambiguous for other models:
        # #216 explicitly retained genuine full reports from other vendors.
        # Only confirmed model-specific rules discard this candidate.
        self.placeholder = bool(self.level is not None and remain == 1000 and full == 1000
                                and raw.get("rate") is None)
        if (self.placeholder and (self.vid, self.pid) == (0x3537, 0x1098)
                and self.status == "discharging"):
            self.level = None
        name = raw["name"].strip() if isinstance(raw.get("name"), str) else ""
        # "HID-compliant game controller" and its translations: a generic HID name
        if "hid" in name.lower():
            name = VENDOR_NAMES.get(self.vid, "")        # "" -> the provider's own name
        elif name.lower() in GENERIC_NAMES:
            name = VENDOR_NAMES.get(self.vid, name or "")
        self.name = name

    def describe(self) -> str:
        return (f"'{self.raw.get('name')}' {self.vid:04x}:{self.pid:04x} status={self.raw.get('status')} "
                f"remain={self.raw.get('remain')} full={self.raw.get('full')} rate={self.raw.get('rate')}")


def parse(raw: str, diag: List[str]) -> Optional[List[WgiController]]:
    if not raw:
        diag.append("[WGI] empty PowerShell output")
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        diag.append(f"[WGI] could not parse the output: {raw[:200]}")
        return None
    if not isinstance(data, dict):
        diag.append("[WGI] output is not an object")
        return None
    if data.get("error"):
        diag.append(f"[WGI] unavailable: {str(data['error'])[:200]}")
        return None
    items = data.get("controllers")
    if items is None:
        items = []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        diag.append("[WGI] invalid controller list")
        return None
    ctrls = []
    for item in items:
        if not isinstance(item, dict):
            diag.append("[WGI] ignored invalid controller entry")
            continue
        ctrls.append(WgiController(item))
    for c in ctrls:
        diag.append(f"[WGI] {c.describe()} -> "
                    f"{'%d%%' % c.level if c.level is not None else 'no level'}"
                    f"{' charging' if c.charging else ''}")
    if not ctrls:
        diag.append("[WGI] no controllers listed")
    return ctrls


def query(diag: List[str]) -> Optional[List[WgiController]]:
    """Controllers with their battery reports, or None if the API is unavailable."""
    if sys.platform != "win32":
        return None
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", PS_SCRIPT],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
            creationflags=0x08000000)                      # CREATE_NO_WINDOW
    except subprocess.TimeoutExpired:
        diag.append("[WGI] PowerShell did not answer within 20 s")
        return None
    except Exception as e:
        diag.append(f"[WGI] {e}")
        return None
    return parse(proc.stdout.decode("utf-8", errors="replace").strip().lstrip("﻿"), diag)
