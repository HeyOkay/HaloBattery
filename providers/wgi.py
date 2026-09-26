"""Controller battery through Windows.Gaming.Input (the API the Xbox Accessories
app uses). Many third-party Xbox-protocol controllers, e.g. the GameSir G7 Pro
on its 2.4 GHz receiver, never report their battery through XInput
(BatteryType stays "disconnected"), but Windows.Gaming.Input returns a real
BatteryReport: remaining / full charge capacity and the charging status.

The WinRT API is reached through PowerShell (like the Bluetooth provider), so
no extra Python packages are needed. In a fresh process the controller list
fills asynchronously, so the script waits briefly for it.
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
  # the controller list fills asynchronously in a new process
  for ($i = 0; $i -lt 30; $i++) {
    if ([Windows.Gaming.Input.RawGameController]::RawGameControllers.Count -gt 0) { break }
    Start-Sleep -Milliseconds 100
  }
  Start-Sleep -Milliseconds 300
  $list = @()
  foreach ($c in [Windows.Gaming.Input.RawGameController]::RawGameControllers) {
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


class WgiController:
    def __init__(self, raw: dict):
        self.raw = raw
        self.vid = raw.get("vid") or 0
        self.pid = raw.get("pid") or 0
        self.status = (raw.get("status") or "").lower()      # notpresent / discharging / idle / charging
        self.charging = self.status == "charging"
        remain, full = raw.get("remain"), raw.get("full")
        self.level: Optional[int] = None
        if isinstance(remain, (int, float)) and isinstance(full, (int, float)) and full > 0:
            self.level = max(0, min(100, round(remain * 100 / full)))
        name = (raw.get("name") or "").strip()
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
    if data.get("error"):
        diag.append(f"[WGI] unavailable: {str(data['error'])[:200]}")
        return None
    items = data.get("controllers") or []
    if isinstance(items, dict):
        items = [items]
    ctrls = [WgiController(it) for it in items]
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
