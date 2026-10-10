# Proving Ground

**A trust layer for AI-generated game tests.** An LLM writes UI tests for a Unity mobile game. A test enters the regression suite only after it proves three things on a real phone: it runs, it is deterministic, and it catches real (seeded) bugs. Tests that cannot prove it are rejected or sent to a human, with the reasons.

**Live demo:** https://proving-ground.onrender.com (read-only public mode; free hosting, so the first page can take about a minute to wake up)

**Status:** Phases 1 and 2 are complete (2026-10-10): the trust loop, and a deployed platform that runs it end to end against an Android phone. Next: self-healing tests when the UI changes (Phase 3), then a pre-registered benchmark (Phase 4). Every number this project reports is generated from stored run IDs by its own report commands, never typed by hand.

## In 60 seconds

- **The problem.** AI makes test scripts cheap to write, but a QA team cannot trust them without proof: a generated test can call functions that don't exist, flake, or pass while checking nothing. Sources: [docs/PROBLEM_STATEMENT.md](docs/PROBLEM_STATEMENT.md).
- **What Proving Ground does.** It gates every AI-written test through five checks: static analysis against a typed SDK, 3/3 clean runs, catching a seeded bug 2/2, novelty, and runtime cost. Each test ends ACCEPT, REVIEW or REJECT, with machine-readable reasons.
- **What is running.**
  - A new game build arrives by signed webhook or CLI.
  - A cloud control plane plans the validation, and an LLM worker writes the tests.
  - A device agent on a Windows PC runs them on a real Android phone.
  - A dashboard shows the verdict and the review queue.
- **What it found.** Most AI-written tests ran and were deterministic but caught none of the seeded bugs. The gates route those to a human instead of trusting them; see the [generated results](#results).

Things to click in the demo:
- [a build](https://proving-ground.onrender.com/builds/2): its candidates, gate results and kill matrix;
- [a validation](https://proving-ground.onrender.com/validations/8): the job DAG that ran on the phone;
- [an accepted test](https://proving-ground.onrender.com/candidates/27): its code, every gate's reasons and the seeded bug it caught.

## How a test earns trust

1. **Generate.** An LLM (Gemini, free tier) writes candidate pytest tests from the feature specs in [`specs/`](specs/). The prompt shows only the specs, a summary of the game and the SDK reference. The bug catalog never enters a prompt, and a unit test checks every rendered prompt for leaks.
2. **Prove.** Each candidate must pass five gates ([ADR-0004](docs/adr/0004-trust-decision.md)):
   - **G1 static:** parsed, never imported. It may call only the typed page-object SDK ([`pg_sdk`](pg_sdk/)); every call is checked against the SDK's generated manifest, and tautological assertions are refused.
   - **G2 determinism:** 3/3 passes on the clean build, each from a data reset.
   - **G3 bug detection:** fails 2/2 on at least one seeded dev bug, switched on at runtime in the same APK.
   - **G4 novelty:** adds a kill or a spec the suite does not already cover.
   - **G5 cost:** median runtime within budget.
3. **Decide.** ACCEPT, REVIEW (passes but kills no dev bug: a human decides) or REJECT, with reasons. A held-out set of seeded bugs is never used for anything but final measurement.

```
specs/*.md ──► generate (LLM) ──► candidates
                  │ prompt: specs + SDK manifest + game summary (no bug catalog)
                  ▼
prove ──► G1 static (AST vs pg_sdk/manifest.json)
      ──► pg_runner: sandboxed pytest subprocess ──► pg_sdk ──► AltTester ──► phone (TrashCat)
           clean ×3 (G2, G5), then each relevant dev bug ×2 (G3, flags via PGBugFlags)
      ──► G4 novelty vs accepted suite ──► ACCEPT / REVIEW / REJECT
report ──► Markdown/JSON from stored rows, naming its run IDs
```

## The platform (Phase 2)

```
GitHub release (build-*) ──HMAC-signed webhook──┐
pg CLI (register / validate) ───────────────────┤
                                                ▼
              pg_api on Render: FastAPI + dashboard (Jinja2/HTMX) + embedded worker
                ├── Postgres (Supabase): domain tables and the job queue
                ├── Storage (Supabase, private): APK parts and run artifacts, signed URLs
                └── worker: GENERATE (LLM) and SCORE (gates), with cost budgets
                                                ▲  outbound HTTPS only: claim, heartbeat, complete
Windows PC: pg_agent ── pg_runner (sandboxed pytest) ── pg_sdk ── AltTester ── Android phone
```

- **The queue is Postgres** ([ADR-0006](docs/adr/0006-postgres-job-queue.md)). Jobs are claimed with `FOR UPDATE SKIP LOCKED` leases and kept alive by heartbeats. Enqueue and completion are idempotent, retries back off, and a job that keeps failing ends as dead with its error. A validation is a job DAG: install → generate → static score → clean runs → seeded-bug runs → final score.
- **The device agent pulls work** ([ADR-0005](docs/adr/0005-agent-pulls-jobs.md)). It needs no inbound ports. It runs health checks before every claim, re-runs the static gate locally on the code it downloads, and installs builds from verified APK parts.
- **Generated code is untrusted** ([SECURITY.md](docs/SECURITY.md)). It is checked statically before it runs, then runs in a subprocess with a timeout, a scrubbed environment and a temporary directory.
- **Security basics.**
  - Signed webhooks with a replay window.
  - Hashed, revocable agent tokens.
  - An argon2-hashed admin password with CSRF protection.
  - Per-client rate limits.
  - A strict Content-Security-Policy.
  - A public demo mode that hides artifacts.
- **Operations.**
  - Structured JSON logs with request IDs.
  - `/metrics` and alerts for dead jobs and silent or unhealthy agents.
  - CI that runs migrations from empty and the integration tests against Postgres.
  - Failure drills: an agent killed mid-job, a database outage, a forged webhook, an LLM error storm, the phone unplugged mid-test ([outputs](docs/evidence/phase2/failure_modes.md)).

## Results

### Phase 2: the deployed platform

Measured on the live system with a real phone, against service levels defined before measuring ([docs/SLO.md](docs/SLO.md)):

- [`slo_v4_7_8.md`](docs/results/phase2/slo_v4_7_8.md): over the three validations that ran without interruption, time to verdict, API availability and device-job success all met their targets.
- [`slo_v1_2_3_4.md`](docs/results/phase2/slo_v1_2_3_4.md): the first window, kept as it is. Time to verdict missed its target there, because one validation failed on a configuration error and two include pauses the operator asked for.
- [`validations_summary.txt`](docs/evidence/phase2/validations_summary.txt): every validation on the control plane, with its decisions, phone time and LLM cost.
- [Phase 2 exit report](docs/results/phase2/exit_report.md): the exit gate with evidence, the problems the live runs found and their fixes, deviations from the plan, and what is not verified yet.

### Phase 1: the trust loop

The report below is generated by `pg report --run 5 --run 6 --readme` from the rows stored in Postgres; it is never edited by hand. It covers generation runs on one build of TrashCat with 10 dev seeded bugs (the 6 holdout bugs are not used until Phase 4).

**No human-written comparison suite yet.** The human baseline is deferred to Phase 4 ([ADR-0009](docs/adr/0009-defer-human-baseline.md)). Until it exists, novelty (G4) compares candidates only with each other, and these results say nothing about how AI tests compare with a person's.

<!-- phase1-report:start (generated by `pg report --readme`; do not edit) -->
## Proving Ground run report

Generated 2026-10-08 08:08 UTC by `pg report` from stored rows. Build `87d396162a05`.

| Generation run | Feature | Model | Prompt | Status | Requested | Prove run |
|---|---|---|---|---|---|---|
| 5 | store | gemini-3.5-flash | generate_tests_v2 (`9607c2de5ed2`) | succeeded | 8 | `prove-5` |
| 6 | run_and_gameover | gemini-3.5-flash | generate_tests_v2 (`3acf45bb0c51`) | succeeded | 8 | `prove-6` |

Human baseline runs used for novelty and the kill matrix: none.

### Candidates by decision

| Decision | Count |
|---|---|
| accept | 1 |
| review | 12 |
| reject | 3 |
| pending | 0 |
| review approved by a human | 0 |

### Main rejection reasons

| Reason | Rejections |
|---|---|
| `redundant` | 1 |
| `not_deterministic` | 1 |
| `unknown_sdk_member` | 1 |

### Determinism (G2)

Candidates that passed 3/3 clean runs: **14/15** of those that reached G2.

### Dev kill matrix

K = killed 2/2, u = failed 1/2 (unstable, not counted), . = survived, blank = not run (relevance filter).

| Test | SB02 | SB04 | SB05 | SB08 | SB09 | SB11 | SB13 | SB14 | SB15 | SB16 |
|---|---|---|---|---|---|---|---|---|---|---|
| c9 test_store_navigation_and_balances_main_menu | . | . | . | . | . |  |  |  |  | . |
| c10 test_store_navigation_game_over | . | . | . | . | . | . | . | . | . | . |
| c11 test_store_item_prices | . | . | . |  |  |  |  |  |  | . |
| c12 test_buy_power_up_inventory_and_balance | . | . | . |  |  |  |  |  |  | . |
| c13 test_buy_character_unlock_and_select | K | . | . |  |  |  |  |  |  | . |
| c14 test_buy_theme_unlock_and_select | . | . | . |  |  |  |  |  |  | . |
| c15 test_store_unaffordable_items_highlighting | . | . | . |  |  |  |  |  |  | . |
| c16 test_store_already_owned_items | . | . | . |  |  |  |  |  |  | . |
| c18 test_pause_and_resume |  |  |  | . | . |  |  | . |  |  |
| c19 test_pause_and_quit |  |  |  | . | . |  |  | . |  |  |
| c20 test_decline_continue_to_game_over |  |  |  | . | . |  | . | . |  |  |
| c21 test_continue_with_premium_success |  |  |  | . | . |  |  | . |  |  |
| c22 test_continue_with_premium_insufficient |  |  |  |  |  |  |  | . |  |  |
| c24 test_game_over_navigation | . | . | . |  |  |  | . | . |  | . |

### Cost and device time

| Measure | Value |
|---|---|
| LLM tokens in / out | 9742 / 14129 |
| LLM cost (configured rates) | $0.000000 |
| LLM cost per accepted test | $0.000000 |
| Device time, proving | 64.1 min |
| Device time per accepted test | 64.1 min |
| Device time, human baseline | 0.0 min |
| Executions (attempts) / infra attempts | 149 / 3 |

### Candidates

| Id | Name | Specs | Decision | Trust | First reason |
|---|---|---|---|---|---|
| 9 | `test_store_navigation_and_balances_main_menu` | STORE-1, STORE-2, STORE-3, STORE-11 | review | 59 | killed none of 6 relevant dev bug(s) |
| 10 | `test_store_navigation_game_over` | STORE-1, STORE-11 | review | 57 | killed none of 10 relevant dev bug(s) |
| 11 | `test_store_item_prices` | STORE-4 | review | 59 | killed none of 4 relevant dev bug(s) |
| 12 | `test_buy_power_up_inventory_and_balance` | STORE-5, STORE-6, STORE-10 | review | 59 | killed none of 4 relevant dev bug(s) |
| 13 | `test_buy_character_unlock_and_select` | STORE-5, STORE-7, STORE-10 | accept | 79 | kills SB02 |
| 14 | `test_buy_theme_unlock_and_select` | STORE-5, STORE-7, STORE-10 | reject | 59 | adds no new kill and no new spec ID over 1 suite test(s) |
| 15 | `test_store_unaffordable_items_highlighting` | STORE-8 | review | 59 | killed none of 4 relevant dev bug(s) |
| 16 | `test_store_already_owned_items` | STORE-9 | review | 59 | killed none of 4 relevant dev bug(s) |
| 17 | `test_run_start_and_hud` | RUN-1, RUN-2, RUN-4 | reject | 15 | failed 1 of 1 clean runs (assertion) |
| 18 | `test_pause_and_resume` | RUN-5 | review | 60 | killed none of 3 relevant dev bug(s) |
| 19 | `test_pause_and_quit` | RUN-6 | review | 60 | killed none of 3 relevant dev bug(s) |
| 20 | `test_decline_continue_to_game_over` | RUN-9, RUN-10 | review | 58 | killed none of 4 relevant dev bug(s) |
| 21 | `test_continue_with_premium_success` | RUN-7, RUN-8 | review | 58 | killed none of 3 relevant dev bug(s) |
| 22 | `test_continue_with_premium_insufficient` | RUN-7 | review | 52 | killed none of 1 relevant dev bug(s) |
| 23 | `test_coins_and_premium_added_to_balance` | RUN-11 | reject | 0 | `Run` has no member `premium_state` (not in the SDK manifest) |
| 24 | `test_game_over_navigation` | RUN-12 | review | 56 | killed none of 6 relevant dev bug(s) |
<!-- phase1-report:end -->

## What it does not show yet

- **Detection of unseen bugs.** The held-out seeded bugs have not been run; that is Phase 4's pre-registered benchmark.
- **AI versus human tests.** A human-written baseline suite comes in Phase 4.
- **Self-healing.** Classifying failures after UI changes and repairing locators without weakening assertions is Phase 3.

## Roadmap

- **Phase 3:**
  - a screen-graph crawler and build diff;
  - a failure classifier (infra, flaky, UI drift, product bug);
  - a locator repair agent whose every repair must pass a "gauntlet" proving it still catches the same bugs;
  - issue triage;
  - performance telemetry to [Sentinel](https://github.com/Dheeru-Reddy-ui/Sentinel), my real-time telemetry and anomaly-detection project.
- **Phase 4:**
  - a pre-registered benchmark comparing raw LLM output, gated output and a human suite on held-out bugs;
  - hardening and a release.

## Documentation

- Problem statement, with a source for every fact: [docs/PROBLEM_STATEMENT.md](docs/PROBLEM_STATEMENT.md)
- Build plan: [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md)
- Progress, with evidence for every milestone: [docs/PROGRESS.md](docs/PROGRESS.md)
- Decisions: [docs/adr/](docs/adr/)
- Operating it: [docs/RUNBOOK.md](docs/RUNBOOK.md), [docs/SECURITY.md](docs/SECURITY.md), [docs/SLO.md](docs/SLO.md)

## Scope and limits

- One game: TrashCat, Unity's Endless Runner sample, driven through [AltTester](https://alttester.com/). It is a stand-in, not an EA title.
- Seeded bugs are synthetic, and tests run on a single Android device.
- Hosting is on free tiers (Render, Supabase); the demo sleeps when idle.
- Running the device side needs your own AltTester subscription. The AltTester Python driver is installed from PyPI under AltTester's license and is not part of this repository.
- Not affiliated with Electronic Arts, Unity or AltTester.

## Development

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pg --help
uv run pytest tests/unit -q
```

## License

[MIT](LICENSE), for this repository's own code and docs (see [ADR-0003](docs/adr/0003-license.md)). The AltTester driver and SDK and Unity's Endless Runner sample are not part of this repository and keep their own licenses.
