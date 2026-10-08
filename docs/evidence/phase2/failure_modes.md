# Phase 2 failure modes (M2.7)

Each failure mode from the Phase 2 plan, the test or drill that exercises it, and its recorded result. Test results: [`failure_mode_tests.txt`](failure_mode_tests.txt) (2026-10-08, local Postgres 16).

| Failure mode | Expected | How it is exercised | Result |
|---|---|---|---|
| The agent is killed in the middle of a job | The lease expires, the job is retried, exactly one completion is recorded | `test_failure_modes.py::test_a_killed_agent_process_leaves_exactly_one_completion`: a real agent **process** claims a RUN_TEST, keeps its lease alive with heartbeats, and is killed while its test runs; after the 10 s lease TTL a second agent's claim reaps the lease and runs the job. Also in-process: `test_pipeline.py::test_an_agent_crash_mid_job_gives_exactly_one_completion` | Passed: the job is `succeeded` by the second agent at attempt 2 with one execution; the dead agent's lease is refused afterwards |
| The same webhook delivered twice | One build | `test_api.py::test_the_webhook_registers_one_build_and_rejects_forgeries`; the registration drill reposts the same delivery through the release workflow's script ([`registration_paths_drill.txt`](registration_paths_drill.txt)) | Passed: the repeat answers `duplicate_delivery: true` and the build count stays 1 |
| A forged or old signature | 401, logged | `test_failure_modes.py::test_forged_and_stale_webhooks_are_refused_and_logged` (wrong secret, timestamp one hour old) | Passed: both 401; `webhook_rejected` logged with `bad_signature` and `stale_timestamp` |
| The database becomes unavailable | `/readyz` fails, the API answers 503, both recover when the database returns | Drill [`ops/drills/db_outage.py`](../../../ops/drills/db_outage.py): a throwaway Postgres container is stopped and started under a running API | Passed ([`db_outage_drill.txt`](db_outage_drill.txt)): 503 `database_unavailable` while down, ready again on the first probe after the restart, no API restart. While down, each request waits for the 10 s connect timeout |
| An LLM 429/5xx storm | Backoff, budget respected, no duplicate candidates | `test_worker.py::test_an_llm_error_storm_gives_one_set_of_candidates` (two 503s, then an answer), `test_a_spent_build_budget_stops_generation`, `test_a_spent_daily_budget_waits_for_tomorrow`. The Gemini adapter's own retries are unit-tested in `tests/unit/test_generator.py` | Passed: one generation run, one set of candidates, every call traced (2 errors + 1 answer); a spent build budget fails the job, a spent day defers it to 00:00 UTC |
| The phone is unplugged during a run | INFRA, retries, then a dead job with a clear error on the dashboard | Simulated: `test_pipeline.py::test_a_run_that_is_infra_every_attempt_dies_and_leaves_the_candidate_pending`. **Live drill not done yet**: it needs the phone attached and someone to unplug it | Simulated run passed: each repeat dies after 3 attempts with `infra on every attempt (...) device offline`, and the candidate stays PENDING |

## Found while building these

- The agent-kill test found a real bug: the API's `/v1/jobs/{id}/complete` refreshed the job before answering, which discarded the unflushed completion of a RUN_TEST job that had no validation. Jobs inside validations were unaffected because advancing the validation flushed first. Fixed by flushing instead (`pg_api/routes/agents.py`); the test now guards it.

## What a live unplug drill should show (to run with the phone)

1. Start a validation, `pg agent run`, and unplug the USB cable during a RUN_TEST.
2. The runner classifies the attempts as infra and retries twice; the server fails the job (retryable) and requeues it.
3. The agent's full doctor fails (`adb device`); the System page shows it unhealthy and claims stop.
4. With the cable out for good, the job waits in the queue with its error (an unhealthy agent claims nothing), rather than going dead. Plug it back in: the agent turns healthy, claims the job and completes it.

This differs from the plan's wording ("then a dead job"): because the agent stops claiming while the phone is missing, an unplugged phone leaves the job queued with its error and the agent marked unhealthy, both on the System page. A job goes dead only when it fails on every attempt while the agent's checks pass (for example, the game crashing at every launch). Raised with Dheeru with the Phase 2 report.
