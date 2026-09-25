# Halo Battery

Shows the battery level of wireless devices in the Windows system tray. Each device gets its own icon: a battery ring with the device pictogram in the middle. No Synapse or other vendor software required.

![All icon states](docs/icons.png)

While charging, the arc slowly "breathes":

![Charging animation](docs/charging.gif)

## Supported devices

| Device | How the battery is read |
|---|---|
| Razer BlackShark V2 Pro 2023 (receiver 1532:0555) | The headset's own "PA" protocol: output reports 0x02 on the vendor interface 0xFF00, remote mode 0xE1, commands 0x21 (battery) and 0x2A (charging) |
| Razer BlackShark V2 Pro 2020 and wireless Razer mice | The 90-byte Razer HID command: class 0x07, command 0x80 (battery), 0x84 (charging). For the 2020 headset the "PA" protocol is tried as a fallback |
| WLmouse Beast X / Beast X Max / Mini Pro | Feature request `02 02 00 83` to the receiver (VID 0x36A7); if there is no reply, the mouse heartbeat is used. Receiver and cable share one icon |
| Bluetooth devices (enable in the menu) | The level Windows itself knows (`DEVPKEY_Bluetooth_Battery`). Only devices connected right now are shown: the link state comes from WinRT (`BluetoothDevice.ConnectionStatus`, queried by MAC address, the same source as Windows Settings); paired-only devices are hidden. If Windows skips the level on a poll, the last known value is shown. Refreshed once a minute |

## Installation

1. Install [Python 3.10+](https://www.python.org/downloads/) with **Add python.exe to PATH** checked.
2. Unpack the folder somewhere permanent, e.g. `C:\Tools\HaloBattery`.
3. Run `install_and_run.bat`.
4. Right-click the tray icon → **Start with Windows**.

Want a single .exe without Python? Run `build_exe.bat`; the result is `dist\HaloBattery.exe`.

## The icon

The icon is a battery ring with the device pictogram in the middle. The arc fills clockwise from the top.

- Centre: a headset, a mouse or the Bluetooth rune. The pictogram can be turned off in the menu.
- Normal arc uses the taskbar colour: white on a dark taskbar, black on a light one.
- Amber arc: the level is close to the alert threshold. Red: at or below it.
- Green arc that slowly "breathes": charging. The animation can be turned off in the menu, leaving a plain green arc.
- Translucent icon without an arc: the device is asleep or off.

Hover over the icon to see the exact percentage. The low battery notification fires once and only again after the device has been charged.

## Tray menu

- **Refresh now**, **Poll interval** (15 s to 5 min), **Low battery alert at** (off, 10–30%)
- **Windows Bluetooth devices**, **Device pictogram**, **Charging animation**
- **Start with Windows** (per-user registry key, no admin rights needed)
- **Diagnostics…**: writes a detailed report and opens it

## Troubleshooting

1. Close Synapse, the WLmouse web driver and other battery tools: they may hold the receiver.
2. Wake the mouse up by moving it.
3. Run `probe.bat` or choose **Diagnostics…** from the tray menu. The report lists every HID device and the raw protocol replies. Attach it to an issue in this repository to get a new device supported. The report contains Bluetooth MAC addresses and device serial numbers; you may want to redact them before posting.

Settings, the log and the diagnostics report live in `%APPDATA%\HaloBattery`.

## Credits

The WLmouse protocol was reverse-engineered by @len0c ([incconutwo/mouse-battery-tray](https://github.com/incconutwo/mouse-battery-tray), MIT). The BlackShark V2 Pro 2023 protocol comes from the OpenRazer driver ([PR #2862](https://github.com/openrazer/openrazer/pull/2862)). Razer PIDs and transaction IDs come from OpenRazer and [RazerBatteryTaskbar](https://github.com/Tekk-Know/RazerBatteryTaskbar).

## License

MIT, see [LICENSE](LICENSE).
