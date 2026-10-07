# Versions

The device chain is only as reliable as its weakest version match. The AltTester Unity SDK, AltTester Desktop and the AltTester Python driver are pinned **together**: never upgrade one without the others (see `CLAUDE.md`).

Raw evidence for the values below: [`docs/evidence/phase0/environment.txt`](evidence/phase0/environment.txt) (script output, captured 2026-10-07).

## Toolchain

| Component | Version | Evidence |
|---|---|---|
| Unity Editor | 2021.3.45f2 (`88f88f591b2e`) | `ProjectVersion.txt` of the game project |
| Unity Android modules | Android Build Support; OpenJDK 11.0.14.1; SDK build-tools 34.0.0, platforms android-33/34/35, platform-tools 32.0.0; NDK r21d (21.3.6528147) | files under the editor's `PlaybackEngines/AndroidPlayer` |
| Endless Runner sample (Asset Store) | `Endless Runner Mobile Sample Project.unitypackage`, 66,105,424 bytes, sha256 `669522542709b43deaefecd78a502d7a69fe89db7fddd920a9b269a96c58164c` | Asset Store download cache, imported 2026-10-07 |
| AltTester Unity SDK | 2.3.2, GPL-3.0 `.unitypackage` (to be imported in M0.2) | alttester.com downloads page, 2026-10-07 |
| AltTester Desktop | 2.3.3; its built-in server reports **2.3.2.0** | Desktop log line `AltTester(R) Server version: 2.3.2.0` |
| AltTester Python driver | 2.3.2 (`AltTester-Driver` on PyPI) | `uv.lock`; `importlib.metadata` |
| Python | 3.12.5 | `uv run python --version` |
| uv | 0.11.28 | `uv --version` |
| adb on the device host | platform-tools 37.0.1 (adb 1.0.41) | `adb version` |
| AltTester licence | Pro 30-day trial, activated 2026-10-07 (local time); Lite requested 2026-10-07, answer pending | Desktop log `Successful license activation!`, `License type: Pro` |

## Python driver

- Distribution `AltTester-Driver` 2.3.2; **import name `alttester`** (`alttester/__version__.py`: `VERSION = "2.3.2"`).
- License: "AltTester® SDK License Agreement, SDK Bindings (Non-GPL Version)". Only holders of a valid AltTester subscription may use it, and it may not be redistributed (including through repositories). It is installed from PyPI by `uv sync` and never vendored into this repo.
- Quirk: `AltDriver._check_server_version` treats only server versions 2.2.x and 1.0.x as supported, so it logs "Version mismatch" against a 2.3 server even when everything matches. Driver logging is off by default. `pg doctor` compares versions itself instead of relying on this warning.
- Bug: log notifications are delivered **twice** when a listener is added with `overwrite=True` (the default of `AltDriver.add_notification_listener`). `NotificationHandler.add_notification_listener` replaces the callback list with `[callback]` and then appends the same callback again. This differs from the docstring ("overwrite the other callbacks or just append"). Reproduced without a device: [`docs/evidence/phase0/driver-notification-duplicate.txt`](evidence/phase0/driver-notification-duplicate.txt). Proposed workaround (awaiting Dheeru's OK): register with `overwrite=False` on a fresh driver connection, which delivers each log once.
- Importing `alttester` prints `SyntaxWarning`s from its dependency `pure-python-adb` 0.3.0.dev0 (invalid escape sequences under Python 3.12) the first time it is compiled. Harmless; we call adb ourselves.

### Confirmed driver API

Filled in during M0.4 with every driver method we call, each confirmed against the installed package.

## Device

| Field | Value |
|---|---|
| Model | Samsung Galaxy S26 Ultra (SM-S948B) |
| Android | 16 (API 36), One UI 8.5, security patch 2026-08-05 |
| ABI | `arm64-v8a` only, so the APK must be IL2CPP + ARM64 |
| Page size | 4096 bytes |
| Screen | 1440x3120, density 600 |

The adb serial is kept in the local `.env` (`PG_ADB_SERIAL`), not in the repo.

## Game build

Filled in by Dheeru after the M0.2 build.

| Field | Value |
|---|---|
| Android package name | `com.unity.trashdash` (from Player settings; confirm after the build) |
| Launch activity | not recorded yet |
| Scripting backend / architectures / managed stripping | not recorded yet |
| APK sha256 | not recorded yet |

## Other tools on the device host (not used by this project)

- AltTester Desktop installed its own AltTester CLI v0.1.2 into the user profile and added it to `PATH` (Desktop log, 2026-10-07).
- Unity Hub also installed Unity 6000.6.4f1 (no Android module). The game project does not use it.
