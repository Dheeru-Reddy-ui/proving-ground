# ADR-0002: Working within AltTester's free-plan limits

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Dheeru (owner)

## Context

- **AltTester Lite (free)** allows one connection in GUI mode only, for users under the revenue threshold. It is granted on request after ALTOM reviews it (`PROBLEM_STATEMENT.md` V3). AltTester Desktop's docs describe the limit as "a maximum of 1 instrumented app and 1 driver" connected concurrently.
- **AltTester Pro** adds CI/CD batch mode and 2 parallel connections. It has a 30-day free trial (V3).
- **Our state on 2026-10-07:**
  - Lite approval would not arrive in time for Phase 0, so Dheeru activated the **Pro trial** that day. It ends around **2026-11-06**.
  - **Lite has been requested.**
  - The Python driver's license allows use only with a valid subscription (ADR-0003).
- AltTester Desktop must be running whenever tests run (V4). On this PC its built-in server listens on port 13000, unencrypted (`Security: Disabled (WS)` in its log).

## Decision

1. **Design for the Lite limits even while the Pro trial is active.**
   - One driver connection at a time and one device.
   - All device work runs **sequentially**: smoke runs, gates G2/G3, benchmark runs and crawls.
   - Every tool releases its driver connection when it finishes or fails. `pg doctor` already does, and reports "slot busy" as its own failure.
   - In Phase 2 the device agent advertises `max_concurrency: 1`.
2. **The device host is the Windows PC.** AltTester Desktop runs in GUI mode there, and the phone is attached over USB with `adb reverse tcp:13000 tcp:13000`.
3. **No device runs in CI.** GitHub Actions runs lint, type checks and unit tests only. Device tests are marked `device` and run on the PC.
4. **Pro-only features are opt-in and reported.**
   - The second parallel connection and batch mode are never assumed.
   - If a benchmark run uses the second connection (M4.3), that must be explicit in its config and stated next to its results.
5. **Device time is the scarce resource and is measured.**
   - Per-test overhead (reset, launch, connect) is measured in M0.4/M0.7.
   - The G3 relevance filter exists to save device time.
   - Device minutes are reported for every run.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Build the design on Pro (parallel runs, headless CI device runs) | The trial ends during the project, and Pro is a paid service (`CLAUDE.md` asks before adding one). Results must be reproducible on the free plan. |
| Wait for Lite approval before starting | Phase 0 has a fixed deadline. The trial unblocks it, and the design does not depend on Pro features. |
| A cloud device farm for parallel devices | Paid, adds network exposure of the AltTester connection, and is out of scope for one game and one device. |

## Consequences

- **Throughput is one test at a time.** Benchmark size is set from measured per-test device time, not assumed.
- **CI cannot catch device regressions.** `pg doctor` and the smoke test catch environment drift on the PC.
- **Reproducibility:** anyone reproducing device results needs their own AltTester subscription and their own copy of the sample game.
- **After about 2026-11-06,** device work continues only if Lite is approved. If it is not, Dheeru decides between stopping device runs and paying for Pro (a paid service needs his approval).
- **Security:** the Desktop server accepts unencrypted WebSocket connections on all interfaces. Keep the PC on a trusted network and allow AltTester through the Windows firewall for private networks only. The full threat model goes in `docs/SECURITY.md` in Phase 2.
- **Operational rule:** close AltTester Desktop's inspector before automated runs, because it occupies the only driver slot (`CLAUDE.md`).

## References

- `docs/PROBLEM_STATEMENT.md` V3, V4; AltTester pricing https://alttester.com/pricing/ and terms https://alttester.com/terms-and-conditions/
- AltTester Desktop docs (connection limits, licence activation): https://alttester.com/docs/desktop/latest/pages/get-started.html
- AltTester Desktop log evidence: `docs/evidence/phase0/environment.txt`
