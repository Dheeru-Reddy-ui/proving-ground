# Service levels (Phase 2)

Defined on 2026-10-08, **before** any measurement. Measured values come only from `pg slo report`, which names the validation IDs and probe files it used; this page links to those reports and never retypes their numbers (CLAUDE.md rule 2).

## Definitions and targets

| # | Service level | How it is measured | Target | Why this target |
|---|---|---|---|---|
| 1 | **Time to verdict** for a 2-feature validation (`store` and `run_and_gameover`, n = 8 each) | From the validation request (`validations.created_at`) to its verdict (`finished_at`); also reported from build registration (`builds.created_at`). Median and worst over the measured validations | Every validation within **120 min** | Phase 1 proved the same two features at n = 8 in 64.1 device minutes (`docs/results/phase1/report_runs_5_6.md`); the rest is generation, scoring, polling and cold starts. One phone runs one test at a time (ADR-0002), so device time dominates |
| 2 | **API availability** during the test window | `pg slo probe` calls `/readyz` every 30 s from the device host for the whole window (first validation requested to last verdict); a probe counts when it answers 200 within 30 s | **≥ 99%** of probes | `/readyz` needs the database and migrations at head, so it covers what the agent and dashboard need. During a validation the agent's polling keeps the free instance awake (ADR-0010), so cold starts should not fall inside the window |
| 3 | **Device-job success** excluding product failures | Over the INSTALL_BUILD and RUN_TEST jobs of the measured validations that finished: share that `succeeded`. A test that fails an assertion on a bug build is a success for the agent; a job that died (infra on every attempt, lost leases) is not | **≥ 95%** of device jobs | Phase 1 had 3 infra attempts in 149 (`report_runs_5_6.md`); job-level retries absorb most single infra attempts |

## Procedure

1. Deploy (`release.yml`), enrol the agent, start `pg agent run` on the PC with the phone attached and AltTester Desktop's server running.
2. In a second terminal, start the probe before the first validation:

   ```
   uv run pg slo probe --url https://<control-plane-url> --out docs/results/phase2/probes_<date>.jsonl
   ```

3. Start each validation (`uv run pg build validate --build <id> --feature store --feature run_and_gameover --n 8 --key slo-<k>`), one at a time, each after the previous verdict. At least one validation runs on a build registered through the webhook and one on a build registered through the CLI.
4. After the last verdict, stop the probe (Ctrl+C) and, where `DATABASE_URL` points at the production database:

   ```
   uv run pg slo report --validation <a> --validation <b> --validation <c> --probes docs/results/phase2/probes_<date>.jsonl --write
   ```

5. Link the written report below and record anything that went wrong during the window.

## Measured

Report: [`results/phase2/slo_v1_2_3_4.md`](results/phase2/slo_v1_2_3_4.md), written by `pg slo report` from validations 1, 2, 3 and 4 (every validation requested in the window) and the probe file [`results/phase2/probes_2026-10-09.jsonl`](results/phase2/probes_2026-10-09.jsonl) (2026-10-09 13:57 UTC to 2026-10-10 04:32 UTC). The numbers are in the report only.

| # | Service level | Against its target |
|---|---|---|
| 1 | Time to verdict | **Not met.** Validation 4, the only one that ran without interruption, met it. Validations 2 and 3 include pauses asked for by the operator, and validation 1 failed on a configuration error (below). Nothing was excluded |
| 2 | API availability | Met |
| 3 | Device-job success | Met |

What happened during the window (times UTC, from the agent log `artifacts/agent/agent-2026-10-09.log` and the job rows):

- **Validation 1** (build 2, key `slo-1`): both GENERATE jobs went dead with `Gemini API error 400: API key not valid`. The LLM key set on Render was wrong; the same key in the PC's `.env` worked. Dheeru replaced it. Status `failed`, with no verdict. The report counts it as finished because its status is final, so it pulls the median down; `pg slo report` does not yet tell a failed validation from a verdict.
- **Validation 2** (build 2, `slo-2`): the agent was stopped from 15:08 to 17:10 on 2026-10-09 so Dheeru could use the phone for a call. The test running at the stop (job 82) went back to the queue when its lease expired and passed on its second attempt.
- **Validation 3** (build 4, the rebuild registered through the webhook, `slo-3`): the agent was stopped after 18:38 on 2026-10-09 at Dheeru's request and started again at 03:16 on 2026-10-10. AltTester Desktop's server was off on restart, so the agent stayed unhealthy and claimed nothing until it was switched on (first claim 03:20).
- **Validation 4** (build 2, `slo-4`): no interruption. The agent switched the phone from build 4 back to build 2 through INSTALL_BUILD.
- **The agent changed during the window**, each change because of what the live runs showed: the health check stopped probing the app (aa475dc) and started restoring a dropped `adb reverse` forward (b32fb87) before validation 2; artifact uploads became concurrent (caa3b33) before validation 3, and large text artifacts were gzipped (0193a78) from the restart inside validation 3. Validation 4 ran with all of them.
- **Build registration to verdict** says little here: builds 2 and 4 were registered before the window and reused.
- **Failed probes**, all on 2026-10-09 between 14:14 and 14:18: three failed in 16-156 ms, too fast to have reached Render, in the same minute the phone's USB connection reset (a PC-side network drop is suspected, not confirmed); two failed while Render restarted after the LLM key was changed.
