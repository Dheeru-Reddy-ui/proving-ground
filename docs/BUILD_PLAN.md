# Proving Ground — Build Plan (all phases)

A trust layer for AI-generated game tests, built for EA Hyderabad's *AI Software Engineer Intern* role (EA Studios – Quality Verification, Role ID 215929). The problem statement, with every fact tagged by source, is in the other file: `PROBLEM_STATEMENT.md`.

## How to use this file
1. Create an empty repo folder, e.g. `C:\Users\dheer\proving-ground`, with a `docs` folder inside it.
2. Put both files into `docs/`: `docs/BUILD_PLAN.md` (this file) and `docs/PROBLEM_STATEMENT.md`.
3. Open Claude Code in the repo folder and type: **"Read docs/BUILD_PLAN.md and start Phase 0."**
4. Phase 0 creates `CLAUDE.md` at the repo root from Part A. Claude Code reads a project-root `CLAUDE.md` at the start of every session ([docs](https://code.claude.com/docs/en/memory)), so the rules carry into every later phase.
5. In every phase, Claude Code proposes a plan and waits for your approval. Push back on anything vague before you approve.
6. You do the steps marked **[DHEERU]** yourself: Unity builds, installing the APK, writing the human baseline tests, the blind audit.
7. Don't start the next phase until every box in its **exit gate** is ticked with real evidence. Start each phase in a fresh session: "Read docs/BUILD_PLAN.md and start Phase N."

## Before you start
- Git, Python 3.12, [uv](https://docs.astral.sh/uv/)
- Android platform-tools (adb) and an Android phone with USB debugging (or an emulator whose ABI matches your build)
- Unity Hub, the Unity Editor version the [Endless Runner Sample Game](https://assetstore.unity.com/packages/essentials/tutorial-projects/endless-runner-sample-game-87901) supports, and the Android Build Support module
- [AltTester Desktop and Unity SDK](https://alttester.com/downloads/), with matching versions. Read their [pricing and T&C](https://alttester.com/pricing/) and confirm you're eligible for the free plan. The free plan is 1 connection and GUI mode only, which is why the design runs tests sequentially on your PC.
- Supabase (Postgres + Storage), Render (API + worker), an LLM API key, and optionally Langfuse. Check each one's current free-tier limits yourself; the plan deliberately doesn't assume them.

## Timeline
| Phase | Dates | Outcome |
|---|---|---|
| 0 | 7–8 Oct | Instrumented game, ≥19/20 smoke reliability, game model, specs committed |
| 1 | 8–11 Oct | First ACCEPT/REJECT decisions with a dev kill matrix and real numbers |
| — | **by 11 Oct** | **Apply to EA (Role 215929) with the repo live, even if later phases aren't done.** Send referral follow-ups with the link. |
| 2 | 12–18 Oct | Deployed platform with a public read-only demo |
| 3 | 19–26 Oct | Healing with zero auto-accepted false repairs, triage, Sentinel perf incidents |
| 4 | 27–31 Oct | Pre-registered benchmark, ablations, v1.0.0 |

If something slips, protect Phases 0–1 and the honesty of the numbers over later features. A smaller system you can fully defend in an interview beats a larger one you can't.

## Honest limits
- One game (a Unity sample, not an EA title), synthetic bugs and a single device. The docs say so, and so should you in interviews.
- The free AltTester plan means no headless CI device runs; ADR-0002 documents this.
- Talk about it as: **"I built a working trust layer for AI-generated game tests and measured what it catches and what it costs."** Never say you solved EA's problem.

---

# Part A — Project rules (becomes `CLAUDE.md`)

Claude Code: in Phase 0 (M0.1), save the content inside the fence below, exactly, as `CLAUDE.md` at the repo root.

````markdown
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
````

---

# Part B — Phases

Each phase below is a complete instruction set. Run one phase per session.

# Phase 0 — Foundation and feasibility spike

**Target dates:** 7–8 Oct 2026  
**Goal:** prove every risky environment piece works before building on it, and lay a repo foundation good enough to keep for the whole project.


You are starting Phase 0 of Proving Ground.

Read Part A of `docs/BUILD_PLAN.md` (the project rules, which become `CLAUDE.md`) and `docs/PROBLEM_STATEMENT.md` first. Then give me a plan covering milestones, the risks you see, and which steps need me ([DHEERU]). **Do not write code until I approve the plan.**

## Why this phase exists
The project depends on a fragile chain:

> Unity build → AltTester instrumentation → AltTester Desktop (free plan, GUI mode, 1 app + 1 driver) → adb → Python driver → our code

If any link is unreliable, every later number is meaningless. This phase measures how reliable the chain is before we trust it.

## Milestones

### M0.1 — Repository scaffold
- Create `CLAUDE.md` at the repo root by copying the fenced block in Part A of `docs/BUILD_PLAN.md` exactly (content inside the fence only). Commit it together with `docs/PROBLEM_STATEMENT.md` and `docs/BUILD_PLAN.md` before anything else.
- Create:
  - `pyproject.toml` (uv, Python 3.12)
  - ruff and mypy config (strict for pg_core, pg_sdk, pg_generator, pg_runner)
  - pytest config
  - pre-commit hooks for ruff and the mypy subset
- Create the package directories from the layout in CLAUDE.md, each with `__init__.py` and a one-line module docstring. Leave Phase 2+ packages as empty stubs.
- `.gitignore`: `.env`, `*.apk`, `artifacts/`, `.venv/`, Unity folders, anything under `game/` except `game/unity_scripts/`, `game/HOOKS.md` and `game/BUILD_TRASHCAT.md`.
- `.env.example` with these keys:
  - `PG_GAME_PROJECT_DIR`, `PG_APK_PATH`, `PG_ANDROID_PACKAGE`
  - `PG_ALTTESTER_HOST`, `PG_ALTTESTER_PORT`, `PG_ADB_SERIAL`
  - `DATABASE_URL`
  - `PG_LLM_PROVIDER`, `PG_LLM_MODEL`, `PG_LLM_API_KEY`
  - `PG_MAX_COST_PER_RUN_USD`
- GitHub Actions workflow `ci.yml`: lint, mypy, unit tests on push/PR, Ubuntu runner. No device tests in CI; the free plan has no batch mode.
- License decision: do **not** pick one yourself. Write `docs/adr/0003-license.md` laying out the options, given that AltTester's SDK is GPL-3.0 and the Python driver's PyPI page lists "Other/Proprietary". I will decide.
- `docs/PROGRESS.md`, `docs/VERSIONS.md`, `docs/adr/0000-template.md`.

### M0.2 — Build checklist for the instrumented game
Write `game/BUILD_TRASHCAT.md`, a checklist I will follow in Unity. Base it only on AltTester's official docs and their TrashCat walkthrough; cite the URL for each step. It must cover:
- getting the Endless Runner Sample Game from the Unity Asset Store and which Unity version it supports (state where you found this, or mark it UNKNOWN for me to check);
- installing the AltTester Unity SDK version that matches the AltTester Desktop version I install;
- the AltTester build settings needed for an instrumented Android build;
- the known issue that IL2CPP builds with Managed Stripping Level above Minimal may fail to connect;
- making sure the APK contains an ABI my target device supports (physical ARM64 phone recommended; emulator only if the ABI matches);
- recording the Android package name and launch activity;
- `adb reverse tcp:13000 tcp:13000` for USB connections, and how to verify it.

**[DHEERU]** I build and install the APK and fill `docs/VERSIONS.md`:
- Unity version
- AltTester SDK / Desktop / Python driver versions
- device model and Android version
- APK sha256
- package name

### M0.3 — `pg doctor`
A CLI command that checks the following, prints PASS/FAIL with a fix hint for each, and exits non-zero on any FAIL:
- Python version
- adb is installed, and exactly one device (or `PG_ADB_SERIAL`) is attached
- the package is installed on the device
- the reverse port forward exists
- the AltTester Desktop server is reachable on host:port
- the app connects and returns its current scene
- the installed driver version equals the one in `docs/VERSIONS.md`

### M0.4 — Connectivity and introspection spike (`pg spike ...`)
1. **Connect** and print the current scene and loaded scenes.
2. **Dump** every element in the current scene: name, id, path, enabled, components where available, screen position. Write it to `artifacts/spike/<build_sha>/scene_<name>.json`.
3. **Screenshot** to the same folder.
4. **Logs:** subscribe to AltTester log notifications, and capture `adb logcat` for the app's PID over the same window. Then trigger a known log. Verify that a `Debug.LogError` from the game appears in both. If the notification API differs from the docs, report it.
5. **Reset:** `adb shell pm clear <package>`, relaunch, wait for the first interactive screen. Verify the game is back at first-launch state; currency and unlocks must be reset. Measure the time and record it.
6. **Time scale:** verify that `SetTimeScale` speeds up a gameplay segment. Note any side effects.

Use only API calls you have confirmed in the installed driver or the official docs. Record the confirmed Python method names in `docs/VERSIONS.md` under "Confirmed driver API".

### M0.5 — Game model
Explore the game. Use the spike tools; I will help navigate if needed. Also read the game's C# scripts in `PG_GAME_PROJECT_DIR` (read-only) to see where state lives. Write `docs/game/GAME_MODEL.md` covering:
- every screen we will test (e.g. main/loadout, store tabs, missions, leaderboard, settings, run HUD, game over), with name, how to reach it, and key interactable objects with their AltTester paths;
- where each piece of state lives (soft and premium currency, owned items, selected character/theme, mission progress, settings) and how a test can **read** it. Prefer UI-visible values; give the model-level read path too. Reference the C# file and member;
- how state is persisted (e.g. save file under persistentDataPath, PlayerPrefs) and what `pm clear` resets;
- for each item, an evidence pointer to a dump file or a source file and line.

Mark anything you inferred rather than observed as **INFERRED**.

### M0.6 — Feature specs (before any bug catalog exists)
Draft `specs/<feature>.md` for: store, character_select, missions, settings, run_and_gameover, persistence.
- Each spec is a list of testable statements describing **intended behaviour**, with IDs (`STORE-1`, `STORE-2`, …).
- Write them the way a game designer would; they are generator input.
- Do not think about bugs here.

**[DHEERU]** reviews, edits and commits the specs. Commit them before Phase 1 starts. The commit order is part of our evaluation integrity.

### M0.7 — Reliability baseline
Write one smoke test **outside** `suites/` (it is infrastructure, not part of any suite): launch → main screen → open store → back. Run it 20 times from reset. Record in `docs/PROGRESS.md`:
- pass rate
- duration p50/p95
- every failure, with its cause

**If the pass rate is below 19/20, stop.** Diagnose and propose fixes before continuing; nothing later is valid on an unstable base.

### M0.8 — Architecture decision records
Use the template. Write:
- **ADR-0001:** target game and tooling (TrashCat + AltTester + Python), alternatives considered, and why.
- **ADR-0002:** free-plan constraints and their consequences (sequential execution, device host on Windows, no headless CI device runs, Pro trial as an option only for benchmark runs).
- **ADR-0003:** license (options only; I decide).

## Exit gate (show evidence for each)
- [ ] `uv run pg doctor` all PASS (paste output)
- [ ] Scene dumps and screenshots exist for every screen listed in GAME_MODEL.md
- [ ] Log capture verified in both channels (evidence file)
- [ ] Reset verified; time measured
- [ ] Smoke test passes ≥ 19/20, with numbers in PROGRESS.md
- [ ] Specs committed (show `git log --oneline -- specs/`)
- [ ] CI green on GitHub
- [ ] VERSIONS.md complete, including confirmed driver API names

## Stop and ask me if
- any AltTester behaviour differs from its docs;
- the free plan blocks something we need;
- the Unity build fails;
- you would need to modify game code in this phase (you should not).

## Report format at the end
1. What works, with evidence.
2. What doesn't, or isn't verified.
3. Measured numbers.
4. Risks for Phase 1.
5. Decisions I need to make.

---

# Phase 1 — Core trust loop (generate → prove → decide)

**Target dates:** 8–11 Oct 2026  
**Goal:** end to end on one machine. AI-generated tests for real game features are run through gates against a clean build and seeded-bug builds, decided ACCEPT / REVIEW / REJECT with reasons, stored in Postgres, and reported with real numbers.

> Prerequisite: Phase 0 exit gate passed and the specs are committed.

You are starting Phase 1 of Proving Ground.

Read `CLAUDE.md`, `docs/PROBLEM_STATEMENT.md`, `docs/PROGRESS.md`, `docs/VERSIONS.md`, `docs/game/GAME_MODEL.md` and `specs/`. Propose a plan with milestones and risks, and wait for approval.

## Design principles for this phase (do not deviate without an ADR)
1. **Generated tests can only call `pg_sdk`.** Hallucinated APIs are designed out by a static check against a manifest, not prompted away.
2. **Seeded bugs are ground truth.** A test proves its worth by failing on a real defect. An LLM's opinion of a test never decides acceptance.
3. **One build, many bug variants.** Bugs are toggled at runtime through AltTester, so there is no rebuild per mutant.
4. **The generator never sees the bug catalog.** Holdout bugs are never used for anything except final measurement.

## Milestones

### M1.1 — Game hooks (our own C#)
Write the following in `game/unity_scripts/`:
- **`PGBugFlags.cs`**
  - A static class holding a set of active flag IDs.
  - `public static void Configure(string csvFlagIds)` replaces the set (empty string clears it).
  - `public static bool On(string id)`.
  - `public static string Active()` returns the current set.
  - Mark methods so IL2CPP code stripping keeps them (e.g. Unity's `[Preserve]` attribute; confirm the right mechanism for our Unity version and cite it).
  - Log `PGFLAGS <csv>` via `Debug.Log` on every change.
- **`PGTelemetry.cs`**
  - A MonoBehaviour created at startup that keeps itself alive across scenes.
  - Every 5 s, logs one line: `PGTELEM {json}` with frame-time p50/p95/p99 over the window, managed memory, scene name and timestamp.
  - Must be cheap; no allocations per frame.
- **`game/HOOKS.md`** — a table of every hook point: bug ID, game file and method (name only; do not paste Asset Store code), what the hook changes, and the observable symptom.

**Seeded bugs:** 12–16 realistic defects across features. Pick from what GAME_MODEL.md shows exists. Each must be a plausible developer mistake, observable through the UI or game state, and must not crash the editor. Categories to cover:
- price charged wrong
- purchase not granted
- purchase allowed with insufficient funds
- currency or unlocks not persisted after restart
- settings not persisted
- mission reward not granted, or granted twice
- wrong value shown on game-over screen
- selection not applied in run
- soft-lock (a back or close button made non-interactable on one screen)
- one error-log bug (an exception thrown and logged on a specific action)

Each hook is `if (PGBugFlags.On("<id>")) { …defect… }` inserted at the right place.

**[DHEERU]** applies the hook edits in the local Unity project and rebuilds the APK once. Then update VERSIONS.md with the new sha256.

### M1.2 — Bug catalog (only after specs are committed)
- `benchmark/bugs.yaml`. Fields per bug:
  - `id`, `flag`, `category`, `feature`, `pages` (pg_sdk page names touched), `symptom`
  - `split: dev | holdout`
- Stratify the split: about 60% dev, 40% holdout, with every category represented in both where possible.
- Write a loader with validation: unique IDs, every flag exists in HOOKS.md, split ratio check.
- **Holdout freeze:** compute a sha256 of the holdout entries. Store it in `benchmark/HOLDOUT_FREEZE.md` with the commit hash. Every benchmark run must verify that hash.

### M1.3 — `pg_sdk`: the typed page-object SDK
- One class per screen from GAME_MODEL.md, reached through a root `Game` object passed in as a pytest fixture.
- Methods express player intent and return typed values, e.g. `store.characters.buy(name) -> PurchaseResult` and `wallet.soft_currency() -> int`.
  - No raw coordinates or raw AltTester objects leak out.
  - Every method has a docstring stating preconditions and what it returns.
- Locators live in `pg_sdk/locators/<build_tag>.yaml`, never hard-coded in Python. This is what makes healing tractable in Phase 3.
- Readers come in two flavours where both are possible, named explicitly: `*_shown()` reads the UI text, `*_state()` reads model state. Tests should assert on what the player sees, and may cross-check model state.
- Waiting:
  - every action waits for its post-condition object with a bounded timeout;
  - no fixed sleeps;
  - timeouts raise `PGTimeout` with screen context.
- Arrange helpers in `game.setup.*` (e.g. grant currency, unlock an item) may use model-level access. Document them as test setup only.
- **Manifest:** `pg_sdk/manifest.json` is generated from the code (introspect signatures and docstrings) and lists every public callable with params, return type and doc. A unit test fails if the manifest is stale.
- **pytest plugin** `pg_sdk.pytest_plugin`:
  - reads a run context from the env var `PG_RUN_CONTEXT` (path to JSON: build, flags, run_id, artifact dir);
  - resets the app via `pm clear` + launch;
  - waits for the main screen and connects the driver;
  - configures bug flags through `CallStaticMethod("PGBugFlags", "Configure", ...)` and verifies with `Active()`;
  - streams logs to the artifact dir, screenshots on failure, and disconnects cleanly;
  - at teardown, fails the test if the game logged an error or exception during it, attaching the log lines. Make this configurable, and record any error noise Phase 0 found on the clean build so it can be allow-listed explicitly, never silently.

  **Tests never know which flags are active.**
- `pg_sdk` itself gets unit tests with a fake driver, plus a small live suite under `tests/device/` (not run in CI).

### M1.4 — Generator (`pg_generator`)
- **Inputs:**
  - one spec file (or a subset of spec IDs);
  - `pg_sdk/manifest.json`;
  - a short game-model summary;
  - names and spec IDs of tests already in `suites/accepted/` (to avoid duplicates).
  - **Never** bugs.yaml, HOOKS.md, flag names or any benchmark output.
  - Add a unit test that builds every prompt and asserts that no bug ID or flag string appears in it.
- **Output:** strict JSON validated with Pydantic: `{tests: [{name, spec_ids: [..], intent, code}]}`. `code` is a single pytest function using only `pg_sdk`, marked `@pytest.mark.spec("STORE-2", ...)`.
- **Provider adapter:** a provider-agnostic interface with one implementation for the provider in `.env`. The model comes from `PG_LLM_MODEL`.
  - Record tokens in/out, latency and cost (rates come from config; never hard-code prices).
  - Retries with backoff on 429/5xx.
  - Enforce `PG_MAX_COST_PER_RUN_USD` and abort cleanly when exceeded.
- **Prompts** live in `pg_generator/prompts/*.md`, each with a version. Store the prompt version, model, temperature and a hash of the full rendered prompt with every candidate.
- **Variants:** support `--n` candidates per spec and `--seed`, where the provider supports seeds; if not, record that it doesn't.

### M1.5 — Gates and trust decision (`pg_core`, pure functions)
Put each gate in its own module, returning `GateResult(passed, reasons[], metrics{})`.

**G1 Static** (AST only; never import or execute the code):
- exactly one test function; allowed imports are `pytest` and `pg_sdk.*` only;
- banned:
  - `exec`, `eval`, `compile`, `open`, `__import__`, `getattr`/`setattr` with dunder names
  - `os`, `sys`, `subprocess`, `socket`, `importlib`, `time.sleep`, `while True`
  - any attribute starting with `_`
- every call on the `game` fixture resolves to a manifest entry with a compatible argument count;
- at least one assertion; no tautologies (`assert True`, `assert x == x`, asserting a literal, asserting only `is not None` on an SDK return that can never be None per the manifest);
- spec IDs exist in `specs/`;
- size limits (lines, number of actions).

Unit-test G1 with at least 25 adversarial samples.

**G2 Determinism:** 3 runs on the clean build (no flags) from reset. All 3 must pass.

**G3 Bug detection:**
- Run on each **dev** bug whose `pages` intersect the pages the test uses. Derive those pages from its AST; this filter exists only to save device time. Log every bug skipped and why.
- A **kill** means the test fails with an assertion failure, a `PGTimeout`, or a logged game error **in 2 of 2 runs** with that flag on. Infra errors (connection lost, device offline, app not launching) are never kills; they are retried, then recorded as INFRA.
- A kill in only 1 of 2 runs is an "unstable kill" and does not count.

**G4 Novelty:** compare the candidate's kill set and spec IDs against `suites/accepted/` plus `suites/human_baseline/`. A candidate adding no new kill and no new spec ID is redundant.

**G5 Cost:** runtime median ≤ the configured budget.

**Decision:**
- **REJECT** if G1, G2 or G5 fails, or if the test is redundant.
- **ACCEPT** if it kills ≥ 1 dev bug and passes all gates.
- **REVIEW** otherwise. This covers passing tests that kill no dev bug: the catalog may simply not cover that area, so a human decides.

Every decision stores machine-readable reasons. A `trust_score` (0–100) is computed for ranking only, from documented weights. It never changes the decision.

Write `docs/adr/0004-trust-decision.md` covering these rules, why kills must reproduce, why the relevance filter exists, and the known risk of overfitting to dev bugs (which is why the holdout split exists).

### M1.6 — Runner (`pg_runner`)
- Runs one test file against one run context in a **subprocess** with:
  - a temp working dir;
  - a scrubbed env (only PATH, the run context and what the driver needs; no API keys, no DATABASE_URL);
  - a wall-clock timeout that kills the process tree on expiry.
- Collects: exit status, a parsed JUnit XML outcome, failure type (assertion / PGTimeout / game error / infra), duration, screenshots, AltTester log notifications, a logcat slice for the app PID, and `PGTELEM` lines.
- Classifies infra failures by explicit rules (connection refused, device not found, app not running) and retries them up to 2 times with backoff. Every attempt is recorded.

### M1.7 — Persistence
Postgres via SQLAlchemy 2.0 + Alembic. Tables (add fields as needed, but don't drop these):
- `builds(id, apk_sha256 UNIQUE, label, locator_tag, created_at)`
- `specs(id, spec_id UNIQUE, feature, text, file_sha)`
- `generation_runs(id, build_id, feature, model, prompt_version, prompt_hash, temperature, seed, n_requested, tokens_in, tokens_out, cost_usd, started_at, finished_at, status)`
- `candidates(id, generation_run_id, name, code, code_sha UNIQUE per run, spec_ids[], pages_used[], decision, trust_score, reasons jsonb, created_at)`
- `executions(id, candidate_id NULL, suite_test_id NULL, build_id, flags[], purpose[clean|bug|benchmark], attempt, outcome, failure_kind, duration_ms, artifacts jsonb, started_at, finished_at)`
- `bugs(id, flag UNIQUE, category, feature, split, pages[])`
- `kills(candidate_or_test_id, bug_id, killed, unstable, evidence_execution_ids[])`
- `suite_tests(id, origin[human|generated|repaired], path, code_sha, accepted_from_candidate_id, status[active|quarantined], created_at)`

### M1.8 — CLI
- `pg build register --apk PATH --label TEXT`
- `pg generate --spec store --n 8 [--seed]`
- `pg prove --run <generation_run_id>` runs G1–G5 and decides
- `pg accept/reject <candidate_id> --reason` handles REVIEW items and records the human decision
- `pg suite sync` writes accepted tests to `suites/accepted/` with a header comment: candidate id, run id, model, prompt version
- `pg report --run <id> [--format md|json]`

### M1.9 — First real results
**[DHEERU]** writes 10–15 tests in `suites/human_baseline/` using pg_sdk, from the specs only.

Then run the following for at least two features (store plus one more):
- generate (n = 8 per spec file)
- prove

`pg report` must produce a Markdown report with:
- candidates by decision, and the main rejection reasons
- G2 pass rate
- the dev kill matrix (tests × dev bugs)
- cost per accepted test
- device time used

**Holdout bugs are not run in this phase.**

Then update the README: a short "What it does", the architecture sketch, and the generated report embedded by file include (not retyped).

## Exit gate (show evidence)
- [ ] APK with hooks built; flags toggle verified via `Active()` and `PGFLAGS` log lines
- [ ] Each seeded bug manually confirmed to show its symptom (log it in HOOKS.md with evidence)
- [ ] G1 unit tests ≥ 25 adversarial cases; pg_core coverage ≥ 90%
- [ ] Prompt-leak test passes (no bug ID or flag in any rendered prompt)
- [ ] At least 2 features generated and proven end to end; report produced from DB, with run IDs
- [ ] Human baseline suite exists and was run (clean + dev bugs) by the same machinery
- [ ] At least one ACCEPT, and evidence of REJECT reasons working (e.g. a hallucinated-call rejection)
- [ ] PROGRESS.md updated with real numbers and known weaknesses

## Stop and ask me if
- fewer than half the seeded bugs are observable through pg_sdk;
- G2 pass rates are low because of the environment rather than test quality;
- costs approach the budget;
- you are tempted to relax a gate to get an ACCEPT.

---

# Phase 2 — Production platform (control plane, device agent, dashboard)

**Target dates:** 12–18 Oct 2026  
**Goal:** turn the Phase 1 loop into a deployed service. A new build arrives, a cloud control plane schedules generation and validation, a device agent on the Windows PC executes runs, and a dashboard shows the verdict and review queue. Built with the reliability properties a real QA platform needs.

> Prerequisite: Phase 1 exit gate passed.

You are starting Phase 2 of Proving Ground.

Read `CLAUDE.md`, `docs/PROBLEM_STATEMENT.md`, `docs/PROGRESS.md` and the Phase 1 ADRs. Propose a plan with milestones, risks and the services you'll use, and wait for my approval.

**Do not create cloud resources until I approve.** Tell me which free or paid plans you intend to use, and tell me to check current limits myself; do not state pricing from memory.

## Target architecture
```
GitHub Actions ──HMAC webhook──►  pg_api (FastAPI, Render web service)
pg CLI (build register) ────────►     │
                                      ├── Postgres (Supabase): domain tables + jobs queue
                                      ├── Storage (Supabase Storage): APKs, artifacts
                                      └── pg_dashboard (served by pg_api)
pg_worker (Render background worker): GENERATE and SCORE jobs (LLM calls, gate decisions)
pg_agent (Windows device host, outbound HTTPS only): CLAIM → RUN_TEST/INSTALL_BUILD → upload → COMPLETE
          │ adb + AltTester Desktop (GUI) + phone
          └── PGTELEM metrics → Sentinel ingestion API (Phase 3)
```

**Why the agent pulls rather than being pushed to:** the device host is behind NAT on a home network. Pulling over HTTPS needs no inbound ports, and it is how real device-lab agents usually work. Write this up as ADR-0005.

**Why Postgres is the queue instead of Redis/Celery:**
- the scale is fewer than ~10k jobs a day;
- it gives transactional enqueue together with the domain writes;
- `SELECT … FOR UPDATE SKIP LOCKED` leasing removes a moving part.

Write this up as ADR-0006, with the conditions under which you would switch.

## Milestones

### M2.1 — Jobs and leasing (`pg_core` + persistence)
- Table `jobs(id, type, payload jsonb, status[queued|leased|succeeded|failed|dead], priority, attempts, max_attempts, lease_owner, lease_expires_at, idempotency_key UNIQUE, last_error, created_at, updated_at)`.
- Job types: `INSTALL_BUILD`, `RUN_TEST` (candidate or suite test × flags × purpose), `GENERATE`, `SCORE`, `CRAWL` (stub for Phase 3).
- Claim:
  - a transaction with `FOR UPDATE SKIP LOCKED`, filtered by job types the claimer supports and by device capabilities;
  - sets the lease with a TTL;
  - heartbeats extend the lease;
  - expired leases return to `queued`, with attempts incremented;
  - after `max_attempts` the job goes to `dead`, with the last error.
- Completion is idempotent: completing twice with the same result is a no-op, and a conflicting result is rejected and logged.
- **Orchestration:** a build's validation is a DAG of jobs.

  > GENERATE → SCORE-static → RUN_TEST(clean ×3) → RUN_TEST(dev bugs ×2 each) → SCORE-final

  A small orchestrator advances it on job completion. Keep it deterministic and unit-tested with a fake clock.

### M2.2 — Control plane API (`pg_api`)
**Endpoints** (versioned `/v1`, Pydantic schemas, OpenAPI published):
- `POST /v1/builds` — register a build: sha256, label, APK storage key, patch notes. Idempotent on sha256.
- `POST /v1/webhooks/github` — HMAC-SHA256 signature over the raw body with a shared secret, constant-time compare, timestamp header with ±5 min tolerance to block replays. Registers a build from a release.
- `POST /v1/builds/{id}/validate` — enqueue the DAG for chosen specs; returns the plan.
- `GET /v1/builds/{id}` — verdict and progress.
- `GET /v1/candidates/{id}` — code, gate results, kill matrix, executions, artifact links.
- `POST /v1/candidates/{id}/review` — approve or reject with a reason (admin only).
- Agent endpoints:
  - `POST /v1/agents/register` (one-time enrolment token)
  - `POST /v1/jobs/claim`
  - `POST /v1/jobs/{id}/heartbeat`
  - `POST /v1/jobs/{id}/complete`
  - `POST /v1/artifacts/upload-url` (or proxy upload; confirm what Supabase Storage supports and choose)
- `GET /healthz` (liveness) and `GET /readyz` (DB reachable, migrations at head).

**Auth:**
- Agents use per-agent bearer tokens, stored hashed, scoped to agent endpoints, and revocable.
- The admin dashboard uses session login with an argon2-hashed password from env, CSRF tokens on POST, and secure cookies.

**Public demo mode** (`PG_PUBLIC_DEMO=true`): read-only build and candidate pages without login, with no review actions and no artifact URLs that expose secrets. This is what recruiters will click.

**Other requirements:**
- Rate limiting on public and webhook routes.
- Request size limits.
- Structured error responses.
- Request IDs propagated into logs.

### M2.3 — Worker (`pg_worker`)
- Claims `GENERATE` and `SCORE` jobs, reusing `pg_generator` and `pg_core`.
- Enforces per-build and per-day LLM cost budgets from config.
- A kill switch: when `PG_GENERATION_ENABLED=false`, GENERATE jobs stay queued and the dashboard says why.
- Every LLM call is traced: prompt version, model, tokens, cost, latency, candidate IDs. Use Langfuse if I approve it in the plan; otherwise a `llm_calls` table. Never send secrets in traces.

### M2.4 — Device agent (`pg_agent`, runs on Windows)
- `pg agent enroll --token …` stores credentials in the user's config dir with restricted file permissions.
- `pg agent run`:
  - loop: claim, execute, heartbeat, complete;
  - graceful shutdown on Ctrl+C (finish or release the current lease);
  - exponential backoff with jitter on API errors, tolerating API cold starts;
  - health self-check via `pg doctor` before claiming. If unhealthy, it claims nothing and reports status.
- `INSTALL_BUILD`: download the APK, verify sha256, install, verify the package version, then switch the locator tag.
- `RUN_TEST`:
  - fetch the test code by sha;
  - re-run G1 locally as defence in depth;
  - execute through `pg_runner`;
  - upload artifacts and complete with the outcome.
- Serial execution only (free-plan constraint). The capability advertised to the server includes `max_concurrency: 1`.
- `docs/RUNBOOK.md`: how to start the agent at login on Windows, how to recover a stuck device, how to rotate the token.

### M2.5 — Dashboard (`pg_dashboard`, Jinja2 + HTMX, no SPA)
Pages:
- **Builds list:** status, verdict, counts, cost.
- **Build detail:** a DAG progress bar, accepted/review/rejected counts, the dev kill matrix heatmap, flake rate, and cost per accepted test.
- **Candidate detail:**
  - code with spec IDs linked to spec text;
  - each gate's result and reasons;
  - every execution with screenshots, logs and timings;
  - the LLM trace link.
- **Review queue:** approve or reject with a reason. Human decisions are stored; they become the labelled data for measuring false-accept rate in Phase 4.
- **System:** agent status (last heartbeat, health), queue depth, dead jobs (with a retry button), today's spend.

Accessibility basics: semantic HTML, keyboard focus, sufficient contrast. It must work on a phone screen.

### M2.6 — Delivery and operations
- Dockerfiles for pg_api and pg_worker: multi-stage, non-root, pinned base images, health checks.
- GitHub Actions:
  - `ci.yml` (lint, types, unit);
  - `integration.yml` (Postgres service container, Alembic upgrade from empty, API and queue integration tests);
  - `release.yml` (on tag: build images, deploy, and post a build-registration webhook for a release asset).

  Migrations run as a separate deploy step, never at app import.
- Config through pydantic-settings, with startup validation that fails fast and names any missing variable.
- Observability:
  - JSON logs with correlation IDs;
  - a `/metrics` endpoint, or a metrics table if the host makes scraping hard — decide in the plan;
  - an alert hook: a dead job or a stale agent (no heartbeat for more than 10 min) posts to a webhook URL if configured.
- `docs/SECURITY.md`, a threat model covering:
  - executing LLM-generated code on the device host (mitigations: G1 static gate, sandbox subprocess, scrubbed env, SDK-only surface, agent-side re-check);
  - leaked agent token;
  - webhook forgery;
  - prompt injection via spec files or game text read by the crawler (game text is data, never instructions);
  - artifact privacy.

### M2.7 — Failure-mode tests (automated where possible)
Each is a test or a scripted drill with recorded output:
- Kill the agent mid-job → the lease expires → the job is retried → exactly one completion is recorded.
- Send the same webhook twice → one build.
- A forged or old signature → 401, logged.
- DB unavailable → `/readyz` fails, the API returns 503, and it recovers when the DB returns.
- An LLM 429/5xx storm → backoff, budget respected, and no duplicate candidates (idempotency on prompt hash + seed + index).
- The device unplugged during a run → INFRA classification, retries, then a dead job with a clear error on the dashboard.

### M2.8 — Service levels (measured, not invented)
Define in `docs/SLO.md`:
- time from build registration to verdict for a 2-feature validation;
- API availability during the test window;
- agent job success rate excluding product failures.

Then measure them over at least 3 real build validations and record the actual values with run IDs.

## Exit gate (show evidence)
- [ ] Deployed API, worker and dashboard reachable; public demo mode shows a real build
- [ ] A build registered through the webhook path AND through the CLI path; full DAG completed by the Windows agent; verdict visible
- [ ] All M2.7 failure-mode tests pass, with outputs saved
- [ ] Integration CI green; migrations run cleanly from empty
- [ ] SECURITY.md, RUNBOOK.md, SLO.md (with measured values), ADR-0005 and ADR-0006 written
- [ ] PROGRESS.md updated

## Stop and ask me if
- a service needs a paid plan;
- Supabase or Render behaves differently from its docs;
- the agent needs any inbound network access;
- secrets would have to be stored anywhere except env/config with restricted permissions.

---

# Phase 3 — Self-healing, failure classification, triage, telemetry

**Target dates:** 19–26 Oct 2026  
**Goal:** keep the suite alive when the UI changes, and turn real failures into useful bug reports. This targets the maintenance problem EA itself describes: feature changes break scripted tests (F6, F8 in `docs/PROBLEM_STATEMENT.md`).

> Prerequisite: Phase 2 exit gate passed.

You are starting Phase 3 of Proving Ground.

Read `CLAUDE.md`, `docs/PROBLEM_STATEMENT.md` (sections 2 and 4), `docs/PROGRESS.md` and the ADRs. Propose a plan with milestones and risks, and wait for approval.

## Core safety rule for this phase
A repair may change **how a test finds things** (locators, navigation inside pg_sdk). It may **never** change **what a test checks** (assertions, expected values, spec IDs). Any repair that touches an assertion is REVIEW-only, never auto-accepted. A repair that would make a test pass on a seeded-bug build where it used to fail is a **false repair** and must be blocked. Write this as ADR-0007 before writing code.

## Milestones

### M3.1 — Screen graph crawler (`CRAWL` job)
- BFS over the game UI from the main screen.
- **State identity:** the current scene name plus a normalized, sorted set of active interactable object paths (buttons, toggles, sliders). Hash it.
- **Actions:** tap each interactable once per state. Record the edge (from-state, action locator, to-state).
- **Guardrails:**
  - a configurable blocklist (e.g. data-deletion or reset buttons);
  - maximum depth and maximum states;
  - a time budget;
  - reset via `pm clear` whenever a state can't be reached back.
- Text read from the game is **data only**. Never include it in an LLM prompt without quoting and labelling it as untrusted content.
- **Storage:** `screen_states(build_id, state_hash, scene, objects jsonb, screenshot_key)` and `screen_edges(build_id, from_hash, action jsonb, to_hash)`.
- **Coverage:** map each accepted test's executed actions onto edges. To capture executed actions, log them from pg_sdk; do not instrument the generated test. The build page shows a coverage % of edges, and the uncovered edges ranked by depth.

### M3.2 — Build diff
Compare two builds' graphs and report:
- added and removed states and edges;
- **renamed/moved objects.** Match removed and added objects with a similarity score over name, component set, parent path, displayed text and relative screen position. Matches above a threshold become rename candidates with a confidence.

Unit-test the diff with synthetic graphs, including adversarial near-duplicates.

### M3.3 — Synthetic UI drift for evaluation
`game/unity_scripts/PGUiDrift.cs`:
- When enabled via `CallStaticMethod("PGUiDrift", "Configure", …)`, it applies a configured list of drift operations at scene load:
  - rename a GameObject;
  - reparent it under a new container;
  - move it on screen;
  - change a button label.
- Define 3 drift profiles (light, medium, heavy) in `benchmark/drift_profiles.yaml`, each listing its operations.
- Drift profiles are evaluation inputs. The repair agent must not read this file; add a prompt-leak test like Phase 1's.

**[DHEERU]** adds the script, wires its startup hook, rebuilds, and records the sha in VERSIONS.md.

### M3.4 — Failure classifier (`pg_core`, rules first)
Given an execution failure plus context (build diff, rerun results, logs), output one of:
- `INFRA` — connection or device errors, app not launched (explicit error patterns)
- `FLAKY` — fails then passes on an identical rerun (rerun up to 2 times)
- `TEST_BROKEN_UI_DRIFT` — a PGTimeout or element-not-found on a locator that the build diff marks removed or renamed
- `PRODUCT_BUG_SUSPECTED` — an assertion failure or logged game error, with locators all resolving
- `UNKNOWN`

Rules decide first. For `UNKNOWN` only, an LLM may suggest a class with a rationale grounded in the evidence. Store it as a suggestion; it never triggers automatic repair or issue filing.

Evaluate on a labelled set built from real runs:
- clean runs;
- seeded-bug runs (ground truth: product bug);
- drift runs (ground truth: drift);
- unplug-device runs (ground truth: infra).

Report a confusion matrix through `pg report`.

### M3.5 — Repair agent
- Input: a `TEST_BROKEN_UI_DRIFT` failure, the old locator map, the build diff, and the SDK page involved.
- Output: a **locator-map patch only** (YAML diff in `pg_sdk/locators/<new_tag>.yaml`). The first strategy is deterministic: apply high-confidence rename matches from the diff. An LLM is used only to choose among candidate matches when the deterministic strategy is ambiguous, and it must pick from the listed candidates. Free-form locators are rejected.
- **Validation of every repair** (the "repair gauntlet"):
  1. every test that uses the patched locators passes 3/3 on the drifted clean build;
  2. on the drifted build with each dev bug that those tests killed before, they still kill it (2/2);
  3. a diff check confirms no file under `suites/` changed, and no assertion or expected value changed anywhere;
  4. the full accepted suite still passes on the non-drifted build with the old locator tag (no cross-contamination).

  Pass all four → auto-accept the new locator tag for that build. Fail any → REVIEW with the evidence.
- If a fix truly needs a change to a test file (e.g. a flow changed), create a REVIEW item with a proposed patch; never auto-apply it.
- Track repair outcomes:
  - **auto-repaired:** correct, or wrong but caught by the gauntlet;
  - **sent to review**;
  - **false repair:** would have hidden a bug; must be 0 auto-accepted.

### M3.6 — Triage and issue filing
- **Error signature:** normalize the failure (exception type, top N in-game frames with line numbers stripped, assertion message with numbers templated, screen state hash). Hash it.
- **Dedupe:** the same signature on the same build increments the count on the existing issue.
- **Issue filing** via the GitHub Issues API on a repo I configure (`PG_ISSUES_REPO`; token in env). Idempotent on the signature (label or marker in the body). Each issue contains:
  - title from the signature;
  - build, the test name and spec IDs;
  - repro steps rendered from the SDK action log;
  - expected vs actual;
  - screenshots and log excerpts (links to artifacts);
  - occurrences.
- Only `PRODUCT_BUG_SUSPECTED` failures that reproduce 2/2 get issues. Never file issues for INFRA, FLAKY or UNKNOWN.
- **Benchmark mode:** with a seeded bug on, issues go to a dry-run table, not GitHub, so we don't spam the repo. The dashboard shows them.

### M3.7 — Telemetry to Sentinel
1. Read the Sentinel repo at `C:\Users\dheer\Sentinel_project`: its ingestion API (auth, schema, endpoints) and its detector configuration. Summarize them in `docs/integrations/SENTINEL.md` before writing code.
2. The agent parses `PGTELEM` lines from each run and sends them to Sentinel's ingestion API, through an adapter that maps our fields to its schema, with batching, retries and idempotency keys.
3. Add 2 performance seeded bugs to the game hooks (e.g. a frame-time spike on a specific screen; steady managed-memory growth during a run). Mark them `category: performance` in bugs.yaml and split them across dev and holdout. Performance bugs are excluded from G3 kill computation, because UI tests are not expected to catch them; they are evaluated by whether Sentinel raises an incident for that build.
4. Configure Sentinel detectors for frame-time p95 and memory slope per build. Pull Sentinel incidents for a build into its Proving Ground verdict.

If Sentinel's API cannot support this without contract changes, stop and propose the minimal change.

## Exit gate (show evidence)
- [ ] Crawler builds a graph for the current build; coverage % shown on the build page
- [ ] Build diff detects every rename in the medium drift profile (show the list) at the chosen threshold
- [ ] Classifier confusion matrix produced from labelled real runs
- [ ] Repair results on all 3 drift profiles: auto-repaired, review, and **false repairs auto-accepted = 0**
- [ ] Triage files a real issue for one product failure in a non-benchmark run, and dedupes a repeat
- [ ] A performance seeded bug produces a Sentinel incident that appears in the build verdict
- [ ] ADR-0007 written; PROGRESS.md updated

## Stop and ask me if
- the repair gauntlet passes a repair you believe is wrong (that's a gate bug; fix the gate, not the repair);
- the crawler would need to press blocklisted buttons to reach a screen;
- Sentinel needs contract changes.

---

# Phase 4 — Benchmark, hardening and release

**Target dates:** 27–31 Oct 2026  
**Goal:** produce honest, reproducible evidence that the trust layer works, harden the system, and package it so a recruiter understands it in 60 seconds and an engineer can verify it in 30 minutes.

> Prerequisite: Phase 3 exit gate passed.

You are starting Phase 4 of Proving Ground.

Read `CLAUDE.md`, `docs/PROBLEM_STATEMENT.md`, `docs/PROGRESS.md` and all ADRs. Propose a plan with the experiment design, the device-time estimate per arm, and risks. Wait for approval.

**Integrity rules for this phase:**
- every number comes from stored run IDs;
- the holdout freeze hash is verified before any holdout run;
- negative results are reported, not hidden.

## Milestones

### M4.1 — Pre-registration
Write `docs/BENCHMARK.md` **before** running anything. It must contain:
- **Question:** does gating AI-generated tests with Proving Ground produce a suite that catches more unseen (holdout) bugs per unit of human effort, with fewer false accepts, than raw LLM output, and how does it compare with a human-written suite?
- **Arms:**
  - **A.** Human baseline (`suites/human_baseline/`).
  - **B.** Raw LLM: every candidate that runs (G1 + G2 only, no G3/G4).
  - **C.** Proving Ground: ACCEPT only.
  - **C+R.** Proving Ground: ACCEPT plus REVIEW items a human approved.
- **Generation:** same specs, model, prompt version and n for B and C. At least 3 independent generation runs (seeds or repeated sampling) to show variance.
- **Measurement:** the full kill matrix on **holdout** bugs. No relevance filter here; every test runs against every holdout bug, 2/2 for a kill.
- **Metrics:**
  - holdout detection rate
  - unique holdout kills
  - flake rate (10 clean reruns per accepted test)
  - false-accept rate: blind audit, as below
  - cost per accepted test
  - device minutes
  - suite size and runtime
- **Blind audit:** a random sample of at least 20 auto-accepted tests, plus 20 rejected and 20 REVIEW items, shuffled with labels hidden. Rated by Dheeru with a written rubric (does it test the spec it claims? is the assertion meaningful? would you keep it?). False-accept rate is the share of ACCEPT items rated "would not keep".
- **Healing:** the Phase 3 drift results rerun on the final suite.
- **Threats to validity:**
  - single game
  - synthetic bugs written by the same person who wrote specs
  - runtime flags rather than real regressions
  - single device
  - LLM nondeterminism
  - small sample sizes

Commit BENCHMARK.md and record the commit hash.

### M4.2 — Expand the catalog (before freeze only if not yet frozen)
If the catalog has fewer than 16 functional bugs plus 2 performance bugs:
- extend the dev split only;
- the existing holdout stays frozen.

If holdout is too small to be meaningful (fewer than 6), stop and ask. Extending holdout now requires a new freeze and a note in BENCHMARK.md.

### M4.3 — Run the experiment
- An orchestrated experiment through the platform (jobs, agent), not ad-hoc scripts, so the run is reproducible: `pg experiment run --config benchmark/experiment.yaml`.
- Checkpointing and resume. Device-time budget enforcement.
- Optional: if I start an AltTester Pro trial for 2 parallel connections, support it through config. Never assume it.
- `pg report --experiment <id>` generates `docs/results/RESULTS.md`, `docs/results/results.json` and charts (PNG/SVG) with:
  - the metrics table per arm, with ranges across generation runs;
  - the holdout kill matrix heatmap;
  - a cost and device-time breakdown;
  - the classifier confusion matrix (from Phase 3);
  - the repair outcomes table;
  - every number footnoted with experiment/run IDs.

### M4.4 — Ablations
Each ablation removes one gate in turn and reports what changes in holdout detection, false-accept rate and suite size. This shows each gate earns its place:
- no G1 (static)
- G2 1/1 instead of 3/3
- no G3 (bug detection)
- no G4 (novelty)

### M4.5 — Hardening
Run a code review pass over the whole repo and fix findings, prioritising:
- security (generated-code execution path, auth, webhook, secrets)
- correctness of gates and leasing
- error handling
- N+1 queries in the dashboard

Also:
- **Dependencies:** a vulnerability audit; pin versions.
- **Load test:** queue 500 synthetic RUN_TEST jobs with a fake agent and verify claim throughput, no double-claims, and dashboard responsiveness. Record the numbers.
- **Backup/restore drill:** restore the DB from backup into a scratch database and verify. Document it in RUNBOOK.md.
- **Data retention:** artifact lifecycle (delete raw videos/screens after N days, keep summaries). Add an admin command.

### M4.6 — Documentation and packaging
**README.md, top section** (first screen a recruiter sees):
- one-paragraph problem, linking to PROBLEM_STATEMENT.md and stating clearly which parts are EA's statements and which are our hypothesis;
- a 60-second summary of what it does;
- the headline results table, generated from results.json by a script — never retyped;
- the live demo link (public read-only mode) and the demo video link;
- the architecture diagram (Mermaid in `docs/ARCHITECTURE.md`, embedded image in README).

**README.md, rest:**
- how to run it locally in under 30 minutes (tested on a fresh clone by following it literally);
- limitations;
- licenses and third-party notices.

**Docs to finalise:**
- `docs/ARCHITECTURE.md`: components, data flow, job DAG, trust boundaries, and why each major choice was made (link to ADRs).
- ADRs 0000–0007+, all consistent with the final code.
- `docs/INTERVIEW_NOTES.md`, written from the real results:
  - the 2-minute story;
  - 5 hardest technical decisions and their trade-offs;
  - what failed and what you changed;
  - what you'd do with EA-scale resources;
  - how each part maps to the job posting's responsibilities and qualifications (cite F1–F4 by ID, no exaggeration).
- `docs/DEMO_SCRIPT.md`: a 3-minute video script: problem (20 s) → register build → live verdict → candidate detail with kill evidence → drift + repair → issue filed → results table.

### M4.7 — Release
- Tag `v1.0.0`, changelog, CI green, deployed demo updated, public demo shows the final experiment.
- A final pass of `docs/PROBLEM_STATEMENT.md`: re-check every source URL still resolves. Update "Last verified" or flag changes to me.

## Exit gate (show evidence)
- [ ] BENCHMARK.md committed before the experiment (show both commit times)
- [ ] Holdout freeze hash verified in the experiment log
- [ ] RESULTS.md generated from results.json, with run IDs, including negative findings
- [ ] Ablations reported
- [ ] Blind audit done by Dheeru; false-accept rate computed from the stored ratings
- [ ] Load test, backup/restore drill and security review fixes recorded
- [ ] README local setup verified on a fresh clone (paste the transcript summary)
- [ ] v1.0.0 tagged; demo live

## Stop and ask me if
- results look worse for arm C than B (report it and analyse why; never tune on holdout to fix it);
- device time will exceed the budget;
- a source in PROBLEM_STATEMENT.md has changed or disappeared.
