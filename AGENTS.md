# Instructions for AI agents and contributors

Halo Battery: a Windows tray app (Python) that shows the battery level of wireless mice, keyboards, headsets and controllers. This is the community fork `ahmedkhursheed23/HaloBattery`. Human-facing rules are in [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md); this file is the short version for agents.

## Repo map

- `halo_battery.pyw`: the app. Holds `VERSION`, `PROVIDER_LABELS`, `make_providers()` and `--probe`.
- `providers/`: one module per protocol family (`base.py` is the interface). A new device goes into the existing provider of its protocol; a new provider only for a new protocol. A new provider must be:
  - imported in `providers/__init__.py`;
  - added to `make_providers()` and `PROVIDER_LABELS` in `halo_battery.pyw`;
  - given a `provider.*` key in all six catalogs in `halo/locales/` (`en es fr ja zh_CN zh_TW`). The catalogs must keep identical keys.
- `halo/`: app modules (i18n, icons, flyout, history, updates, whatsnew, winevents) and `locales/`.
- `tests/`: `unittest`, with fake HID devices. No hardware, no network.
- `docs/`: `devices.md` (support table), `protocols.md` (how each device is read, with source), `features.md`, `credits.md`.
- `tools/`, `scripts/`: build and helper scripts. `CHANGELOG.md`: user-visible changes under `[Unreleased]`.

Run the tests from the repo root: `python -m unittest discover -s tests`. Smoke test: `python halo_battery.pyw --probe`.

## Workflow

- Branch from `pre-release` (`feat/`, `fix/`, `device/`, `docs/`, `test/` + short name). Never commit to `main` or `pre-release` directly. Pull requests target `pre-release`.
- Never bump `VERSION` outside a "Release X.Y.Z" pull request (see CONTRIBUTING, "Branches and releases").
- Never send guessed commands to a device. Every request written to a device must come from a named source (documentation, an open-source project with file and line, or a capture). Name it in a comment at the top of the provider and in `docs/protocols.md`. A wrong command can change device settings.
- No network and no real hardware in tests.

## Before you open a pull request

1. Run the full suite; it must pass.
2. Read your own full diff (`git diff`) as a skeptical reviewer: correctness, unrelated changes, leftover debug output, Windows path and encoding problems (use UTF-8 explicitly, no hard-coded separators).
3. Write tests for the edge cases and make them pass:
   - truncated, short and garbage HID replies;
   - level `None`, `0`, `100` and out of range (refuse it, do not show it);
   - the device disappears in the middle of a poll;
   - unknown charging state;
   - all six locales (when you add or change strings);
   - a config file with values of the wrong type.
4. Update `CHANGELOG.md` (`[Unreleased]`), and for devices `docs/devices.md` and `docs/protocols.md`.

## Pull request rules

- Small, one topic, minimal diff. Concise imperative title ("Add Razer Viper V3 Pro battery").
- Description: **What / Why / How tested / Risks** (the template has these). Link issues (`Closes #123`). Say whether you tested on real hardware, and which device; do not claim hardware testing you did not do.
- Commit messages: a short imperative subject, a body that says why when it is not obvious. One logical change per commit.

## Do not

- Delete, skip or weaken tests to get green. Fix the code, or the test for a real reason.
- Reformat or rename unrelated code.
- Commit build output (`dist/`, `build/`, `*.spec`), `__pycache__`, logs or `diagnostics.txt`.
- Add dependencies without a clear need (the app is built into one folder with PyInstaller).
- Push or force-push to `main` or `pre-release`.
