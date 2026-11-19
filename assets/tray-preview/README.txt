WINDOWS 11 SYSTEM TRAY — WEB COMPONENT

Open index.html in Edge or Chrome on Windows 11. No build step is required.
The demo is English, uses Acrylic, a 24-hour clock and DD.MM.YYYY dates.

INTEGRATION
Copy win11-tray.js, win11-tray.css AND the halobattery/ asset directory together.

  <win11-tray theme="dark" language="en-US" dpi="96"></win11-tray>
  <script src="/assets/win11-tray.js"></script>

Use a classic script, not type="module". Assets resolve relative to the JS file.
Shadow DOM isolates component styling. The default position is fixed bottom-right.
For an existing taskbar: win11-tray { position: relative; }
Use background="none" when your containing taskbar paints the shared Acrylic
background. This avoids a second, more opaque layer under the system tray.

OPTIONS
theme="dark" | "light"
dpi="96" | "120" | "144" | "168" | "192" | "240" | "288"
96 DPI = 100%. The demo scales icons, typography, spacing and flyouts together.
Changing the preview DPI does not change Windows display settings.
language="en-US" | "ru-RU" (English labels; indicator ENG or RUS)
notifications="false": hide the bell
time-zone="Asia/Novosibirsk": optional explicit timezone
datetime="2026-11-19T14:05:00+07:00": optional fixed time for comparisons
seconds: optional integration attribute; no Seconds button is shown in the demo

SAVED STARTUP SETTINGS
defaults.json contains the captured profile. The active startup copy is embedded
in index.html as tray-defaults, so opening the site directly from disk needs no
JSON fetch. The current profile is Dark, 192 DPI (200%), with Keyboard selected.
Mouse is fully charged, Keyboard is charging at 65%, Headset is in warning,
and Gamepad is low. The other devices keep their captured settings too.
Reloading restores this saved profile. Later UI edits are temporary previews.
live-tray-settings mirrors the runtime profile in the DOM for inspection/export.

DEVICE STATES
Each device keeps an independent state and charge level. Choose a device in the
editor, or click its tray icon. Pin to taskbar controls its placement. If space
is limited at high DPI, pinned icons automatically move into the overflow.

Modes: normal, charging, low, warning, full, sleeping, paused, unknown_charging.
Normal/charging use the 0–100 level. Low = 10%, warning = 20%, full = 100%.
Sleeping uses the dim inactive glyph. Paused uses the native dashed ring.
Known-level charging breathes over three seconds. Unknown-level charging rotates
a quarter arc over three seconds. Reduced-motion preference disables playback.

  const tray = document.querySelector('win11-tray');
  tray.setDeviceState('mouse', {mode:'charging', level:47});
  tray.setDeviceState('keyboard', {mode:'low'});
  tray.setDeviceState('headset', {mode:'unknown_charging'});
  tray.setDeviceState('gamepad', {mode:'sleeping'});
  tray.setDevicePinned('speaker', true);
  tray.getDeviceState('mouse');
  tray.getDeviceKinds();
  tray.getDeviceModes();

Kinds: mouse, keyboard, headset, gamepad, bluetooth, microphone, speaker, webcam,
health, smart_home, watch, display, tracker, xbox, playstation, nintendo.

Custom placement:
  tray.setPinnedIcons([{id:'my-mouse', label:'Mouse', icon:'halo:mouse'}]);
  tray.setTrayIcons([{id:'my-watch', label:'Watch', icon:'halo:watch'}]);

System icon preview:
  tray.setStatus({wifi:true, volume:42, airplane:false});

Events: win11:trayclick, win11:devicechange, win11:devicepinchange,
win11:languagechange, win11:showdesktop, win11:panelchange, win11:controlclick.
event.detail contains the selected value. Network/sound and the clock emit
controlclick events; calendar and quick-settings panels are intentionally absent.
The remaining flyouts are tray overflow and the language selector.

HALOBATTERY ARTWORK AND RINGS
Device resources are from the published HeyOkay/HaloBattery pre-release branch.
Artwork blob: 7dafd7e97bf8817ef6a6e92ae89736a9d7eb5147
Renderer blob: 724f51a91542d5f0a51d2af0d5145228739298ac
https://github.com/HeyOkay/HaloBattery/blob/pre-release/halo/icon_artwork.py
https://github.com/HeyOkay/HaloBattery/blob/pre-release/halo/icons.py

Original glyph masks are extracted byte-for-byte. Ring atlases are generated
through the published Python renderer, using its thin geometry and 15% low
threshold. Browser playback blends the two native charging-opacity endpoint
tiles and uses the native 90-frame unknown-charge cycle. No app logo is included.
Neutral ink is adjusted to match this system-tray replica: white at full opacity
in dark theme, black at 228/255 opacity in light theme. Semantic red/amber/green
charge-arc colors are preserved. Inactive devices remain intentionally dim.

Common physical DPI sizes are rendered directly. Canvas backing dimensions
match the displayed physical pixel size, and icon origins snap to the physical
pixel grid. This avoids scaling a small finished icon into a larger bitmap.
Unusual custom DPI/display combinations may use the nearest available atlas.
These are local preview values, not a live connection to Windows or HaloBattery.

halobattery/manifest.json: original mask hashes and source provenance
halobattery/states-manifest.json: native rendering parameters and atlas layout
halobattery/NOTICE.txt: source and licensing notices

WINDOWS STYLE PROVENANCE
SystemTrayResources was decoded from this computer's Windows build 26300.9974.
reference/ contains both reconstructed XAML variants. resources.json records
hashes, resource offsets, colors and geometry. Reader: chausner/XbfTools,
Apache-2.0 (research parser; reconstructed XAML may differ from source).

Ported values include state colors, the 83ms brush transition, icon padding,
normal/language widths, grouped spacing, clock margins and overflow cell sizes.
The requested chevron-toggle animation is a 200ms web rotation. Flyout movement
and Acrylic blur/tint/noise approximate native compositor effects in CSS.
No pixel-by-pixel equivalence with the complete native Windows taskbar is claimed.

FONTS
Local Segoe UI Variable Small / Text and Segoe Fluent Icons, with Segoe UI /
Segoe MDL2 Assets fallbacks. Windows font binaries are not redistributed.
Appearance can differ on platforms without those fonts.

LOCAL PREVIEW
http://127.0.0.1:8767/ (also available on port 8768)
Servers are intentionally left running in the background and bound to loopback.
After a Windows restart, open index.html directly or restart a local HTTP server.

verification.json records the checks performed for this version.
