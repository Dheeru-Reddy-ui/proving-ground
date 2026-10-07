# Proving Ground — project instructions

Proving Ground is a trust layer for AI-generated game tests. An LLM writes UI tests for a Unity mobile game, and a test is admitted to the regression suite only after it proves three things:
- it runs
- it is deterministic
- it catches seeded bugs

The system also classifies failures, repairs broken locators without weakening assertions, and triages bugs.

- Problem statement and sources: `docs/PROBLEM_STATEMENT.md`. Read it at the start of every phase.
- Build plan with every phase prompt: `docs/BUILD_PLAN.md`.
- Current status: `docs/PROGRESS.md`.
- Decisions: `docs/adr/`.
- Owner: Dheeru. He approves plans, writes the human baseline suite, and performs all Unity/device steps marked [DHEERU].

## Environment facts (do not assume otherwise)
- **Device host:** Dheeru's Windows PC. It runs AltTester Desktop (GUI) and adb, with an Android phone over USB (preferred) or an emulator.
- **AltTester free plan:** 1 app + 1 driver connection, GUI mode only, no CI batch mode.
  - Test execution is sequential on one device.
  - Close the AltTester Desktop inspector connection before automated runs.
- **Game:** TrashCat (Unity Endless Runner sample), instrumented locally with the AltTester Unity SDK.
  - The Unity project lives OUTSIDE this repo, at `PG_GAME_PROJECT_DIR` (from `.env`). Treat it as read-only except for the hook edits listed in `game/HOOKS.md`.
- **Versions:** pin AltTester SDK, Desktop and Python driver versions together and record them in `docs/VERSIONS.md`. Never upgrade one without the others.
- **Python:** 3.12 managed with `uv`. Postgres comes from `DATABASE_URL` (Supabase or local).
- **Sentinel** (Dheeru's telemetry/anomaly project) lives at `C:\Users\dheer\Sentinel_project`. Read its ingestion API before integrating; do not change its contract without asking.

## Repo layout
```
pg_core/        pure domain logic: models, gates, trust decision, classifiers (no I/O)
pg_sdk/         typed page-object SDK — the ONLY API generated tests may call
pg_sdk/locators/  locator maps (YAML), versioned per build
pg_generator/   prompt building, LLM client adapter, output parsing (prompts versioned)
pg_runner/      executes one test in a sandboxed subprocess; collects artifacts
pg_agent/       device agent (Phase 2+): claims jobs, runs pg_runner, uploads results
pg_api/         FastAPI control plane (Phase 2+)
pg_worker/      generation/scoring worker (Phase 2+)
pg_dashboard/   Jinja2 + HTMX UI (Phase 2+)
pg_cli/         `pg` command-line entry point
benchmark/      bugs.yaml (dev/holdout), experiment runner, report generator
specs/          feature specs (intended behaviour, IDs like STORE-3) — input to generation
suites/human_baseline/   written by Dheeru ONLY
suites/accepted/         tests accepted by the gates (generated or repaired)
game/           unity_scripts/ (our own C#: PGBugFlags, PGTelemetry, PGUiDrift), HOOKS.md, BUILD_TRASHCAT.md — never Asset Store code
migrations/     Alembic
tests/unit, tests/integration   tests of the platform itself
docs/           PROBLEM_STATEMENT, ARCHITECTURE, adr/, PROGRESS, RUNBOOK, BENCHMARK, SECURITY, VERSIONS
```

## Commands
- Install: `uv sync`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Types: `uv run mypy pg_core pg_sdk pg_generator pg_runner`
- Unit tests: `uv run pytest tests/unit -q`
- Integration tests (needs DB): `uv run pytest tests/integration -q`
- CLI: `uv run pg --help`
- Migrations: `uv run alembic upgrade head`

Run lint, types and unit tests before every commit.

## Non-negotiable rules
1. **No invented APIs.** Before using any AltTester, Unity, adb, Supabase, Render or LLM SDK call, confirm it exists. Either read the installed package source (find the `AltTester-Driver` package in the venv's site-packages and inspect signatures; Phase 0 records its import name in `docs/VERSIONS.md`) or the official docs. If you cannot confirm it, stop and say so.
2. **No invented numbers.** Every metric in README or docs is produced by `pg report` from stored run IDs. Never type a result by hand. Generated report files carry the run IDs they came from.
3. **Eval hygiene.**
   - `benchmark/bugs.yaml`, bug flag names, and anything derived from them must never appear in any LLM prompt.
   - The holdout split is never used for generation, prompt tuning or threshold tuning.
   - Specs are committed before the bug catalog; git history must show this.
4. **Human baseline is human.** Never create or edit files in `suites/human_baseline/`. You may run them.
5. **Generated code is untrusted.**
   - It runs only after the static gate passes.
   - It runs in a subprocess with a timeout, a scrubbed environment (no secrets) and a temp working dir.
   - Never `exec`/`eval` generated code in-process.
6. **Never weaken a gate, threshold or assertion to improve results** without an ADR and Dheeru's explicit approval. Report negative results honestly.
7. **Secrets** only via environment / `.env` (gitignored; keep `.env.example` current). Never log secrets or put them in prompts.
8. **Licensing.**
   - Never commit Unity Asset Store content, APKs, AltTester source, or code copied from AltTester example repos (they have no license file).
   - Use those repos only as references for object names and patterns.
   - Our own C# hook scripts are fine to commit.
9. **Ask before:**
   - deleting data
   - destructive migrations
   - changing the bug split after the holdout freeze
   - adding a paid service
   - editing files outside this repo other than the hook edits in `game/HOOKS.md`

## Engineering standards
- **Code:** typed Python everywhere. `mypy --strict` for pg_core, pg_sdk, pg_generator, pg_runner. Ruff for lint and format.
- **Structure:** pg_core is pure and deterministic: no network, DB, filesystem or clock calls (pass time in). Adapters live at the edges.
- **Data:** Pydantic v2 models at every boundary; pydantic-settings for config. SQLAlchemy 2.0 + Alembic, and every schema change is a migration.
- **Logging:** structlog JSON logs with correlation IDs (`build_id`, `run_id`, `candidate_id`, `job_id`).
- **Reliability:** any operation that can be retried is idempotent (idempotency keys or unique constraints). Bounded retries with exponential backoff and jitter. Explicit timeouts on every network and device call.
- **Coverage:** pg_core ≥ 90% line coverage. Every gate and classifier rule has unit tests, including adversarial cases.
- **Commits:** small, conventional ("feat:", "fix:", "test:", "docs:").
- **Phase close:** `docs/PROGRESS.md` updated with what works, what was verified (with evidence paths) and what is not done.

## How to work
1. At phase start: read this file, `docs/PROBLEM_STATEMENT.md`, `docs/PROGRESS.md` and the phase prompt. Propose a plan with milestones and risks, and wait for approval.
2. Work milestone by milestone. After each one, run checks, commit, and give a short status.
3. When reality differs from the prompt (tool behaves differently, API missing), stop and report. Don't improvise around it silently.
4. At phase end:
   - run the phase's exit-gate checklist and show evidence (command output, file paths, run IDs);
   - update PROGRESS.md;
   - list anything unverified.

## Definition of done (any task)
- Lint, types and tests pass.
- Behaviour is verified by running it, not just compiling.
- Docs are updated.
- No TODOs left without an issue reference.
- Nothing claimed that wasn't observed.
