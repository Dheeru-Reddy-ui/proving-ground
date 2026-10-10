# Phase 2 exit report: production platform

Written 2026-10-10 by Claude; **signed off by Dheeru on 2026-10-10**. Phase 2 started 2026-10-08 (plan approved the same day). Every number below is quoted from a linked evidence file or generated report; none is typed from memory.

**Deployed:** https://proving-ground.onrender.com, Render deploy `dep-db4ubdijnfac738ipnq0` (commit `1bd1c4a`, live 2026-10-10 07:28 UTC), Supabase Free (Mumbai session pooler). Device agent `dheeru-pc` on the Windows PC with the SM-S948B over USB.

## Exit gate

| Item | State | Evidence |
|---|---|---|
| Deployed API, worker and dashboard reachable; public demo mode shows a real build | ✅ | `/readyz` ready (database, migrations, worker) after the last deploy. Without logging in, `/`, `/builds/2`, `/builds/4`, `/validations/8` and `/candidates/27` answer 200; the public candidate page has no artifact links and no review form, and an artifact URL answers 401 (checked 2026-10-10 07:30 UTC) |
| A build registered through the webhook path and through the CLI path; full DAG completed by the Windows agent; verdict visible | ✅ | Build 2 `source=cli`, build 4 `source=webhook` (release run [37942986479](https://github.com/Dheeru-Reddy-ui/proving-ground/actions/runs/37942986479)); validations 2, 3, 4, 7 and 8 ran the whole DAG on the phone and succeeded, verdicts on `/validations/<id>`: [`validations_summary.txt`](../../evidence/phase2/validations_summary.txt) |
| All M2.7 failure-mode tests pass, with outputs saved | ✅ | [`failure_modes.md`](../../evidence/phase2/failure_modes.md): agent killed mid-job, duplicate webhook, forged or stale signature, database outage ([`db_outage_drill.txt`](../../evidence/phase2/db_outage_drill.txt)), LLM 503 storm, and the live phone-unplug drill ([`unplug_drill.txt`](../../evidence/phase2/unplug_drill.txt)) |
| Integration CI green; migrations run cleanly from empty | ✅ | Latest push `1bd1c4a`: [integration 38033711583](https://github.com/Dheeru-Reddy-ui/proving-ground/actions/runs/38033711583) (migrations 0001→0005 from empty, down to base, up again; 56 integration tests passed; cloud image smoke test) and `ci` [38033711544](https://github.com/Dheeru-Reddy-ui/proving-ground/actions/runs/38033711544) green. Locally on 2026-10-10: ruff clean, mypy strict clean, 647 unit tests passed, `pg_core` line coverage 95% |
| SECURITY.md, RUNBOOK.md, SLO.md (with measured values), ADR-0005 and ADR-0006 written | ✅ | [`SECURITY.md`](../../SECURITY.md), [`RUNBOOK.md`](../../RUNBOOK.md) (agent at login, stuck device, token rotation), [`SLO.md`](../../SLO.md) linking two generated reports, [ADR-0005](../../adr/0005-agent-pulls-jobs.md), [ADR-0006](../../adr/0006-postgres-job-queue.md), plus [ADR-0010](../../adr/0010-free-tier-hosting.md) |
| PROGRESS.md updated | ✅ | Phase 2 section, milestones M2.1-M2.8 and this exit gate |

## Service levels

Two generated reports, both kept:

- [`slo_v1_2_3_4.md`](slo_v1_2_3_4.md), validations 1-4: availability and device-job success met their targets; **time to verdict did not**. Validation 1 failed in its first minute on a wrong LLM key on Render, and validations 2 and 3 include pauses the operator asked for (a phone call; overnight). Nothing was excluded.
- [`slo_v4_7_8.md`](slo_v4_7_8.md), the three validations that ran without interruption: **all three targets met**.

What happened in each window is in [`SLO.md`](../../SLO.md).

## What the live system found, and the fixes

Each was found by running the deployed system with the phone, fixed, tested and pushed.

| Found | Fix |
|---|---|
| The database password had an unencoded `@`; the first connection diagnostics logged four of its characters to Render | Redaction of any token containing `@` (c1191c5); incident in SECURITY.md; password replaced |
| The agent's health check needed the game running, so an idle agent stayed unhealthy | Health check no longer probes the app (aa475dc, ADR-0005) |
| A USB reconnect drops the `adb reverse` forward and left the agent unhealthy | The agent restores it before each check (b32fb87) |
| The release workflow wanted an asset named exactly `build.json` | Any one `build*.json` (53ded9c) |
| Rate limits keyed on Render's proxy address | Measured three `X-Forwarded-For` entries, `PG_TRUSTED_PROXY_HOPS=3` ([`proxy_hops.txt`](../../evidence/phase2/proxy_hops.txt)) |
| Uploading a run's artifacts one file at a time took most of the gap between runs (logcat alone was up to about 2 MB) | Four at a time (caa3b33), text over 64 KiB gzipped (0193a78; logcat 17-24x smaller, as measured in the commit message) |
| The SLO report counted a validation without a verdict as a fast finish | Counted as a miss; availability limited to the test window (380558e) |
| An unplugged phone leaves jobs queued with no alert | `unhealthy_agent` alert after 10 minutes with device jobs waiting (9edbc81) |

## Deviations from the plan

All were raised and approved, except where noted.

- **Worker inside the web service** rather than a Render background worker, which has no free tier (ADR-0010, approved with the plan).
- **`llm_calls` table** for LLM tracing, not Langfuse (approved with the plan).
- **One Docker image** for the API and the worker; the worker runs as `python -m pg_worker` or embedded.
- **Unplugged phone ends with a queued job, not a dead one.** The unhealthy agent stops claiming, so no attempts are spent while the phone is away and the job resumes when it returns. Accepted by Dheeru on 2026-10-10 (ADR-0005), with the alert above.
- **Time to verdict is measured from the validation request.** The plan asks for "build registration to verdict". The reports show that figure too, but it is not meaningful here: builds 2 and 4 were registered well before their validations and reused. See "Not verified" below.

## Not verified or still open

- **Registration to verdict on a fresh build** is not measured as the plan words it. Measuring it needs a new build registered and validated straight away.
- **The `unhealthy_agent` alert** is deployed and covered by integration tests, but has not been seen firing on the live system, which would need the phone unplugged for over 10 minutes.
- **Alerts are recorded but not delivered.** `PG_ALERT_WEBHOOK_URL` is not set on Render, so dead-job and agent alerts appear only on the System page.
- **Graceful Ctrl+C of the agent** is unit-tested only. Every live stop in this phase was a hard stop, which exercised the lease-expiry path instead; it worked each time.
- **Losing the phone mid-test** takes about 2.5 minutes to detect, because the runner waits out the AltTester command timeout ([`unplug_drill.txt`](../../evidence/phase2/unplug_drill.txt)). This is bounded, not a hang.
- **The last Render deploy** took about 15 minutes to go live after a 30-second build, with no failure event and no incident on Render's status page; the old version served throughout. The cause is unknown.
- **Known gaps from SECURITY.md** still stand: in-memory rate limiter (one instance), test runner not isolated from Dheeru's Windows account, AltTester Desktop's unencrypted server on the PC.
- **Review queue:** every `review` decision in [`validations_summary.txt`](../../evidence/phase2/validations_summary.txt) waits on `/review`. These human decisions become Phase 4's labelled data.
- **Carried over from Phase 1:** the human baseline suite is deferred to Phase 4 (ADR-0009).
- **Upcoming:** Render maintenance on 2026-10-14 01:00-02:00 UTC; deploys are unavailable during it and running services are unaffected.

## For Dheeru

1. ~~Sign off the Phase 2 exit gate~~: signed off 2026-10-10.
2. Optional: set `PG_ALERT_WEBHOOK_URL` on Render to a Discord or Slack incoming-webhook URL so alerts reach you.
3. Optional: go through the review queue.
4. Optional: measure registration to verdict on a fresh build, at your next game build.
