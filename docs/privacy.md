# Privacy

HaloBattery processes battery and device information on your PC. It does not
automatically upload your device inventory, battery history, settings, logs,
diagnostics or screen samples. It has no built-in analytics or crash-report upload.

## Network connections

- **Updates:** automatic update checks are enabled by default and can be disabled
  in General. Checks request release metadata from `api.github.com` and identify
  the app as `HaloBattery/<version>`. Installing an update downloads release assets from GitHub's release-asset
  infrastructure over HTTPS.
- **What's new:** opening release notes can request the current version's release
  from GitHub. This may also happen after an upgrade when automatic What's new
  is enabled and notes are not cached. Disabling update checks does not disable
  these separate release-note requests.
- **Iconify:** browsing the catalog downloads collection metadata and icon artwork
  from `api.iconify.design`, including neighboring page previews. Requests identify
  the app as `HaloBattery/IconPicker` and contain collection/icon names. Search
  text is matched against the local index. Startup and saved-icon rendering do
  not contact Iconify. Applied artwork and attribution remain available offline.
- **Links:** following a project, release, author or license link opens your browser,
  which has its own privacy settings and network behavior.

These services receive ordinary connection metadata, including your IP address
and the requested URL and headers. Their server logging is controlled by the
service operator. See [GitHub's privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)
and the [Iconify API documentation](https://iconify.design/docs/api/).

## Local data and Windows integration

Configuration, device customizations, battery history, logs, optional `status.json`,
requested diagnostics and selected icon artwork/licenses are kept in
`%APPDATA%\HaloBattery`, or the writable application folder in portable mode.
The settings companion also stores window position and view state locally.
Temporary icon-gallery caches are cleared when Settings closes.

Device identifiers can contain Bluetooth addresses, serial numbers, HID paths or
hashes of those values. Diagnostics can include device inventory, names, paths,
recent history/logs and theme/process context. Review a diagnostic report before
posting it publicly; generating it does not upload it.

Windows registration, optional autostart and tray-visibility migration use the
current-user registry. Old tray records are backed up locally before cleanup.

Adaptive icon contrast can inspect Windows theme/accent information, window/process
context and the desktop wallpaper. The MyDockFinder contrast path can fall back
to downscaled screen samples when the wallpaper cannot be read. These samples
are processed locally in memory and are not uploaded.

Exit HaloBattery before manually removing its local data. Removing settings or
artwork resets the corresponding preferences and custom icons. Windows registry
entries are separate from these files.
