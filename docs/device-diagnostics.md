# Explicit local device diagnostics

These commands are optional experimental tools, not automatic discovery or
normal polling. Close HaloBattery first. A Windows named-object lease shared
with normal startup and generic `--probe` excludes concurrent app/diagnostic
access, including older HaloBattery versions. It cannot exclude vendor tools;
close those too. Permission or lease errors fail closed.

From a packaged build, choose a new writable report path:

```powershell
.\HaloBattery.exe --diagnose-device logitech-pro-x3 --acknowledge-experimental --diagnostic-output .\x3-test.json
.\HaloBattery.exe --diagnose-device endgame-4k --transport receiver --acknowledge-experimental --diagnostic-output .\endgame-test.json
```

Other exact choices: `aj159`, `inzone-h9ii`, `lamzu-tachi`.
Endgame requires an explicit `receiver` or `wired` transport. Do not run a test
against an unintended receiver; two matching receivers are refused rather than
guessed. Output files are created exclusively and never overwritten. Source
runs may print JSON to the console; compatibility wrappers are
`tools/probe_endgame_4k.py` and `tools/probe_lamzu_tachi.py`.

No acknowledgement means no provider import, enumeration or device I/O.
Reports omit raw HID paths, serials, packet dumps and device exception text.
A disconnected X3 produces a valid no-reading report. Native operations use the
existing in-process cancellable transport. A failed/cancelled scope discards
readings; the lease remains held until native cleanup is confirmed, or until
that diagnostic process exits. No additional process tree is introduced.

## Limitations

PRO X 3, Endgame 4K and Tachi remain disabled during normal use. AJ159 and H9 II
retain their existing normal implementations. Test results do not create tray
icons or history and never enable device families. Normal Maya X, XM2we and
mixed-family headset switches remain available; held-only switches are hidden
without deleting preferences.

PRO X 3 has independent upstream Linux capture evidence, but this diagnostic
reader has no fresh Windows/charging hardware confirmation. H9 II has earlier
owner battery-level confirmation; the current strict reader and charging
transitions are not newly hardware-tested here. AJ159 charging remains unknown.

Endgame's successful status does not prove reply freshness. Exactly one B4 is
sent; repeated values are not treated as corroboration. Cabled percentages are
not published because source observations show that charging changes their
meaning; a cable only suggests charging, not a verified charging flag. A failed
command can leave the receiver link wedged according to the primary source.
This tool sends no automatic recovery or target-selection query; replugging or
recovery through the vendor tool may be needed. Persistent status08 and recovery
remain unresolved, so do not treat a diagnostic success as production support.

Tachi's Aurora payload is sourced, but real receiver/radio behavior and charging
have not been verified here. No sibling fallback or guessed command is tried.
All readers refuse malformed/out-of-range data and mark freshness unverified.
See [protocol sources](protocols.md#explicit-diagnostic-readers).
