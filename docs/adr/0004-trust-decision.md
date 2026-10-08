# ADR-0004: Trust decision for generated tests

- **Status:** Accepted (rules approved by Dheeru on 2026-10-07 with the Phase 1 plan)
- **Date:** 2026-10-07
- **Deciders:** Dheeru (owner)

## Context

An LLM writes candidate UI tests from the feature specs. A candidate may call APIs that do not exist, pass while checking nothing, fail at random, or duplicate tests we already have. Admitting it to the regression suite must rest on evidence from the game, not on anyone's opinion of the code, and the evidence must hold up when the benchmark later measures the suite on bugs the gates never saw (the holdout split, `benchmark/bugs.yaml`).

The gates run on one Android phone through AltTester's free plan (one driver connection, ADR-0002), so device time is the scarce resource.

## Decision

Five gates, each a pure function in `pg_core` returning `GateResult(passed, inconclusive, reasons[], metrics{})`, and one decision function, `pg_core.trust.decide`.

| Gate | Module | Rule |
|---|---|---|
| G1 static | `pg_core/gates/static.py` | Syntax tree only, never imported or run. One `test_*(game)` function with `@pytest.mark.spec(...)` naming existing spec IDs; imports limited to `pytest`, public `pg_sdk` names and `__future__`; no banned names or calls (`exec`, `eval`, `compile`, `open`, `__import__`, `getattr`/`setattr`/`delattr`, `globals`, `vars`, `breakpoint`, `input`, any `_`-prefixed name or attribute), no `while True`, `try`, `global`, nested `def`, `class`, `async`, `pytest.skip`/`xfail`; every attribute and call reached from `game` must resolve to `pg_sdk/manifest.json` with an argument list its signature accepts; at least one assertion, none a tautology (literal, `x == x`, `is not None` on a never-None SDK value), and at least one checking something the player sees; at most 80 lines and 40 SDK calls. |
| G2 determinism | `pg_core/gates/runs.py` | 3 of 3 valid runs pass on the clean build (no flags), each from a data reset. |
| G3 bug detection | `pg_core/gates/runs.py` | For each **dev** bug whose pages intersect the pages the test uses (derived from its syntax tree by G1): a **kill** is an assertion failure (including a failed `pytest.raises` expectation, "DID NOT RAISE", and `pytest.fail`), a `PGTimeout` or a logged game error in **2 of 2** runs with that bug's flag on. 1 of 2 is an *unstable kill* and does not count. |
| G4 novelty | `pg_core/gates/runs.py` | The candidate adds a dev kill or a spec ID not already covered by `suites/human_baseline/`, `suites/accepted/` and the candidates accepted earlier in the same run. |
| G5 cost | `pg_core/gates/runs.py` | Median runtime of the passing clean runs (test body only, without reset and connect) is at most **120 s**. |

**Decision** (`pg_core/trust.py`):

- **REJECT** if G1, G2 or G5 fails, or G4 finds the candidate redundant.
- **ACCEPT** if it kills at least one dev bug and passes every gate.
- **REVIEW** otherwise: it passes every gate but kills no dev bug. The catalog may simply not cover that area, so a human decides (`pg accept` / `pg reject` with a reason).
- **PENDING** (no decision) while a gate is inconclusive because infra failures left too few valid runs.

Every verdict stores machine-readable reasons (`Reason(code, message)`) per gate.

**Infra is never evidence.** Runs that end in `PGInfraError`, a lost connection or an app that never reached the main menu are classified INFRA by the runner, retried, and excluded from every gate. A test that crashes with its own error (`TypeError`, ...) is a failure on the clean build but never a kill.

**What counts as an assertion** (amended 2026-10-08, approved by Dheeru). A `pytest.raises(...)` block whose expected error never comes fails with pytest's own `Failed` exception, not `AssertionError`. It was first classified as a test error, which meant a correct "this must never appear" test could never kill the bug that makes it appear. It now counts as an assertion, as does `pytest.fail`. On the clean build nothing changes: any failure fails G2. Seen in generation run 1, where two candidates failed G2 this way (`docs/evidence/phase1/report_run1_clean_only_e63240052d1b.md`).

**trust_score** (0-100) is for ranking only and never changes a decision: 40 × min(kills, 2)/2 + 20 if G2 passed + 15 × min(visible assertions, 3)/3 + 15 × (1 − median runtime / budget) + 10 if no unstable kill.

**Novelty inside a run.** Candidates of one generation run are decided in trust-score order (ties by candidate id), and each ACCEPT joins the suite that later candidates are compared against. Approved by Dheeru on 2026-10-07; without it, eight near-identical siblings could all be accepted.

## Why kills must reproduce (2 of 2)

A single failing run on a bug build can be noise: a dropped tap, a slow frame, a random mission. Counting it would reward flaky tests, the opposite of what the gate is for. Requiring the same product-visible failure twice, on top of 3/3 clean passes, means the failure is caused by the bug. Unstable kills are recorded so the report can show them.

## Why the relevance filter exists

Running every candidate against every dev bug twice would cost about `2 × dev bugs` runs per candidate (20 runs at 10 dev bugs) on a single phone. G1 already knows which pages a test touches, and the catalog records where each bug shows (`pages` in `benchmark/bugs.yaml`). The filter only saves device time: every skipped bug is logged with its reason, it never turns a failure into a pass, and the Phase 4 holdout measurement runs **every** test against **every** holdout bug with no filter.

## Known risk: overfitting to dev bugs

Prompts and gate details are tuned while looking at dev-bug kills. A generator could learn to write tests that happen to catch exactly these ten bugs, and the gates would reward it. This is why the catalog has a holdout split (6 of 16 bugs, drawn by a seed fixed before the bugs existed, `pg_core.catalog.stratified_split`) that is never used for generation, prompts or thresholds, and is frozen by hash before any benchmark run (`benchmark/HOLDOUT_FREEZE.md`). The claim we can make is holdout detection, not dev detection.

## Alternatives considered

- **An LLM grades each test.** Cheap, but it is an opinion, not evidence, and it can be fooled by plausible-looking code. Rejected by design principle 2: seeded bugs are ground truth.
- **Accept anything that passes G1 and G2.** That is benchmark arm B (raw LLM): tests that run but may check nothing. Kept as a comparison arm, not a gate setting.
- **One failing run counts as a kill.** Faster, but rewards flaky tests (above).
- **Novelty only against the committed suites, as first written.** Lets siblings of one run all be accepted for the same kill; replaced by the in-run ordering above.

## Consequences

- A candidate's acceptance is reproducible from stored executions: the decision functions are pure.
- G1 is stricter than the plan's list in places (all `getattr`, any `try`, nested `def`): these never appear in a reasonable SDK test, and they defeat the manifest check.
- Tests that kill nothing land in REVIEW, so human time goes only where the bug catalog is silent.
- PERSIST-3 cannot pass G2 on the clean build: selections are not saved when changed (`docs/game/GAME_MODEL.md`). This is a game/spec mismatch, not a gate problem; Dheeru decides how to treat the spec.

## References

- `docs/BUILD_PLAN.md`, Phase 1, M1.5
- `pg_core/gates/static.py`, `pg_core/gates/runs.py`, `pg_core/trust.py`
- Tests: `tests/unit/test_gate_static.py` (G1, 45 adversarial samples), `tests/unit/test_gates_runs.py`
- ADR-0002 (free-plan constraints)
