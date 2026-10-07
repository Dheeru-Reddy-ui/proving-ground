# ADR-0008: Dismiss Android 16's 16 KB compatibility warning during resets

- **Status:** Accepted (option A chosen by Dheeru on 2026-10-07)
- **Date:** 2026-10-07
- **Deciders:** Dheeru (owner)

ADR numbers 0004–0007 are reserved by the build plan for later phases, so this takes 0008.

## Context

- On the test phone (Android 16), launching the instrumented build shows a system dialog, **"Android app compatibility"**. It appears because the app is debuggable and its native libraries (`libunity.so`, `libil2cpp.so`, `libmain.so`, `lib_burst_generated.so`) are not 16 KB-aligned. Unity 2021.3.45f2 does not produce 16 KB-aligned libraries.
- The phone itself uses 4 KB pages (`docs/evidence/phase0/environment.txt`), so the game runs correctly. The dialog is only a warning.
- **The dialog blocks Unity.** With the dialog up, the game process starts but Unity's engine does not initialise. In one launch the process started at 21:43:23 and Unity initialised at 21:45:08, after the dialog was gone. The game never connects to AltTester in the meantime.
- "Don't show again" is **reset by `pm clear`**: Dheeru tapped it, and the next clear and launch showed the dialog again. Every test starts from `pm clear` (build plan M0.4, M1.3).
- Evidence: a 3-run smoke failed 0/3, each run at `connect` after 60 s with `NoAppConnected` ([`smoke-20261007T1611210000-4631ed.json`](../evidence/phase0/smoke/smoke-20261007T1611210000-4631ed.json)).

## Decision

Keep `pm clear` as the reset. After launching, `pg_runner.launch.fresh_launch` polls the screen's accessibility tree (`uiautomator dump`) for up to 6 s. If the dialog is showing, it taps **OK**.

- **Matching** (`pg_core.android_ui.compat_dialog_ok`) requires all of the following:
  - a window owned by `android`;
  - title `android:id/alertTitle` = "Android app compatibility";
  - a message `android:id/message` mentioning "16 KB";
  - `android:id/button2` labelled "OK".
- **Never pressed:** "Don't show again" (`button1`). No device setting is changed.
- **Recorded:** `compat_dialog_dismissed` for every reset and smoke run.

Measured live: dialog found and dismissed 2.5 s after launch; Start screen reached 7.5 s after the reset began.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| B. Reset without `pm clear` (force-stop, delete the save and preference files) so "Don't show again" sticks | Changes the planned reset method. It would need proof that it reaches the same first-launch state as `pm clear`. |
| C. Upgrade Unity to a version that builds 16 KB-aligned libraries (Unity 6, or 2022.3.56+) | Removes the cause, but means a new editor, a rebuild and re-verification of the whole chain: too large for Phase 0. Kept as a later hardening option. |
| Tap "Don't show again" from automation | Changes a device setting, and is undone by the next `pm clear` anyway. |
| Press the Back key blindly after launch | When the dialog is not showing, Back goes to the game. Unsafe. |

## Consequences

- **Cost:** each reset is about 2.5 s longer when the dialog shows, and up to 6 s longer if it never does (the poll runs out).
- **Fragility:** matching depends on the phone's English system-UI text. A different locale or Android version makes the matcher return "not found", and the run then fails at `connect` with a clear error rather than tapping something wrong.
- **Phase 1:** the pytest plugin's reset must use `fresh_launch`.
- **Follow-up:** if Unity is upgraded (option C), this step should find no dialog. Smoke results record that per run.

## References

- [Android: 16 KB page sizes](https://developer.android.com/16kb-page-size) (linked from the dialog)
- `docs/VERSIONS.md` (device and build facts), ADR-0001 (engine version choice)
