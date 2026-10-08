# Progress

Status of each phase: what works, what was verified (with evidence) and what is not done. Numbers here come from evidence files or stored runs, never typed from memory.

## Phase 2: Production platform

Started 2026-10-08. Plan approved by Dheeru on 2026-10-08 with every recommended default.

### Decisions (2026-10-08)

- **Hosting on free tiers** (ADR-0010). Render's free instances exist only for web services, Postgres and Key Value, and pre-deploy commands are paid-only ([render.com/docs/free](https://render.com/docs/free), [render.com/docs/deploys](https://render.com/docs/deploys), checked 2026-10-08). So:
  - the worker loop runs **inside the pg_api web service** (`PG_EMBEDDED_WORKER=true`); the same code runs standalone as `pg worker run`;
  - migrations run from the release workflow before it calls Render's deploy hook, never at app start.
- **APKs are stored in parts.** Supabase Storage's Free plan caps a file at 50 MB and the cap cannot be raised ([file limits](https://supabase.com/docs/guides/storage/uploads/file-limits)); build 2's APK is 64.1 MB. Each APK goes to a private bucket as parts of at most 45 MB plus a manifest of sha256s; the agent joins and verifies them. APKs never go into a GitHub release (public repo, Asset Store content).
- **Database:** Supabase Free, reached through the session pooler (IPv4, prepared statements supported; the direct connection is IPv6-only on Free, [docs](https://supabase.com/docs/guides/database/connecting-to-postgres)). The local Phase 1 database is copied in once so the demo shows runs 5 and 6 with the same IDs; the local copy stays untouched.
- **LLM tracing:** an `llm_calls` table, not Langfuse.
- **Metrics:** `/metrics` computed from the existing tables on request; nothing scrapes on the free tier.
- **Public demo mode** hides screenshots (the Phase 0 game-imagery decision is still open).
- **Keep-alive:** a weekly scheduled workflow calls `/readyz`, because Supabase pauses Free projects after 7 days of low activity ([docs](https://supabase.com/docs/guides/platform/free-project-pausing)).
- **Cloud resources:** Dheeru creates the Supabase project and the Render web service and enters the secrets. Secret locations as approved: `DATABASE_URL` in `.env`, Render env and a GitHub Actions secret; the Supabase service key in Render env only; agent and CLI tokens stored hashed on the server.
- **Second build:** Dheeru makes a no-change rebuild of the hooked game (new sha256) so one build can be registered through the webhook path and one through the CLI path.

### Milestones

| Milestone | State | Evidence |
|---|---|---|
| M2.1 Jobs, leasing, orchestrator | Done (local): `jobs` and `validations` tables (migration 0002, additive); claim with `FOR UPDATE SKIP LOCKED` + per-owner `max_concurrency`; lease tokens, heartbeats, reaper, dead jobs, admin retry; idempotent enqueue and completion; pure DAG planner that reuses the Phase 1 gate rules; every job event advances its validation in the same transaction. Integration tests: 6 concurrent claimers over 40 jobs never share one; a locked job is skipped, not waited for; a killed agent's job is retried and stored once; infra every attempt → dead with the error; a whole validation runs with a fake worker and agent and never schedules a holdout or irrelevant bug. Unit: 135 parity cases show the planner makes the same runs as `pg prove` | `pg_core/jobs.py`, `pg_core/orchestrator.py`, `pg_db/jobs.py`, `pg_db/pipeline.py`; `tests/integration/test_jobs_queue.py`, `test_pipeline.py`; ADR-0006 |
| M2.2 Control-plane API | Done (local): every `/v1` endpoint of the plan plus `/v1/apk/*` (APK parts), `/v1/code/{sha}`, `/v1/jobs/{id}/fail`, `/release`, `/retry`, `/session`; OpenAPI at `/openapi.json`. Agent and admin bearer tokens stored as sha256, one-time expiring enrolment tokens, admin session (argon2, signed HttpOnly SameSite=Strict cookie, CSRF on every write). Webhook: HMAC-SHA256 over `timestamp.body`, constant-time compare, ±5 min, delivery-id dedupe, idempotent on the APK hash. Public demo mode hides artifacts and reduces failure details to one line; home-directory paths are scrubbed from every view. Per-IP token buckets, JSON 413 body limit, request IDs, JSON logs, `/readyz` (database + migrations at head), 503 on database loss. Live check on 2026-10-08: `pg api serve` against the local Phase 1 database answered `/readyz` ready and served candidate c13 as ACCEPT with its SB02 kill, as in the Phase 1 report. **Not verified yet:** Supabase Storage live (calls taken from Supabase's Python client and tested with a mocked transport), and how many proxies Render puts in `X-Forwarded-For` (`PG_TRUSTED_PROXY_HOPS`) | `pg_api/`, migration 0003; `tests/integration/test_api.py` (14), `tests/unit/test_api_parts.py`, `test_webhook_ratelimit_builds.py` |
| M2.3 Worker | Done (local): `pg_worker` claims GENERATE and SCORE, heartbeats from its own thread, reaps leases, records itself in `workers`; standalone (`pg worker run`) or embedded in the API. GENERATE reuses `pg_generator` unchanged inside per-run, per-build and per-day budgets (a spent day defers to the next UTC day, a spent build fails the job); each call is traced in `llm_calls` (no prompt text, no secrets). SCORE recomputes the gates from stored executions: on the local database it reproduces all 16 decisions of Phase 1 runs 5 and 6 exactly. Kill switch `PG_GENERATION_ENABLED=false` leaves GENERATE queued. Live on 2026-10-08 against Gemini 3.5 Flash on a throwaway database: 2 candidates, both pass G1, one call traced, cost recorded as 0 (free tier) | [`worker_live_gemini.txt`](evidence/phase2/worker_live_gemini.txt), [`scoring_parity_runs_5_6.txt`](evidence/phase2/scoring_parity_runs_5_6.txt); `tests/integration/test_worker.py` (7) |
| M2.4 Device agent | Code done; **live run on the phone not done yet** (on 2026-10-08 the phone was not attached: `pg agent status` correctly reported `adb device` FAIL and UNHEALTHY). `pg agent enroll` stores the agent token in a file whose ACL grants only the current Windows user (verified by test). `pg agent run`: quick doctor checks before each claim, full doctor at start and after infra, heartbeat thread, two-stage Ctrl+C, orphaned runners killed at start, `max_concurrency: 1`. INSTALL_BUILD downloads APK parts by signed URL, verifies each part and the whole file, `adb install -r`, verifies on the device. RUN_TEST re-hashes the code, refuses another SDK manifest, re-runs G1 locally, runs through `pg_runner`, uploads artifacts, reports every attempt. End-to-end test over real HTTP (uvicorn, worker with a fake LLM, agent with a fake phone): APK in 3 parts uploaded and downloaded, one install, all four expected decisions. `docs/RUNBOOK.md` written | `pg_agent/`, `tests/unit/test_agent.py` (20), `tests/integration/test_agent_e2e.py` |
| M2.5 Dashboard | Done (local): server-rendered pages for builds, build detail (accepted, review, rejected; G2 rate; LLM cost and device time per accepted test; infra attempts), generation runs with the dev kill matrix and LLM calls, validation progress (HTMX polling every 5 s), candidate detail (spec text, code, each gate's reasons and measurements, kills with evidence ids, executions, screenshots and logs for the admin), review queue (inline decision with reason, CSRF), System page (agents, workers and the generation kill switch, queue depth, dead jobs with retry, enrolment tokens, today's LLM spend). Public demo mode is read-only and hides artifacts, human reasons and health detail. CSP `default-src 'self'` with no inline styles or scripts, `frame-ancestors 'none'`; system fonts; light and dark themes. Checked in the browser pane on 2026-10-08 against the local Phase 1 data: build 2 shows 1 accepted of 16, G2 14 of 15, 64.1 device minutes, 3 infra of 149 attempts (as in the Phase 1 report); run 5's matrix shows c13 killing SB02 2 of 2; every page fits a 375 px screen without sideways scrolling. htmx 2.0.11 vendored (0BSD), sha256 checked against jsDelivr | `pg_dashboard/`, `tests/integration/test_dashboard.py` (6, including escaping of hostile generated code) |

## Phase 1: Core trust loop

Started 2026-10-07. Plan approved by Dheeru on 2026-10-07. **Closed 2026-10-08** with one exit-gate item deferred to Phase 4 (human baseline, ADR-0009).

### Decisions (2026-10-07)

- **Order:** hooks (M1.1) → catalog (M1.2) → `pg_sdk` (M1.3) → gates (M1.5) → runner (M1.6) → persistence (M1.7) → generator (M1.4) → CLI (M1.8) → results (M1.9). The rebuild is the critical path; the generator needs the SDK manifest.
- **Database:** local Postgres in Docker for Phase 1; the same Alembic migrations move to Supabase in Phase 2.
- **LLM:** Google Gemini API free tier, model `gemini-3.8-flash` (listed "Free of charge" on [the pricing page](https://ai.google.dev/gemini-api/docs/pricing), checked 2026-10-07). Free-tier prompts are used by Google to improve its products; our prompts carry only specs, the SDK manifest and a game summary. LLM cost is recorded at the configured rate of 0.
- **Hook edits:** Claude applies the edits listed in `game/HOOKS.md`; Dheeru reviews in Unity and builds.
- **LLM model switched to `gemini-3.8-flash` → `gemini-3.5-flash`** (2026-10-08, approved by Dheeru). Generation runs 2, 3 and 4 failed: the API answered 503 "high demand" to every retry. Gemini 3.5 Flash is also free on the pricing page and was confirmed through the models API. All Phase 1 result runs (5 and 6) use it with prompt `generate_tests_v2`; the failed runs stay in the database.
- **Second feature for M1.9: `run_and_gameover`**, by a rule fixed before generation: the non-store spec file with the most statements (14). Not chosen by where the bugs are.
- **Human baseline:** written by a person who has not seen the bug catalog (Dheeru's choice, 2026-10-08), because Dheeru saw the bug list in the plan. Recorded as a threat to validity.
- **"DID NOT RAISE" counts as an assertion** (2026-10-08, ADR-0004 amended).
- **G4 novelty:** candidates of the same generation run count against each other, in trust-score order (to be recorded in ADR-0004).
- **G5 budget:** 120 s median test runtime, excluding reset and connect.
- **Seeded bugs:** the 16 proposed bugs, with one change: SB13 shows the run's coins instead of distance on the game-over screen, because score equals distance at multiplier 1 and the swap would be invisible (`TrackManager.AddScore`).

### Phase 1 exit gate

| Item | State | Evidence |
|---|---|---|
| APK with hooks built; flags toggle verified via `Active()` and `PGFLAGS` | ✅ build 2 `87d396162a05` | [`bug_symptoms_87d396162a05.json`](evidence/phase1/bug_symptoms_87d396162a05.json) |
| Each seeded bug confirmed to show its symptom | ✅ 16/16 | [`game/HOOKS.md`](../game/HOOKS.md#verification) |
| G1 unit tests ≥ 25 adversarial cases; pg_core coverage ≥ 90% | ✅ 46 cases; 93% | `tests/unit/test_gate_static.py`; `pytest --cov=pg_core` |
| Prompt-leak test passes | ✅ | `tests/unit/test_prompt_leak.py` |
| ≥ 2 features generated and proven end to end; report from the DB with run IDs | ✅ runs 5 (store) and 6 (run_and_gameover) | [`report_runs_5_6.md`](results/phase1/report_runs_5_6.md), embedded in README |
| Human baseline suite exists and was run (clean + dev bugs) | ⏸ **deferred to Phase 4** (not passed): no human-written suite exists yet; it must be written by a person and run before any Phase 4 benchmark run | [ADR-0009](adr/0009-defer-human-baseline.md) |
| ≥ 1 ACCEPT; REJECT reasons working (e.g. a hallucinated call) | ✅ 1 accept (c13 kills SB02); rejections: `redundant` 1, `not_deterministic` 1, `unknown_sdk_member` 1 | report |
| PROGRESS.md updated with real numbers and known weaknesses | ✅ this section | — |

**Phase 1 closed on 2026-10-08 with the human baseline deferred (ADR-0009).** When it arrives: `pg baseline`, then `pg prove --run 5` and `--run 6` again (stored runs are reused; only the G4 decision is recomputed against the baseline), then `pg report --run 5 --run 6 --write --readme`.

### Known weaknesses (Phase 1)

- **Few ACCEPTs.** Most store candidates pass every gate but kill no *dev* bug: the power-up bugs they could catch (SB01, SB06) are in the holdout split, so they land in REVIEW. Their value can only show in the Phase 4 holdout measurement.
- **G1 does not catch weak assertions** such as `assert game.game_over.score_shown() >= 0` (c20): not a tautology, but it cannot fail on a wrong score. Such tests pass the gates and reach REVIEW, where a human must reject them.
- **No human baseline yet** (deferred, ADR-0009), so G4 novelty compared candidates only with each other in their run; some decisions may change to REJECT (redundant) once it exists.
- **One device, one game, synthetic bugs**; the person who wrote the specs also wrote the bugs, and Dheeru saw the bug list before the baseline was commissioned.
- **Environment fragility:** the adb reverse forward can vanish and AltTester Desktop stops its server when its licence check loses the internet (both now detected; see `docs/VERSIONS.md`).
- **Procedure changes between runs** (each recorded): the model switched from gemini-3.8-flash after 503s; G3 skips the second bug run once a kill is impossible (applied after run 5); "DID NOT RAISE" counts as an assertion. Run 1 (build 1, prompt v1, clean-only) was pipeline validation, not a result.

### Findings from the live SDK work (2026-10-07)

- **The store reloads the save file when it opens** (`ShopUI.Start` → `PlayerData.Create`). Unsaved in-memory changes vanish, so every `game.setup.*` helper saves after writing.
- **Selections are not saved when changed** (`LoadoutState.ChangeCharacter` and friends do not call `Save`): after a restart the clean build shows Trash Cat again. This contradicts **PERSIST-3**; any correct test of it fails G2. Raised with Dheeru.
- AltTester 2.3.2 sets a list element's field and then raises (`docs/VERSIONS.md`); setup verifies such writes by reading them back.
- `GAME_MODEL.md` corrected: each store tab has its own list, the buy button is `BuyButton`, mission progress is `Image/Reward/Text`.
- **The adb reverse forward can vanish** (USB reconnect or adb restart). Every launch then fails with `NoAppConnected` and is (correctly) classified infra. Seen on 2026-10-08 during the first `pg prove`; the plugin now recreates the forward before each launch and records when it did.

### Milestones

| Milestone | State | Evidence |
|---|---|---|
| M1.1 Game hooks | Done: hooked build 2 (`87d396162a05`) built 2026-10-08 and installed; all 16 bugs verified by `pg bugs verify`: clean passes, flag on fails (14 assertion, 1 `PGTimeout`, 1 game error), `Active()` and `PGFLAGS` show each flag | [`game/HOOKS.md`](../game/HOOKS.md#verification), [`bug_symptoms_87d396162a05.json`](evidence/phase1/bug_symptoms_87d396162a05.json) |
| M1.2 Bug catalog | Done: 16 bugs, seeded split 10 dev / 6 holdout, approved by Dheeru and frozen 2026-10-08 (sha256 `0d4edd7b05ba`, commit `b09f8a4`) | [`benchmark/bugs.yaml`](../benchmark/bugs.yaml), [`benchmark/HOLDOUT_FREEZE.md`](../benchmark/HOLDOUT_FREEZE.md) |
| M1.3 `pg_sdk` | Pages, locators, setup helpers, model readers, manifest (11 classes, 110 members) and pytest plugin written; live suite 6/6 on the clean build `e63240052d1b` with 0 game errors; fake-driver unit tests. Locators for the hooked build and the human baseline still to come | [`sdk_live_clean_e63240052d1b.json`](evidence/phase1/sdk_live_clean_e63240052d1b.json), `pg_sdk/manifest.json` |
| M1.5 Gates and decision | G1-G5, `decide`/`decide_batch`, trust score; 46 adversarial G1 cases; ADR-0004 | `pg_core/gates/`, `pg_core/trust.py`, [ADR-0004](adr/0004-trust-decision.md) |
| M1.6 Runner | Sandboxed subprocess runner, infra classification and retries, `pg run-test`; live: sample test 2/2 passed, hanging test killed at 40 s as infra | [`runner_live_checks.json`](evidence/phase1/runner_live_checks.json) |
| M1.7 Persistence | Postgres 16 in Docker, SQLAlchemy models, migration 0001, idempotent repo writes; integration tests 5/5 | `pg_db/`, `migrations/` |
| M1.4 Generator | Done: Gemini adapter, prompts v1 and v2 (v2 corrects the PGTimeout rule), budget, retries with backoff, prompt-leak test over every spec file | `pg_generator/`, `tests/unit/test_prompt_leak.py` |
| M1.8 CLI | `pg build register`, `generate`, `prove` (resumable; `--static-only`, `--clean-only`), `baseline`, `accept`/`reject`, `suite sync`, `report`, `bugs verify`. First live generation: run 1 (store, n=8) returned 8 candidates, all passing G1 | `pg_cli/pipeline.py`, `pg_cli/report.py`, `pg_core/report.py` |
| M1.9 Results | Generation runs [5, 6] (store, run_and_gameover; gemini-3.5-flash, prompt v2) on build 2: 16 candidates → **1 accept, 12 review, 3 reject** (`redundant` 1, `not_deterministic` 1, `unknown_sdk_member` 1); G2 14/15; device time 64.1 min over 149 attempts (3 infra); LLM cost $0.000000 (free tier). Accepted test synced to `suites/accepted/`. **Human baseline not run yet** | [`report_runs_5_6.md`](results/phase1/report_runs_5_6.md), [`.json`](results/phase1/report_runs_5_6.json) |

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
