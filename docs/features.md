# Features

[← Back to the README](../README.md)


## The icon

- **Centre**: Lucide device pictograms inside a thin circular ring (1 logical pixel at 16×16). The pictogram can be turned off or replaced by the percentage. A device that only reports coarse steps shows its number like any other device; the tooltip and status file keep the “about NN%” wording. Controller logos are chosen manually and the default is a generic controller.
- **Custom pictograms**: the Devices page can search a reviewed [Iconify catalogue](iconify.md), preview a symbol inside the real ring and save it for one device. Monochrome symbols follow the theme; colored artwork keeps its original colors, including fixed parts of mixed icons. Selections work offline. Search uses pages of 50 candidates with round arrow and number buttons; neighboring previews are prepared ahead of time. Temporary gallery caches are cleared when Settings closes. Complete attribution and licenses are available in Settings; the existing percentage and ring-only modes retain priority.
- **Ring thickness**: choose the current thin ring or the classic thicker ring on the Tray icons page. The same style applies to previews, charging animations and the no-device indicator.
- **Display scaling**: tray artwork is rendered at the taskbar's physical icon size and refreshed when its DPI changes, keeping the thin ring and pictograms sharp.
- **Colour**: follows the taskbar (white on a dark bar, black on a light one). With [MyDockFinder](https://store.steampowered.com/app/1787090/MyDockFinder/) running it follows its top menu bar instead. With a transparent taskbar (e.g. TranslucentTB) pick **Icon colour → White** or **Black**.
- **Amber**: close to the alert threshold. **Red**: at or below it.
- **Green**: charging. A known battery level breathes in brightness; an unknown level uses a rotating green arc over a steady neutral ring. Turning animation off in Settings leaves the arc still.
- **Translucent**: the mouse is asleep and keeps its last level for 5 minutes. A device that is switched off leaves the tray and comes back when it is switched on.

The low battery notification fires once, and again only after the device has been charged.

## Reading failures

Windows HID reads use bounded in-process transport workers. A failed or timed-out
query keeps any previous result marked unavailable rather than replaying it as a
new measurement. Refresh retries a paused provider. No process is started for
each device or protocol. Drivers that refuse cancellation can still occupy the
fixed worker budget; Diagnostics distinguishes unavailable readings from an
ordinary sleeping device.

The native HID adapter keeps the last transport diagnostic as text for
`device.error()`. Reading it performs no native call, including before opening
or after closing a device; a successful transport operation clears it to
`Success`. The bounded adapter deliberately lets a `send_feature_report()`
deadline raise `TimeoutError` (an `OSError`) instead of returning HIDAPI's `-1`.
Callers keep that failure distinct from a valid reply while cancellation and
native handle cleanup continue under the existing owner.

If the native HID module is missing or blocked, HaloBattery explains the failure
and keeps Settings and Windows Bluetooth support available. USB/2.4 GHz support
is not emulated; Diagnostics includes the native loader error.

Full-charge notifications cannot cross a disconnect or an unavailable transport
reading. Reconnecting at 100% is not a newly completed charge. Temporary unknown
values from a still-connected device wait for confirmation without firing an alert.

## Tray menu

Right-click a device icon for **Settings**, **Refresh now**, **Diagnostics**
and **Exit**, plus an update action when a release is available.
Double-click an icon to open settings for that exact device, even when several
devices have the same name. Device customization and app preferences are in the
settings window.

The menu follows the Windows light or dark app theme and display scale. If it
does not work on your PC, set `"fluent_menu": false` in
`%APPDATA%\HaloBattery\config.json` to use the classic Windows menu.

## Windows tray visibility

All device icons share one Windows visibility preference per executable path.
Use the HaloBattery switch under **Taskbar → Other system tray icons** to choose
the taskbar or hidden-icons flyout for the whole application. Per-device hiding
remains available on the Devices page in HaloBattery.

The packaged app migrates its old per-device Windows records after creating the
shared entry. It backs up removed values to `tray-history-backup-*.json` in the
app's data folder and preserves the existing shared preference. Cleanup matches
only the current executable's exact path, so installed and portable copies remain
separate. Source launches never clean shared Python executable records. Explorer
can retain old rows in memory until the next Windows sign-in; the app does not
restart Explorer or clear other applications' tray history.

## Settings

Settings run in a native C# WinUI 3 companion process; Python remains responsible
for device polling, configuration, notifications and the tray. The shell uses
the actual Mica backdrop, ThemeShadow, custom cards and page transitions
extracted from the Chrome Mica Patcher reference implementation. Mica is enabled
on supported Windows 11 systems; Windows 10 uses an opaque theme background.

Release builds publish the companion as an unpackaged x64 NativeAOT folder with
the Windows App SDK runtime bundled. The .NET code is compiled into the native
executable; a managed .NET runtime installation is not required. Keep the entire
HaloBattery folder together. The build references only WinUI, input/composition
and runtime components; unused AI, machine-learning, search and widget components
are not included. Generated JSON metadata and explicit roots for dynamically
created XAML retain the protocol and UI activation paths while trimming unused
framework code. The separate native publish tree is added after Python dependency
analysis to avoid collecting its DLLs twice.
Wallpaper image formats, fonts and native language resources are retained.
Each native publish uses a clean staging directory; a failed build leaves the
previous output in place. Release PDBs remain in intermediate build output and
are archived separately by CI for 90 days instead of being included in the app.
Clean-machine deployment still needs verification before a stable release.

For source builds, install .NET SDK 10.0.404, generate the application icon with
`python tools/make_icon.py halo.ico`, then run `./scripts/build_settings.ps1`
in PowerShell. Its default output is `.build/settings-ui`; the Python host can
find that folder during development. `-OutputDirectory`,
`-IntermediateDirectory`, `-NuGetConfig` and `-PackagesDirectory` allow an
isolated build cache and an explicitly configured offline package feed. The
default Auto mode uses NativeAOT for self-contained Release. Debug and explicit
verification builds remain JIT; `-PublishMode Jit` is an explicit fallback.
`-SelfContained $false` supports development with matching shared runtimes.
The pinned native build toolchain is cached in a dedicated build directory via
`-ToolchainCache`; `-OfflineToolchain` requires its already verified archives.
`scripts/build_exe.bat` and both release workflows include this publish step
before freezing Python. No SDK or shared runtime installer is run by the script.

Open **Settings** from the tray menu. Changes apply immediately and are saved;
there is no separate Save button. Position, size, monitor and the navigation
panel state are restored before the window is shown. Closing Settings releases its companion process and memory. Reopening restores
the saved page and scroll position while the app splash covers loading.
Window preferences are stored natively in `settings-window.json` inside the
active installed or portable profile. Settings remain available when no devices are
connected. The interface keeps seven languages: English, Spanish,
French, Japanese, Russian, Simplified Chinese and Traditional Chinese.

Scrolling beyond the top or bottom of a settings page fills a small neutral
ring. Continuing past its resistance threshold opens the adjacent page; a pause
or reverse scroll cancels the gesture. Content stretches slightly and springs
back, while the ring fades, slides and scales through native Composition animations.

- **General**: language, **Start with Windows**, the normal polling interval
  (15 seconds to 5 minutes), update preferences and manual update checks.
  Existing custom intervals are preserved. **Exit HaloBattery** stays fixed at
  the bottom of this page while its settings scroll.
- **Devices**: select a device, rename it or reset its name, show or hide its
  tray icon, choose its pictogram and controller logo, and set its own low-battery
  threshold (off, 5–30%, or the app default). Hidden devices can be shown again
  here. Windows Bluetooth discovery and keeping disconnected devices until restart
  are also configured here. Controller logos include generic, Xbox, PlayStation and Nintendo Switch,
  with optional brand colors. These choices do not change the device type in
  the status file. The device summary always uses a basic outline glyph for the
  detected device type, without a battery ring or tray artwork customizations.
- **Tray icons**: choose **Outline** or **Filled** pictograms, automatic/white/black
  icon color, pictogram/percentage/ring-only content, a pictogram fallback when
  percentage is unavailable, charging animation and estimated time left.
  The remaining-time estimate appears in the tooltip after at least 30 minutes
  of use and a 3% drop. Only time awake and on battery counts; history is saved
  in `%APPDATA%\HaloBattery\history.json`. The preview uses the actual
  tray icon renderer and current device state at 80 × 80 pixels (500% of a
  16 × 16 icon), including per-device artwork choices.
- **Notifications**: the default low-battery threshold, fully charged alerts,
  low-battery sound and **Quiet while gaming**. Visual low-battery alerts fire
  once per discharge cycle. Sound is off by default; when enabled, it repeats
  every 5 minutes while low and off the charger, even in full-screen apps.
  Quiet mode is on by default: it holds visual alerts during full-screen apps
  and polls no more often than every 5 minutes. Plugging in a device still
  updates at once; a queued low alert is dropped if charging has begun.
- **Advanced**: supported device families,
  **PlayStation full mode (Bluetooth)** and the status file integration. Disabled
  families are not opened or queried. PlayStation full mode is off by default;
  some games may stop seeing the controller until it is turned off and on.
  The optional `%APPDATA%\HaloBattery\status.json` is written after every poll
  for Rainmeter, Stream Deck and scripts. It includes device `name`, `level`,
  `charging`, `online`, `kind`, `seconds_left` and tooltip `text`, plus `running`
  and `updated_unix`. Turning the option off deletes the file.
- **About**: version, diagnostics, the data folder, project page and issue reporting.

## Updates

The app checks for a new release once a day (or at once with **Settings → General → Check for updates now**) and adds **Update to vX.Y.Z** to the menu when there is one. One click downloads it, checks it, closes the app, replaces the program files and starts the new version, which then shows what is new in a small window. Settings, the battery history and `portable.txt` are kept. From version 1.14.0 or older, or when the app sits in a folder it cannot write to (e.g. Program Files), update by hand: tray menu → **Exit**, then replace the folder; **Start with Windows** follows the new copy. If you used the old single-file `HaloBattery.exe`, delete it.

## SmartScreen and antivirus warnings

Windows SmartScreen may warn about an unrecognized app on first launch, because the file is not code-signed: **More info → Run anyway**. Some antivirus tools flag unsigned Python apps by mistake (usually a generic `!ml` detection). The release is built by GitHub Actions straight from this repository, with public build logs; if you would rather not trust it, run it from source (see below).

## Portable mode

To keep the settings, the log and the battery history next to the app instead of in `%APPDATA%\HaloBattery` (for a USB stick or a folder you sync), create an empty file named `portable.txt` in the app's folder, next to `HaloBattery.exe` (or `halo_battery.pyw` when running from source), and restart the app. The folder must be writable: if it is not (e.g. inside `C:\Program Files`), the app keeps using `%APPDATA%\HaloBattery`. Settings are not moved over by themselves: copy `config.json` from `%APPDATA%\HaloBattery` to keep them. **Start with Windows** still adds an entry to the registry of the current user.

## Run from source

1. Install [Python 3.10+](https://www.python.org/downloads/) with **Add python.exe to PATH** checked.
2. Download or clone this repository somewhere permanent, e.g. `C:\Tools\HaloBattery`.
3. Install .NET SDK 10.0.404 for the native settings window. From the repository
   root, run `python -m pip install -r requirements.txt`,
   `python tools/make_icon.py halo.ico`, then `./scripts/build_settings.ps1`
   in PowerShell. Keep its `.build/settings-ui` output in place.
4. Run `scripts\install_and_run.bat`, then right-click the tray icon → **Settings… → General → Start with Windows**.

`scripts\probe.bat` polls once and prints what was found in a console window. `scripts\build_exe.bat` builds the app yourself into `dist\HaloBattery`. Releases are built automatically: pushing a tag like `v1.8.0` makes GitHub Actions build the app and attach the zip (`.github/workflows/release.yml`).

## Phone Link mobile battery

Phone Link appears under Device types. Its selected mobile device appears in
the tray and Devices with a phone pictogram when its identity can be proved.
This source is independent of Windows Bluetooth battery reporting: no Bluetooth
connection is required by HaloBattery, and Phone Link may obtain status over Wi-Fi.

It reads the Windows 11 Phone Link Start-menu companion cache, not the phone.
Only the selected companion device's battery is available; this does not provide
real-time telemetry or every linked phone's level. An unchanged, previously
accepted level remains visible as **last reported**, rather than disappearing
after five minutes. The ring and percentage stay visible; the source is marked
offline/unconfirmed and old charging animation, alerts and use-time estimates
are disabled. On a cold start, a cache older than three hours cannot seed a
reading. This is an admission policy, not a measurement or heartbeat guarantee.
Missing/unsupported cache or ambiguous identity clears admission. HaloBattery
will not launch Phone Link or change device settings.

The initial card parser recognizes the observed English status format even
when HaloBattery uses another UI language. Other card formats are refused.
Unknown charger state is shown as unknown and cannot trigger battery alerts or
use-time estimates. See [source and verification limits](protocols.md#phone-link-selected-cache).

Ongoing updates depend on Phone Link's background synchronization. HaloBattery
does not keep it alive or refresh it by opening its window. If Phone Link omits
charging status, HaloBattery cannot reconstruct it from the battery percentage.
