# Features

[← Back to the README](../README.md)


## The icon

- **Centre**: a headset, a mouse, a keyboard (a keycap with a K), an Xbox or PlayStation controller, or the Bluetooth rune - the pictogram can be turned off in the menu.
- **Colour**: follows the taskbar (white on a dark bar, black on a light one). With [MyDockFinder](https://store.steampowered.com/app/1787090/MyDockFinder/) running it follows its top menu bar instead. With a transparent taskbar (e.g. TranslucentTB) pick **Icon colour → White** or **Black**.
- **Amber**: close to the alert threshold. **Red**: at or below it.
- **Green and breathing**: charging - the animation can be turned off in the menu, leaving a plain green arc.
- **Translucent**: the mouse is asleep and keeps its last level for 5 minutes. A device that is switched off leaves the tray and comes back when it is switched on.

The low battery notification fires once, and again only after the device has been charged.

## Tray menu

Right-click a device icon to open its menu. It looks like a Windows 11 menu (acrylic background, rounded corners, the light or dark app theme) and is sharp at any display scale. If it does not work on your PC, set `"fluent_menu": false` in `%APPDATA%\HaloBattery\config.json` to get the classic Windows menu back.

- **Rename…**: give the device your own name (for example, two controllers with the same name). **Reset name** goes back to the device's own name.
- **Icon**: pick the pictogram of this device (Automatic, Mouse, Keyboard, Headset, Controller or Bluetooth), for example a controller over Bluetooth that shows the Bluetooth pictogram.
- **Low battery alert at**: an alert level for this device only (off, 5–30%), or **Default** to follow Preferences. The red ring follows it too.
- **Hide this device**: remove its icon, for example for a controller that always reports 100%.
- **Refresh now**
- **Preferences**:
  - **Poll interval** (15 s to 5 min) and **Low battery alert** (off, 5–30%): change them with the − and + buttons or the mouse wheel, the menu stays open
  - **Alert when fully charged** (a notification once per charge, on by default)
  - **Estimated time left**: "about 5 h of use left" in the tooltip, from how fast the device has drained since its last charge. Only time the device is awake and on battery counts, and there is no estimate until it has been used for 30 minutes and dropped 3%. The history is kept in `%APPDATA%\HaloBattery\history.json`.
  - **Keep disconnected devices until restart** (off by default): a device that goes away stays in the tray, greyed, with its last level, until it comes back or the app restarts
  - **Quiet while gaming** (on by default): while a game or other app is full screen (the same signal Windows uses to hold its own notifications), alerts are held and shown once it closes - one per device, and a low battery alert is dropped if the device was put on the charger meanwhile. Devices are polled only every 5 minutes then, since every poll talks to them; plugging something in still updates at once.
  - **Sound with the low battery alert** (off by default): also play the Windows "Battery Low" sound ("Battery Critical" at 5% or less), for full-screen games where the notification is not seen. It plays again every 5 minutes while the device stays low and off the charger, and **Quiet while gaming** does not hold it back
  - **Windows Bluetooth devices**, **Device pictogram**, **Percentage in the icon** (the level as a number in the ring instead of the pictogram, amber or red when low), **Charging animation**
  - **PlayStation full mode (Bluetooth)** (off by default): always read the battery of a PS4 / PS5 controller over Bluetooth. Some games stop seeing the controller in that mode until it is turned off and on
  - **Status file for other apps** (off by default): writes `%APPDATA%\HaloBattery\status.json` after every poll, for Rainmeter, a Stream Deck plugin or a script. Each device has `name`, `level`, `charging`, `online`, `kind`, `seconds_left` and the tooltip `text`; `running` turns false when the app exits, and `updated_unix` says how fresh it is. Turning it off deletes the file.
  - **Device types**: turn off a brand or device family; its devices are then not opened at all
  - **Icon colour**: Automatic (the Windows theme, or MyDockFinder's menu bar while it is running), White or Black
  - **Start with Windows** (per-user registry key, no admin rights needed)
  - **Check for updates**: once a day, on by default; a notification and an **Update to vX.Y.Z** item appear when a new release is out. **Check for updates now** asks at once and always answers with a notification
- **Hidden devices** (only when a device is hidden): click a device to show it again
- **Diagnostics…**: writes a detailed report and opens it

## Updates

The app checks for a new release once a day (or at once with **Preferences → Check for updates now**) and adds **Update to vX.Y.Z** to the menu when there is one. One click downloads it, checks it, closes the app, replaces the program files and starts the new version, which then shows what is new in a small window. Settings, the battery history and `portable.txt` are kept. From version 1.14.0 or older, or when the app sits in a folder it cannot write to (e.g. Program Files), update by hand: tray menu → **Exit**, then replace the folder; **Start with Windows** follows the new copy. If you used the old single-file `HaloBattery.exe`, delete it.

## SmartScreen and antivirus warnings

Windows SmartScreen may warn about an unrecognized app on first launch, because the file is not code-signed: **More info → Run anyway**. Some antivirus tools flag unsigned Python apps by mistake (usually a generic `!ml` detection). The release is built by GitHub Actions straight from this repository, with public build logs; if you would rather not trust it, run it from source (see below).

## Portable mode

To keep the settings, the log and the battery history next to the app instead of in `%APPDATA%\HaloBattery` (for a USB stick or a folder you sync), create an empty file named `portable.txt` in the app's folder, next to `HaloBattery.exe` (or `halo_battery.pyw` when running from source), and restart the app. The folder must be writable: if it is not (e.g. inside `C:\Program Files`), the app keeps using `%APPDATA%\HaloBattery`. Settings are not moved over by themselves: copy `config.json` from `%APPDATA%\HaloBattery` to keep them. **Start with Windows** still adds an entry to the registry of the current user.

## Run from source

1. Install [Python 3.10+](https://www.python.org/downloads/) with **Add python.exe to PATH** checked.
2. Download or clone this repository somewhere permanent, e.g. `C:\Tools\HaloBattery`.
3. Run `scripts\install_and_run.bat`, then right-click the tray icon → **Start with Windows**.

`scripts\probe.bat` polls once and prints what was found in a console window. `scripts\build_exe.bat` builds the app yourself into `dist\HaloBattery`. Releases are built automatically: pushing a tag like `v1.8.0` makes GitHub Actions build the app and attach the zip (`.github/workflows/release.yml`).
