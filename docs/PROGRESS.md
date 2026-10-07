# Progress

Status of each phase: what works, what was verified (with evidence) and what is not done. Numbers here come from evidence files or stored runs, never typed from memory.

## Phase 1: Core trust loop

Started 2026-10-07. Plan approved by Dheeru on 2026-10-07.

### Decisions (2026-10-07)

- **Order:** hooks (M1.1) → catalog (M1.2) → `pg_sdk` (M1.3) → gates (M1.5) → runner (M1.6) → persistence (M1.7) → generator (M1.4) → CLI (M1.8) → results (M1.9). The rebuild is the critical path; the generator needs the SDK manifest.
- **Database:** local Postgres in Docker for Phase 1; the same Alembic migrations move to Supabase in Phase 2.
- **LLM:** Google Gemini API free tier, model `gemini-3.8-flash` (listed "Free of charge" on [the pricing page](https://ai.google.dev/gemini-api/docs/pricing), checked 2026-10-07). Free-tier prompts are used by Google to improve its products; our prompts carry only specs, the SDK manifest and a game summary. LLM cost is recorded at the configured rate of 0.
- **Hook edits:** Claude applies the edits listed in `game/HOOKS.md`; Dheeru reviews in Unity and builds.
- **G4 novelty:** candidates of the same generation run count against each other, in trust-score order (to be recorded in ADR-0004).
- **G5 budget:** 120 s median test runtime, excluding reset and connect.
- **Seeded bugs:** the 16 proposed bugs, with one change: SB13 shows the run's coins instead of distance on the game-over screen, because score equals distance at multiplier 1 and the swap would be invisible (`TrackManager.AddScore`).

### Findings from the live SDK work (2026-10-07)

- **The store reloads the save file when it opens** (`ShopUI.Start` → `PlayerData.Create`). Unsaved in-memory changes vanish, so every `game.setup.*` helper saves after writing.
- **Selections are not saved when changed** (`LoadoutState.ChangeCharacter` and friends do not call `Save`): after a restart the clean build shows Trash Cat again. This contradicts **PERSIST-3**; any correct test of it fails G2. Raised with Dheeru.
- AltTester 2.3.2 sets a list element's field and then raises (`docs/VERSIONS.md`); setup verifies such writes by reading them back.
- `GAME_MODEL.md` corrected: each store tab has its own list, the buy button is `BuyButton`, mission progress is `Image/Reward/Text`.

### Milestones

| Milestone | State | Evidence |
|---|---|---|
| M1.1 Game hooks | Scripts and 16 hooks written and applied to the Unity project (18 marked lines, `grep -rn "PG hook"`); waiting for Dheeru's build | [`game/HOOKS.md`](../game/HOOKS.md), [`game/unity_scripts/`](../game/unity_scripts/) |
| M1.2 Bug catalog | 16 bugs, seeded stratified split 10 dev / 6 holdout (holdout SB01, SB03, SB06, SB07, SB10, SB12); `pg bugs check` passes. **Freeze pending Dheeru's approval of the split** | [`benchmark/bugs.yaml`](../benchmark/bugs.yaml), `pg_core/catalog.py` |
| M1.3 `pg_sdk` | Pages, locators, setup helpers, model readers, manifest (11 classes, 110 members) and pytest plugin written; live suite 6/6 on the clean build `e63240052d1b` with 0 game errors; fake-driver unit tests. Locators for the hooked build and the human baseline still to come | [`sdk_live_clean_e63240052d1b.json`](evidence/phase1/sdk_live_clean_e63240052d1b.json), `pg_sdk/manifest.json` |

## Phase 0: Foundation and feasibility spike

Started 2026-10-07. Plan approved by Dheeru on 2026-10-07.

### Milestones

| Milestone | State | Evidence |
|---|---|---|
| M0.1 Repository scaffold | Done | commits `5999222`..`5864dc3`; local checks below; first CI run green: [run 37616434447](https://github.com/Dheeru-Reddy-ui/proving-ground/actions/runs/37616434447) |
| M0.2 Build checklist (`game/BUILD_TRASHCAT.md`) | Done: Dheeru built the instrumented APK (19:05), installed and verified 2026-10-07; rebuilt ARM64-only at 22:08 (64.1 MB, down from 121.9 MB), with `pg doctor` 8/8 and smoke 10/10 on it | [`game/BUILD_TRASHCAT.md`](../game/BUILD_TRASHCAT.md); build facts in [`docs/VERSIONS.md`](VERSIONS.md) |
| M0.3 `pg doctor` | Done: 8/8 PASS live on 2026-10-07 | [`docs/evidence/phase0/doctor_all_pass.txt`](evidence/phase0/doctor_all_pass.txt); rules in `pg_core/doctor.py` (100% line and branch coverage) |
| M0.4 Connectivity and introspection spike | Done, all six steps live on 2026-10-07: connect; dumps + screenshots; log capture in both channels; reset (`pm clear` + relaunch to `Start`) 8.0 s / 12.1 s / 10.4 s over 3 runs, first-launch save verified each time, including after the save held 2,000,000 coins; time scale ×2.00 game time and ×2.07 distance on a normal run. Side effects: the game's Pause/Resume and tutorial prompts reset the time scale. Driver/server issues recorded in `docs/VERSIONS.md` | [`spike/`](evidence/phase0/spike/), [`logs_check.json`](evidence/phase0/logs_check.json), [`scenes/`](evidence/phase0/scenes/) |
| M0.5 Game model | Done pending review: every screen visited on the phone with a dump and screenshot (Start, main menu first launch and after a run, missions, settings, leaderboard, store x4 tabs, run HUD, second chance, game over). Buying and mission claiming not exercised yet | [`docs/game/GAME_MODEL.md`](game/GAME_MODEL.md), [`scenes/`](evidence/phase0/scenes/) |
| M0.6 Feature specs | Done: 6 files, 58 statements (4 marked `[confirm]`), committed `a60b9d2` at Dheeru's request, before any bug catalog | [`specs/`](../specs/) |
| M0.7 Reliability baseline (20 smoke runs) | Done: **20/20 passed**, p50 8.31 s, p95 8.55 s (run `smoke-20261007T1620250000-b47a01`, 2026-10-07). Earlier attempt failed 0/3 at `connect` because Android 16's compatibility dialog blocked Unity; fixed by ADR-0008 | [`smoke/`](evidence/phase0/smoke/), [ADR-0008](adr/0008-android-compat-dialog.md) |
| M0.8 ADRs 0001–0003 | Written: 0001 (target game and tooling), 0002 (free-plan limits), 0003 (license options; decision pending), plus 0008 (Android 16 compatibility dialog) | `docs/adr/` |

### M0.7 reliability baseline

Scenario: `pm clear` + launch (dismissing the compatibility dialog) → Start → tap START → main menu → open store → close store. Each run starts from a fresh reset. Durations are nearest-rank percentiles over passing runs.

| Run ID | Result | p50 | p95 | Notes |
|---|---|---|---|---|
| `smoke-20261007T1620250000-b47a01` | **20/20 passed** | 8.31 s | 8.55 s | dialog dismissed 20/20; 0 error logs. Step medians: reset 2.75 s, connect 4.73 s, START → menu 0.34 s, open store 0.23 s, close store 0.23 s |
| `smoke-20261007T1611210000-4631ed` | 0/3 passed | – | – | all 3 failed at `connect` after 60 s (`NoAppConnected`): Android 16's 16 KB compatibility dialog blocked Unity from starting. Fixed by ADR-0008 |
| (unrecorded) | stopped | – | – | first 20-run attempt, stopped by Claude after the same dialog appeared on the phone; no result file was written |

### Phase 0 exit gate

| Item | State | Evidence |
|---|---|---|
| `uv run pg doctor` all PASS | ✅ | [`doctor_all_pass.txt`](evidence/phase0/doctor_all_pass.txt) |
| Scene dumps and screenshots for every screen in GAME_MODEL.md | ✅ dumps committed; screenshots kept locally in `artifacts/spike/cf86374974de/` (game imagery is not committed until Dheeru decides) | [`scenes/`](evidence/phase0/scenes/) |
| Log capture verified in both channels | ✅ | [`logs_check.json`](evidence/phase0/logs_check.json) |
| Reset verified; time measured | ✅ 7.5–12.1 s across runs | [`spike/`](evidence/phase0/spike/) |
| Smoke test ≥ 19/20, numbers in PROGRESS.md | ✅ 20/20 | above |
| Specs committed (`git log --oneline -- specs/`) | ✅ commit `a60b9d2`, before any bug catalog | `specs/` |
| CI green on GitHub | ✅ on every push so far | GitHub Actions |
| VERSIONS.md complete, including confirmed driver API | ✅ | [`VERSIONS.md`](VERSIONS.md) |

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

- License decision (ADR-0003).
- License: options in ADR-0003; Dheeru decides.

### Known risks carried forward

- **First-launch flow** (observed 2026-10-07): no licence popup in this build; START loads `Main`; the main menu shows `TutorialOverlay` but the store is reachable; the tutorial does **not** start by itself (25 s hands-off watch) — the earlier unexpected run was most likely a touch on the phone. The first Run is the tutorial, which pauses at each obstacle until a swipe.
- **Shop rows refresh affordability only when built:** setup that grants currency must do so before opening the store (or re-open the tab).
- **Determinism lever for Phase 1:** `TrackManager.trackSeed` is a settable property (`TrackManager.cs`), so runs can be pinned to one track layout.
- **Player settings to change in M0.2** (both covered by the checklist): managed stripping is Low (AltTester's known issue requires Minimal for IL2CPP); target architectures are ARMv7 + ARM64 + x86 (the phone only needs ARM64).
- **Licence window.** The driver may only be used with a valid AltTester subscription; continuing past the trial depends on the Lite request.
- **For Phase 1 (G1 static gate):** `pg_sdk._driver` wraps the raw AltTester driver. Generated tests may import `pg_sdk.*`, so G1 must also reject imports of underscore modules such as `pg_sdk._driver`.
