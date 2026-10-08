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
| Postgres (Phase 1, local) | `postgres:16` image, digest `sha256:65b16a8b326e0cfbdf33fa7e783f2a0cb352a61448616ccccfd616ef42aa0f65`; container `pg-proving-ground` on `127.0.0.1:5433`, volume `pg_proving_ground_data` | `docker run` output, 2026-10-07 |
| SQLAlchemy / Alembic / psycopg | 2.1.4 / 1.20.0 / 3.3.6 | `uv.lock` |
| AltTester licence | Pro 30-day trial, activated 2026-10-07 (local time); Lite requested 2026-10-07, answer pending | Desktop log `Successful license activation!`, `License type: Pro` |

## Python driver

- Distribution `AltTester-Driver` 2.3.2; **import name `alttester`** (`alttester/__version__.py`: `VERSION = "2.3.2"`).
- License: "AltTester® SDK License Agreement, SDK Bindings (Non-GPL Version)". Only holders of a valid AltTester subscription may use it, and it may not be redistributed (including through repositories). It is installed from PyPI by `uv sync` and never vendored into this repo.
- Quirk: `AltDriver._check_server_version` treats only server versions 2.2.x and 1.0.x as supported, so it logs "Version mismatch" against a 2.3 server even when everything matches. Driver logging is off by default. `pg doctor` compares versions itself instead of relying on this warning.
- Bug: log notifications are delivered **twice** when a listener is added with `overwrite=True` (the default of `AltDriver.add_notification_listener`). `NotificationHandler.add_notification_listener` replaces the callback list with `[callback]` and then appends the same callback again. This differs from the docstring ("overwrite the other callbacks or just append"). Reproduced without a device: [`docs/evidence/phase0/driver-notification-duplicate.txt`](evidence/phase0/driver-notification-duplicate.txt). Workaround (approved by Dheeru on 2026-10-07): register with `overwrite=False` on a fresh driver connection, which delivers each log once.
- Bug: log notifications never carry their stack trace. The game sends the trace under the key `stackTrace`, but the driver reads `stack_trace`, so `LogNotificationResult.stack_trace` is always `None`. Observed live on 2026-10-07: the logcat copy of the notification in [`docs/evidence/phase0/logs_check.json`](evidence/phase0/logs_check.json) shows a populated `stackTrace`, while the notification the driver delivered had an empty trace. Stack traces are taken from logcat instead.
- Server bug (AltTester Unity SDK 2.3.2, in the game): `set_static_property` on a dotted path whose first segment is a static **property** fails with `UnknownErrorException: Unable to cast object of type 'RuntimePropertyInfo' to type 'FieldInfo'`, although the docs say "field or property". Reads through the same path work. Workaround: start the path at the backing static field (`PlayerData` → `m_Instance.tutorialDone`). Observed 2026-10-07.
- Server bug (AltTester Unity SDK 2.3.2): `set_static_property("PlayerData", "m_Instance.missions[0].progress", ...)` **sets the value and then raises** `UnknownErrorException: Object of type 'BarrierJumpMission' cannot be converted to type 'PlayerData'` (it appears to write the list element back into the wrong parent). Observed 2026-10-07: the progress read back as the new value and the missions popup showed the mission as claimable. `pg_sdk` setup ignores this exception and verifies the write by reading it back. Reading `instance.missions[0]` itself raises `PropertyNotFoundException`, while `instance.missions[0].max` works.
- AltTester Desktop 2.3.3 **stops its server when a licence check cannot reach the internet** and does not start it again after the licence re-activates. Observed 2026-10-08 12:01 (Desktop log: `License check: False: No internet connection`, then `Successful license activation!` with no new `Server started` line): every driver connection then failed until the server was restarted by hand. `pg prove` and `pg baseline` now check the server before starting and after any infra repeat, and stop instead of retrying.
- Reading a member through a static property that is currently null (e.g. `TrackManager.instance` on the main menu) raises `UnknownErrorException`; adapters must treat that as "not available".
- Importing `alttester` prints `SyntaxWarning`s from its dependency `pure-python-adb` 0.3.0.dev0 (invalid escape sequences under Python 3.12) the first time it is compiled. Harmless; we call adb ourselves.

### Confirmed driver API

Each call below is used by our code, matches the installed package's signature, and was exercised against the phone on 2026-10-07 unless marked otherwise.

| Call | Used for | Observed |
|---|---|---|
| `AltDriver(host, port, app_name, timeout)` | connect through AltTester Desktop | `pg doctor`, `pg spike *` |
| `AltDriver.stop()` | release the single driver slot | every command |
| `get_current_scene()`, `get_all_loaded_scenes()`, `wait_for_current_scene_to_be(name, timeout, interval)` | scene state | `Start` → `Main` after tapping START |
| `get_all_elements(enabled=False)`, `AltObject.to_json()`, `AltObject.get_all_components()` | scene dumps | 311 objects (`Start`), 441 (`Main`) |
| `get_png_screenshot(path)` | screenshots | both dumps |
| `add_notification_listener(NotificationType.LOG, callback, overwrite=False)`, `remove_notification_listener(NotificationType.LOG)` | game logs | one delivery per log |
| `call_static_method(type, method, assembly, parameters)` | trigger `UnityEngine.Debug.LogError` | logged in both channels |
| `get_static_property(component, "instance.<field>", assembly, max_depth)` | read `PlayerData`, `TrackManager` | first-launch save values, `isTutorial`, `worldDistance` |
| `find_object(By.PATH, path)`, `AltObject.tap()` | press the START button | scene changed to `Main` |
| `get_time_scale()`, `set_time_scale(scale)` | time-scale spike | game time ×2.00 at scale 2, restored afterwards |
| `find_object(By.COMPONENT, name)`, `AltObject.call_component_method(component, method, assembly, parameters)` | find `ShopUI`, call `CheatCoin` | coins 0 → 1,000,000 |
| `set_static_property(component, path, assembly, value)` | test setup: mark the tutorial done | works via `m_Instance.tutorialDone` with value `"true"`; fails via `instance.tutorialDone` (server bug below) |
| `find_objects(By.PATH, path)` | store rows, mission entries, hearts | rows of every store tab; paths with spaces and `(Clone)` work |
| `find_object(By.PATH, "//*[@id=<id>]/<relative>")` | a child of one specific row | name, prices and button of each store row |
| `AltObject.get_text()` | every displayed value | balances, prices, mission texts, HUD |
| `AltObject.get_component_property(component, property, assembly)` | `UnityEngine.UI.Button.interactable`, `UnityEngine.UI.Text.color`, `UnityEngine.UI.Slider.value`, `UnityEngine.UI.Image.color` (assembly `UnityEngine.UI`) | Buy enabled/disabled, red unaffordable price, slider positions, white/black hearts |
| `AltObject.set_component_property("UnityEngine.UI.Slider", "value", "UnityEngine.UI", v)` | settings sliders | fires `onValueChanged`: music 0.5 → `PlayerData.musicVolume` -40 dB |
| `call_static_method("PlayerData", "m_Instance.Save", "Assembly-CSharp", [])` | save after test setup | an instance method reached through a static field; `instance.Save` works too |
| `get_player_pref_key(key, type)` | (probe only) | raises `NotFoundException` for a missing key |

`NotificationType` is not exported at the package top level; import it from `alttester.commands.Notifications.notification_type`.

## Device

| Field | Value |
|---|---|
| Model | Samsung Galaxy S26 Ultra (SM-S948B) |
| Android | 16 (API 36), One UI 8.5, security patch 2026-08-05 |
| ABI | `arm64-v8a` only, so the APK must be IL2CPP + ARM64 |
| Page size | 4096 bytes |
| Screen | 1440x3120, density 600 |

The adb serial is kept in the local `.env` (`PG_ADB_SERIAL`), not in the repo.

**Android 16 compatibility warning.** Launching the debuggable build shows "Android app compatibility: this app isn't 16 KB-compatible". Unity 2021.3.45f2's libraries are not 16 KB-aligned, but the phone uses 4 KB pages, so the game runs. The dialog blocks Unity from starting, and `pm clear` resets "Don't show again", so every reset taps its OK (ADR-0008).

## Game build

Current build: **build 2, with the seeded-bug hooks and telemetry** (M1.1), built by Dheeru on 2026-10-08 (`Build Finished, Result: Success.` at 11:47 local time) and installed with `adb install -r` the same day.

| Field | Value |
|---|---|
| APK | `C:\Users\dheer\pg_game\Builds\TrashCat.apk`, 80,909,977 bytes |
| APK sha256 | `87d396162a05a085aa11aa9541aa168b15a3d98ce16bc4fc3ad3da431c1a3833` (build tag `87d396162a05`, registered as build 2) |
| Checked | `aapt dump badging`: package `com.DefaultCompany.TrashCat`, versionCode 350, `native-code: 'arm64-v8a'` only, `application-debuggable`, INTERNET, launch activity `com.unity3d.player.UnityPlayerActivity`; the phone's installed `base.apk` hashes to the same sha256 |
| Contents | Build 1 plus `PGBugFlags`, `PGTelemetry` and the 16 hooks of `game/HOOKS.md`; Addressables rebuilt before the build (the extra size is in the asset bundles; the native libraries are arm64 only) |
| Locators | `pg_sdk/locators/87d396162a05.yaml` extends `e63240052d1b` (same UI) |
| Build note | The first attempt stalled for 27 min at "Checking prerequisites": an Android `sdkmanager --list` that Unity started the evening before had hung. Ending that process let the build finish. |

Previous build (build 1, used for Phase 0 and the SDK work): **ARM64-only rebuild** by Dheeru, 2026-10-07 22:08 (`Build Finished, Result: Success.`), installed 22:11. Checked with `aapt dump badging` from Unity's SDK build-tools 34.0.0, `unzip`, and `adb`. Rows not marked otherwise are unchanged from the first build.

| Field | Value |
|---|---|
| APK | `C:\Users\dheer\pg_game\Builds\TrashCat.apk`, 64,108,498 bytes |
| APK sha256 | `e63240052d1b6c889eb2f33a102db25ab123728670a9805a06ac12404b7c324b` (build tag `e63240052d1b`) |
| Android package name | `com.DefaultCompany.TrashCat`. Unity's default `com.<Company>.<Product>`, because "Override Default Package Name" is off in Player settings; adopted as-is. |
| Launch activity | `com.DefaultCompany.TrashCat/com.unity3d.player.UnityPlayerActivity` (`cmd package resolve-activity --brief`) |
| Version | versionCode 350, versionName 1.0; minSdk 22, targetSdk 35 |
| Scripting backend / stripping | IL2CPP / Managed Stripping Level **Minimal** |
| Native code | `arm64-v8a` only (Target Architectures = ARM64) |
| Development build | yes (`application-debuggable`) |
| AltTester SDK inside | 2.3.2: `AltRunner`, `AltTesterPrefab` and `AltTester.AltTesterUnitySDK.*` present in the IL2CPP metadata |
| Permissions | INTERNET, ACCESS_NETWORK_STATE, AD_ID, BILLING (the last two come from the Ads and IAP packages) |
| Installed | 2026-10-07 22:11 with `adb install -r` |
| Checks on this build | `pg doctor` 8/8 ([evidence](evidence/phase0/doctor_all_pass_arm64.txt)); smoke 10/10, p50 8.25 s, p95 8.58 s (`smoke-20261007T1641590000-e2bea7`) |

Earlier build, used for the Phase 0 spike and the 20-run baseline: sha256 `cf86374974de0964efb33bdf9d78664c28f4c4032276501f7b1c800603616bc2`, 121,941,454 bytes, built 2026-10-07 19:05, same settings except native code `arm64-v8a`, `armeabi-v7a` and `x86`.

## Other tools on the device host (not used by this project)

- AltTester Desktop installed its own AltTester CLI v0.1.2 into the user profile and added it to `PATH` (Desktop log, 2026-10-07).
- Unity Hub also installed Unity 6000.6.4f1 (no Android module). The game project does not use it.
