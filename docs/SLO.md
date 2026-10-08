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

Not measured yet: it needs the deployed control plane and three real validations on the phone (Phase 2 exit gate). The report from step 4 will be linked here.
