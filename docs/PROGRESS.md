# Progress

Status of each phase: what works, what was verified (with evidence) and what is not done. Numbers here come from evidence files or stored runs, never typed from memory.

## Phase 0: Foundation and feasibility spike

Started 2026-10-07. Plan approved by Dheeru on 2026-10-07.

### Milestones

| Milestone | State | Evidence |
|---|---|---|
| M0.1 Repository scaffold | Done locally; CI pending first push | commits `5999222`..HEAD; local checks below |
| M0.2 Build checklist (`game/BUILD_TRASHCAT.md`) | Not started | |
| M0.3 `pg doctor` | Not started | |
| M0.4 Connectivity and introspection spike | Not started | |
| M0.5 Game model | Not started | |
| M0.6 Feature specs | Not started | |
| M0.7 Reliability baseline (20 smoke runs) | Not started | |
| M0.8 ADRs 0001–0003 | ADR-0003 (options) written; 0001 and 0002 not started | `docs/adr/` |

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

- M0.2 to M0.8.
- CI on GitHub has not run yet.
- The AltTester SDK is not imported into the game yet, and no APK exists.
- License: options in ADR-0003; Dheeru decides.

### Known risks carried forward

- **First-run tutorial.** After `pm clear`, `PlayerData.tutorialDone` is false: the loadout screen shows a `tutorialBlocker` and the first run is the tutorial (game scripts `LoadoutState.cs`, `GameState.cs`). Every test starts from `pm clear`, so M0.4/M0.5 must establish what the blocker blocks.
- **Player settings to change in M0.2:** managed stripping is Low (AltTester's known issue requires Minimal for IL2CPP); target architectures are ARMv7 + ARM64 + x86 (the phone only needs ARM64).
- **Phone "Stay awake" is off.** A sleeping screen pauses the game; Dheeru to enable it before automated runs.
- **Licence window.** The driver may only be used with a valid AltTester subscription; continuing past the trial depends on the Lite request.
