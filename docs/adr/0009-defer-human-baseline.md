# ADR-0009: Defer the human baseline to Phase 4

- **Status:** Accepted
- **Date:** 2026-10-08
- **Deciders:** Dheeru (owner)

## Context

Phase 1's exit gate asks for a human baseline suite (`suites/human_baseline/`, 10–15 tests written from the specs) that has been run through the same machinery as the generated tests. It serves two purposes:

1. In Phase 1, G4 (novelty) compares each candidate against the suite, so a candidate that only repeats what the human tests already catch is rejected as redundant.
2. In Phase 4, it is benchmark arm A, the comparison that tells whether gated AI tests do better or worse than a person's.

The baseline must be written by a person, and ideally by one who has never seen the seeded-bug catalog (CLAUDE.md rule 4). Dheeru saw the bug list in the Phase 1 plan, so he asked a friend who has not seen it to write the suite. On 2026-10-08 the folder was still empty, and Claude cannot write it: Claude designed every seeded bug, so its tests would be aimed at them and the "human" arm would be meaningless.

## Decision

Phase 1 closes without the human baseline. The exit-gate item is marked **deferred (ADR-0009)**, not passed.

- The Phase 1 results stand as reported: G4 compared candidates with the accepted suite and with the candidates accepted earlier in the same run, and the report states "Human baseline runs used: none".
- The baseline must exist and be run (`pg baseline`) **before any Phase 4 benchmark run**. Phase 4's pre-registration (BENCHMARK.md) depends on it.
- When it arrives, generation runs 5 and 6 are re-decided against it (`pg prove --run 5`, `--run 6` reuse every stored execution and only recompute G4), and the Phase 1 report is regenerated. Any decision that changes is visible in the regenerated report.
- The author should be someone who has not seen `benchmark/bugs.yaml`, `game/HOOKS.md`, `tests/device/test_seeded_bug_symptoms.py` or `artifacts/`. If Dheeru writes it, the benchmark records that he had seen the bug list.

## Alternatives considered

- **Claude writes the baseline.** Rejected: it would be fabricated evidence. Claude knows every seeded bug, so arm A would overstate what a human suite catches, and the comparison would be rigged.
- **Keep Phase 1 open until the baseline exists.** Honest, but blocks Phase 2 on an outside person's schedule while nothing in Phase 2 depends on the baseline.
- **Dheeru writes it now.** Possible, but he saw the bug list; the friend's suite is the cleaner comparison, so this stays a fallback.

## Consequences

- Phase 2 can start now. The Phase 1 numbers in the README are unchanged and still generated from stored runs.
- G4's novelty check in Phase 1 was weaker than designed: some REVIEW or ACCEPT decisions might become REJECT (redundant) once the baseline exists. This is stated in PROGRESS.md as a known weakness.
- Phase 4 gains a hard prerequisite: the human baseline, run on the benchmark build, before BENCHMARK.md is committed.

## References

- `docs/BUILD_PLAN.md`, Phase 1 exit gate and Phase 4 M4.1 (arm A)
- `docs/BASELINE_GUIDE.md`, `docs/SDK_REFERENCE.md`
- `docs/PROGRESS.md`, Phase 1 exit gate
- ADR-0004 (G4 novelty)
