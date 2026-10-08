# ADR-0006: Postgres is the job queue

- **Status:** Accepted (approved by Dheeru on 2026-10-08 with the Phase 2 plan)
- **Date:** 2026-10-08
- **Deciders:** Dheeru (owner)

## Context

Phase 2 turns the Phase 1 loop (`pg prove`, one process on the PC) into jobs that a cloud worker and the Windows device agent execute (ADR-0005). The work is small and slow:

- One phone runs one test at a time (ADR-0002). Phase 1's two-feature proving used 149 test attempts in 64.1 device minutes ([`report_runs_5_6.md`](../results/phase1/report_runs_5_6.md)), so even a busy day is far below 10,000 jobs.
- Every job result is also a domain write: an execution, candidates, kills, a decision. The next jobs depend on those writes (G3 runs only after G2 passes).
- We already run Postgres (Supabase, ADR-0010), and the free tiers we use have no managed broker we would want to add.

## Decision

A `jobs` table in the same Postgres as the domain tables, with leases.

**Columns:** `id, type, payload jsonb, requires jsonb, status, priority, attempts, max_attempts, lease_owner, lease_token, lease_expires_at, run_after, idempotency_key UNIQUE, validation_id, result jsonb, result_sha, last_error, created_at, updated_at, finished_at`.

**Types:** `INSTALL_BUILD`, `RUN_TEST`, `GENERATE`, `SCORE` (stage `static` or `final`), `CRAWL` (Phase 3 stub; nobody claims it yet).

**State machine** (rules are pure functions in `pg_core/jobs.py`; SQL in `pg_db/jobs.py`):

| From | Event | To |
|---|---|---|
| queued | claim | leased; `attempts += 1`; new `lease_token`; `lease_expires_at = now + ttl` |
| leased | heartbeat with the current token | leased; expiry extended |
| leased | complete with the current token | succeeded; `result` and `result_sha` stored |
| leased | retryable failure, or the lease expired | queued with `run_after = now + backoff` while `attempts < max_attempts`, else **dead** with `last_error` |
| leased | non-retryable failure (e.g. the agent's G1 re-check refused the code) | failed |
| leased | release (agent shutting down before it started the work) | queued; the attempt is not counted |
| dead | retry (admin) | queued; `attempts = 0` |

- **Claiming** is one statement: select the next `queued` job whose type the claimer handles, whose `requires` is contained in the claimer's capabilities (`requires <@ capabilities`) and whose `run_after` has passed, `ORDER BY` priority then id, `FOR UPDATE SKIP LOCKED LIMIT 1`, and update it to `leased`. Two claimers never get the same job, and neither waits for the other. A claimer holding `max_concurrency` leases gets nothing.
- **Time** comes from the server side only (the API and the worker), passed into every rule, so tests use a fake clock and the agent's clock never matters.
- **Enqueueing is idempotent:** `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`. Keys name the work, e.g. `run:prove-12:c40:SB03:r2`, so re-planning never duplicates a job.
- **Completion is idempotent:** completing an already succeeded job with the same `result_sha` is a no-op that returns success; a different result is rejected (409) and logged; a stale or wrong lease token is rejected (409). Exactly one result is ever stored.
- **Transactional enqueue:** a completion stores its domain rows (executions, candidates, kills, decisions), marks the job, and enqueues the jobs that become possible, in **one transaction**. There is no window where a result exists without its follow-up jobs, or the reverse.

**Orchestration.** A build validation is a DAG of jobs:

> INSTALL_BUILD → GENERATE (per feature) → SCORE static → RUN_TEST clean ×3 → RUN_TEST dev bugs ×2 each → SCORE final

`pg_core/orchestrator.py` is a pure function from a snapshot of the validation (jobs, executions, G1 reports, dev bugs) to the jobs to enqueue next and the validation's status. It reuses the Phase 1 gate rules, so the DAG makes the same runs `pg prove` makes:

- clean runs one repeat at a time while `determinism_needs_more` (a failure stops them);
- bug runs only after G2 passed, only for relevant **dev** bugs, and the second run is skipped once a kill is impossible (`detection_needs_more`, ADR-0004);
- a repeat that ended in infra is retried as the same job (above), not as an extra repeat; a dead job leaves its gate inconclusive and the candidate PENDING with that reason;
- SCORE final runs once every candidate of a generation run has nothing left to run.

`advance(validation)` takes a row lock on the validation, loads the snapshot, calls the planner and enqueues. It runs inside every completion, failure and lease expiry of that validation's jobs, so concurrent completions serialize per validation and the planner always sees committed state.

**Reaper.** The worker loop expires leases every few seconds (one `UPDATE ... WHERE status = 'leased' AND lease_expires_at < now`) and advances the affected validations.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Redis + Celery / RQ | Another service to host and pay for or babysit; enqueueing would no longer be atomic with the domain writes, so we would need an outbox anyway. |
| A managed queue (SQS, Cloud Tasks, Supabase Queues) | Same atomicity problem; ties the design to one host; leasing and visibility timeouts would mirror what we already get from one table. |
| No queue: the agent runs `pg prove` itself | Works on one PC but leaves no shared state for the dashboard, no retries across crashes, and nothing for the cloud worker to do. |
| Advisory locks instead of `SKIP LOCKED` | Locks tied to a session do not survive a pooled connection being reused; a lease column does. |

## When to switch

Move to a dedicated broker, keeping the same claim / heartbeat / complete API so the agent does not change, if any of these holds:

- sustained load above about 10,000 jobs a day, or many agents polling so often that idle polls dominate database load;
- claim latency p95 above 1 s in the M4.5 load test (500 jobs, fake agent);
- consumers that need fan-out or pub/sub rather than one-worker-per-job;
- the database's connection limit (the Supabase pooler's, on the plan in use) becomes the bottleneck.

## Consequences

- One system holds the domain data and the queue; a backup or a transaction covers both.
- Polling costs a query per idle agent per interval. Fine at one agent; it is the first thing to watch.
- Lease TTL is a trade-off: short means faster recovery from a dead agent, long means fewer heartbeats. Heartbeats run on their own thread in the agent, so a long test never loses its lease.
- The orchestrator is deterministic and unit-tested with a fake clock; the SQL is integration-tested against real Postgres (two concurrent claimers, lease expiry, duplicate completion).

## References

- `docs/BUILD_PLAN.md`, Phase 2 M2.1
- ADR-0004 (gate rules the orchestrator reuses), ADR-0005 (pulling agent), ADR-0010 (hosting)
- PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`: https://www.postgresql.org/docs/16/sql-select.html#SQL-FOR-UPDATE-SHARE
