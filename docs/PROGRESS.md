# Progress

Status of each phase: what works, what was verified (with evidence) and what is not done. Numbers here come from evidence files or stored runs, never typed from memory.

## Phase 0: Foundation and feasibility spike

Started 2026-10-07. Plan approved by Dheeru on 2026-10-07.

### Milestones

| Milestone | State | Evidence |
|---|---|---|
| M0.1 Repository scaffold | Done | commits `5999222`..`5864dc3`; local checks below; first CI run green: [run 37616434447](https://github.com/Dheeru-Reddy-ui/proving-ground/actions/runs/37616434447) |
| M0.2 Build checklist (`game/BUILD_TRASHCAT.md`) | Done: Dheeru built the instrumented APK (19:05), installed and verified 2026-10-07. ARM64-only was not applied (APK has 3 ABIs); fix on the next rebuild | [`game/BUILD_TRASHCAT.md`](../game/BUILD_TRASHCAT.md); build facts in [`docs/VERSIONS.md`](VERSIONS.md) |
| M0.3 `pg doctor` | Done: 8/8 PASS live on 2026-10-07 | [`docs/evidence/phase0/doctor_all_pass.txt`](evidence/phase0/doctor_all_pass.txt); rules in `pg_core/doctor.py` (100% line and branch coverage) |
| M0.4 Connectivity and introspection spike | Live: connect, dumps (`Start` 311 objects, `Main` 441) and log capture in both channels verified. Reset timing and time scale not yet run. Two driver bugs recorded (duplicate log notifications, worked around with `overwrite=False`; stack traces never delivered). A dump race fixed (objects vanishing mid-dump are counted, not fatal) | [`logs_check.json`](evidence/phase0/logs_check.json), [`scenes/`](evidence/phase0/scenes/), [`driver-notification-duplicate.txt`](evidence/phase0/driver-notification-duplicate.txt) |
| M0.5 Game model | Source pass done; `Start` and main-menu paths mapped from device dumps; store, run and game-over screens pending | [`docs/game/GAME_MODEL.md`](game/GAME_MODEL.md) |
| M0.6 Feature specs | Drafts written (6 files, 59 statements, 4 marked `[confirm]`), left uncommitted for Dheeru to review, edit and commit | `specs/` (Dheeru's commit pending) |
| M0.7 Reliability baseline (20 smoke runs) | Not started | |
| M0.8 ADRs 0001–0003 | Written: 0001 (target game and tooling), 0002 (free-plan limits), 0003 (license options; decision pending) | `docs/adr/` |

### M0.1 verification (local, 2026-10-07)

- `uv run ruff check .` and `uv run ruff format --check .`: pass.
- `uv run mypy pg_core pg_sdk pg_generator pg_runner`: no issues.
- `uv run pytest tests/unit -q --cov=pg_core --cov-fail-under=90`: 11 passed.
- `uv run pg --help` and `uv run pg version` work; the driver reports 2.3.2.
- `.gitignore` checked with probe files: `.env`, `*.apk`, `artifacts/` and stray files under `game/` are ignored; `game/HOOKS.md`, `game/BUILD_TRASHCAT.md` and `game/unity_scripts/` are tracked.
- `CLAUDE.md` is identical to the Part A fence of `docs/BUILD_PLAN.md` (lines 45–153); both docs are byte-identical to the files Dheeru provided.

### Environment verified (outside the repo)

Raw output: [`docs/evidence/phase0/environment.txt`](evidence/phase0/environment.txt). Versions: [`docs/VERSIONS.md`](VERSIONS.md).

- Unity 2021.3.45f2 with Android Build Support, OpenJDK, SDK and NDK installed.
- The Endless Runner sample is imported into the game project, which opens in 2021.3.45f2 with 0 compile errors and 0 C# warnings (editor log, 2026-10-07). Build scenes: Start, Main, Shop. Render pipeline: URP. Android Player settings already use IL2CPP.
- AltTester Desktop 2.3.3 licensed (Pro trial); built-in server 2.3.2.0 listening on port 13000.
- Phone connected over USB and authorized for adb: Samsung SM-S948B, Android 16, `arm64-v8a` only, 4 KB pages.

### Decisions (2026-10-07)

- Game: the Asset Store Endless Runner sample on Unity 2021.3.45f2, the editor AltTester's own TrashCat project uses.
- Device: a physical arm64 phone over USB.
- AltTester: 30-day Pro trial now (it ends about 2026-11-06); Lite requested. The design stays within free-plan limits either way (ADR-0002, to be written in M0.8).
- Repository: public, `proving-ground` on GitHub.
- `docs/PROBLEM_STATEMENT.md` V3 now records that AltTester Lite is granted on request only.

### Not done or not verified

- M0.4: reset timing and time scale; M0.5: store, run and game-over dumps; M0.6: Dheeru's review and commit of the specs; M0.7: smoke baseline.
- License: options in ADR-0003; Dheeru decides.

### Known risks carried forward

- **First-launch flow** (observed 2026-10-07 on a fresh install):
  - There is no licence popup in this build. `LicenceDisplayer` is unused, which corrects the source-only reading.
  - START loads `Main` cleanly. The main menu then shows `TutorialOverlay` while `tutorialDone` is false.
  - In the first spike a tutorial run started without any AltTester tap after START. A touch on the phone is not ruled out, so M0.4 repeats this hands-off. How a reset test reaches the store (M0.7) depends on the answer.
- **Determinism lever for Phase 1:** `TrackManager.trackSeed` is a settable property (`TrackManager.cs`), so runs can be pinned to one track layout.
- **Player settings to change in M0.2** (both covered by the checklist): managed stripping is Low (AltTester's known issue requires Minimal for IL2CPP); target architectures are ARMv7 + ARM64 + x86 (the phone only needs ARM64).
- **Licence window.** The driver may only be used with a valid AltTester subscription; continuing past the trial depends on the Lite request.
- **For Phase 1 (G1 static gate):** `pg_sdk._driver` wraps the raw AltTester driver. Generated tests may import `pg_sdk.*`, so G1 must also reject imports of underscore modules such as `pg_sdk._driver`.
