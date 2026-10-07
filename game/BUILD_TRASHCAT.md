# Building the instrumented TrashCat APK

A checklist for Dheeru to follow in Unity. Every step cites its source. AltTester's official docs, their TrashCat walkthrough and the AltTester SDK source come first. Where they don't cover a Unity or Android setting, Unity's own docs or the game's own notes are cited and marked as such.

- `[x]` = already done and verified on 2026-10-07 (evidence linked)
- `[ ]` = still to do

Sources are listed at the bottom ([A1], [U2], …).

---

## 0. Before you start

- [x] Unity **2021.3.45f2** with Android Build Support, OpenJDK, Android SDK & NDK Tools. Evidence: [`docs/evidence/phase0/environment.txt`](../docs/evidence/phase0/environment.txt). AltTester: "Use the Unity Hub to install Android Build Support and the required dependencies: Android SDK & NDK tools, and OpenJDK" [A1].
- [x] AltTester Desktop 2.3.3 is licensed and its built-in server is running on port 13000. Since 2.0.0, Desktop "must be running on your PC while the tests are running" [A1].
- [x] Phone connected over USB with USB debugging authorized (Samsung SM-S948B, `arm64-v8a` only) [D1].
- [ ] On the phone: Developer options → **Stay awake** ON. If the screen sleeps, the game pauses and tests fail for reasons unrelated to the game.

## 1. Game project

- [x] Unity's Endless Runner sample comes from the Unity Asset Store [U1]. The store page lists these supported versions: **2021.3.6f1 (URP)** and **6000.3.0f1 (URP)**; the listing version is 2.0.0, released 2026-09-22 [U1].
- [x] We use **2021.3.45f2**. It's the same LTS line as 2021.3.6f1, and it's the editor AltTester's own TrashCat project uses (`ProjectSettings/ProjectVersion.txt` in [A8]).
- [x] Imported into `C:\Users\dheer\pg_game\TrashCat` (outside this repo). Package file: `Endless Runner Mobile Sample Project.unitypackage`, 66,105,424 bytes, sha256 `669522542709b43deaefecd78a502d7a69fe89db7fddd920a9b269a96c58164c`. The project opens with 0 compile errors.

## 2. Switch the build target to Android

- [ ] **File → Build Settings → Android → Switch Platform.**
  Do this first. The Addressables build in step 5 builds asset bundles "for the current platform" (game's `Assets/INSTRUCTIONS.txt` [G1]), and the AltTester build in step 6 targets Android [A1].

## 3. Player settings

Open **Edit → Project Settings → Player**, then the **Android** tab (the robot icon).

| | Setting | Set to | Currently | Why | Source |
|---|---|---|---|---|---|
| [ ] | Other Settings → Optimization → **Managed Stripping Level** | **Minimal** | Low | Known issue: an IL2CPP app with Managed Stripping Level above Minimal fails to connect. Workaround: "Set the Managed Stripping Level setting to `Minimal` from Player Settings -> Other Settings -> Optimization" | [A2] |
| [ ] | Other Settings → Configuration → **Target Architectures** | **ARM64 only** (untick ARMv7 and x86) | ARMv7 + ARM64 + x86 | The phone only runs `arm64-v8a`. Building one architecture is also much faster. | [U2], [D1] |
| [x] | Other Settings → Configuration → **Scripting Backend** | IL2CPP | IL2CPP | Unity's ARM64 option is only available with IL2CPP ("You can only interact with this setting if your project uses the IL2CPP … back-end") | [U3] |
| [x] | Other Settings → Configuration → **Internet Access** | leave **Auto** | Auto | The SDK reaches AltTester Desktop over a WebSocket, which needs the INTERNET permission. Unity: "Set to Require by default for development builds", and the AltTester build is a development build (step 6). Step 7 checks the permission is in the APK. | [U2] |
| [x] | Resolution and Presentation → **Run In Background** | ON | ON | AltTester's setup steps enable it | [A1] |
| [x] | Other Settings → Identification → **Package Name** | leave as is | `com.DefaultCompany.TrashCat` (first build) | "Override Default Package Name" is off, so Unity builds `com.<Company Name>.<Product Name>`. `PG_ANDROID_PACKAGE` in `.env` must equal the built APK's package, which step 7 checks. | [U2] |

## 4. Add the AltTester Unity SDK 2.3.2

- [ ] Download the **GPL-3** Unity SDK **2.3.2** `.unitypackage` from the downloads page (`AltTesterUnitySDK_2_3_2.unitypackage`, 976,171 bytes) [A5]. Desktop 2.3.3's built-in server reports version 2.3.2.0 [D1], so SDK, server and Python driver all stay on 2.3.2. Never upgrade one without the others (`CLAUDE.md`).
- [ ] Add the two required dependencies to `Packages/manifest.json` → `"dependencies"` [A1]. In this project both are already pulled in indirectly, so list them explicitly at the versions the project already uses:
  ```json
  "com.unity.nuget.newtonsoft-json": "3.2.1",
  "com.unity.editorcoroutines": "1.0.0",
  ```
  AltTester's docs name Newtonsoft JSON `3.1.0` [A1]. This project's `com.unity.services.core` 1.12.5 requires `3.2.1`, so pin 3.2.1 (newer) rather than downgrade it. This is a deliberate deviation from the docs.
- [ ] Skip the optional Input System dependency [A1]. The project uses the old Input Manager (`activeInputHandler: 0`).
- [ ] **Assets → Import Package → Custom Package…** → choose the 2.3.2 `.unitypackage` → import everything [A1].
- [ ] Verify the install: the menu **AltTester® → AltTester® Editor** opens [A1].

## 5. Build Addressables for Android

- [ ] **Window → Asset Management → Addressables → Groups**, then **Build → New Build → Default Build Script** [A4], [A8], [G1].
  TrashCat loads its assets through Addressables, which "calls for a separate Addressable build, before building the application itself" [A4]. Rebuild Addressables before **every** APK build so bundles never go stale.

## 6. Instrument and build with the AltTester Editor

Open **AltTester® → AltTester® Editor** [A1].

- [ ] **Platform:** Android [A1].
- [ ] **Build Location:** click **Browse** and choose `C:\Users\dheer\pg_game\builds`. The APK is written to `<Build Location>\<Product Name>.apk`, which here is `C:\Users\dheer\pg_game\builds\TrashCat.apk` [A6 L325–333].
- [ ] **Settings:** keep the defaults: **AltTester® Server Host** `127.0.0.1`, **AltTester® Server Port** `13000`, **App Name** `__default__` [A7 L30–33]. These match `PG_ALTTESTER_HOST` / `PG_ALTTESTER_PORT` and the Python driver's default app name.
- [ ] Leave **Append "Test"…** **unticked**. When ticked, the build appends `Test` to the product name and the package name (for example `com.DefaultCompany.TrashCatTest`) [A6 L48–50], which would no longer match `PG_ANDROID_PACKAGE`.
- [ ] Leave **Reset Connection Data** and **Hide Green Popup** unticked. Leave **Keep ALTTESTER symbol defined** at its default (ticked) [A7 L53].
- [ ] **Scenes:** select `Start`, `Main` and `Shop`, with **Start first**. AltTester inserts its prefab into "the first scene of the app" [A3]. Leave out `SampleScene`, which the Unity template left behind.
- [ ] Click **Build Only**.
  - The AltTester Editor always makes a **Development** build [A6 L399]. That matters: "If you instrument your app in release mode, AltTester® Prefab self removes from the scenes and the socket server does not start" [A3]. Do **not** build through File → Build Settings → Build.
  - Use **Build Only**, not **Build & Run**. We install with our own adb in step 7. The editor's **Adb Path** setting defaults to a macOS path (`/usr/local/bin/adb`) [A7].
  - The first IL2CPP build is slow. If the build fails, stop and report it; don't work around it (`CLAUDE.md`).

## 7. Install, check and record

Claude can run these and fill `docs/VERSIONS.md` for you to review. `<serial>` is `PG_ADB_SERIAL` from `.env`.

- [ ] Install: `adb -s <serial> install -r C:\Users\dheer\pg_game\builds\TrashCat.apk`
- [ ] Inspect the APK with `aapt` from Unity's SDK (`…/AndroidPlayer/SDK/build-tools/34.0.0/aapt.exe dump badging TrashCat.apk`). Check that:
  - `package: name='<PG_ANDROID_PACKAGE>'` (first build: `com.DefaultCompany.TrashCat`)
  - `native-code: 'arm64-v8a'`, and nothing else
  - `uses-permission: name='android.permission.INTERNET'`
  - the `launchable-activity` line is present
- [ ] Record the launch activity: `adb -s <serial> shell cmd package resolve-activity --brief <PG_ANDROID_PACKAGE>`. The last line is `<package>/<activity>`. On this phone the same command for `com.android.settings` prints `com.android.settings/.Settings`.
- [ ] Record in `docs/VERSIONS.md` → "Game build": package name, launch activity, scripting backend / architectures / stripping, AltTester SDK version, APK sha256 (`certutil -hashfile TrashCat.apk SHA256`). Then set `PG_APK_PATH` in `.env`.

## 8. Connect over USB

- [ ] `adb -s <serial> reverse tcp:13000 tcp:13000`. The syntax is `adb [-s UDID] reverse tcp:device_port tcp:local_port` [A3], and AltTester's walkthrough uses `adb reverse tcp:13000 tcp:13000` for 2.0.x [A4]. Inside the app, `127.0.0.1:13000` then reaches AltTester Desktop on the PC.
- [ ] Verify: `adb -s <serial> reverse --list` prints a line ending in `tcp:13000 tcp:13000` [ADB]. The mapping is lost when the phone reconnects or the adb server restarts. `pg doctor` (M0.3) checks for it before every run.
- [ ] Launch TrashCat on the phone. The AltTester green popup shows "Waiting for connections on port: {Port}" until the app connects, then disappears [A1]. The app then appears in AltTester Desktop's app list.
- [ ] Before automated runs, close any AltTester Desktop inspector connection. The free plan allows 1 app and 1 driver at a time (`CLAUDE.md`).

## If something goes wrong

- **App never appears in Desktop:** check `adb reverse --list`, that Desktop's server is ON, Managed Stripping Level is Minimal [A2], and that you built with the AltTester Editor (development build) [A3].
- **Build or import errors:** stop and report them with the Console output. These are "stop and ask" cases in the Phase 0 plan.

---

## Sources

| ID | Source |
|---|---|
| A1 | AltTester Unity SDK docs, Get started (2.3.2): https://alttester.com/docs/sdk/latest/pages/get-started.html |
| A2 | AltTester Unity SDK docs, Known issues: https://alttester.com/docs/sdk/latest/pages/known-issues.html |
| A3 | AltTester Unity SDK docs, Advanced usage: https://alttester.com/docs/sdk/latest/pages/advanced-usage.html |
| A4 | AltTester walkthrough, Upgrading TrashCat to 2.0.x: https://alttester.com/walkthrough-tutorial-upgrading-trashcat-to-2-0-x/ |
| A5 | AltTester downloads: https://alttester.com/downloads/ |
| A6 | AltTester SDK source at tag 2.3.2, `AltBuilder.cs`: https://github.com/alttester/AltTester-Unity-SDK/blob/2.3.2/Assets/AltTester/Editor/Scripts/AltTesterEditor/AltBuilder.cs |
| A7 | AltTester SDK source at tag 2.3.2, `AltEditorConfiguration.cs`: https://github.com/alttester/AltTester-Unity-SDK/blob/2.3.2/Assets/AltTester/Editor/Scripts/AltTesterEditor/AltEditorConfiguration.cs |
| A8 | AltTester's TrashCat project (README: Addressables build; `ProjectVersion.txt`: 2021.3.45f2): https://github.com/alttester/trashcat |
| U1 | Unity Asset Store, Endless Runner Mobile Sample Project: https://assetstore.unity.com/packages/essentials/tutorial-projects/endless-runner-sample-game-87901 |
| U2 | Unity 2021.3 manual, Android Player settings (Unity docs, not AltTester): https://docs.unity3d.com/2021.3/Documentation/Manual/class-PlayerSettingsAndroid.html |
| U3 | Unity 2021.3 manual, Delivering to Google Play, section "64-bit Architecture" (Unity docs, not AltTester): https://docs.unity3d.com/2021.3/Documentation/Manual/android-distribution-google-play.html |
| G1 | The game's own notes, `Assets/INSTRUCTIONS.txt` in the Unity project (Unity's sample, not committed here) |
| D1 | Device and toolchain evidence: [`docs/evidence/phase0/environment.txt`](../docs/evidence/phase0/environment.txt) |
| ADB | `adb --help`, platform-tools 37.0.1: `reverse --list` lists all reverse socket connections from the device |
