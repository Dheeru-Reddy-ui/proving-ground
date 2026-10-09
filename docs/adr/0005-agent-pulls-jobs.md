# ADR-0005: The device agent pulls jobs over outbound HTTPS

- **Status:** Accepted (approved by Dheeru on 2026-10-08 with the Phase 2 plan)
- **Date:** 2026-10-08
- **Deciders:** Dheeru (owner)

## Context

- The phone, adb and AltTester Desktop live on Dheeru's Windows PC on a home network, behind NAT (ADR-0002). The control plane runs in the cloud (Render, ADR-0010).
- AltTester Desktop's server accepts unencrypted WebSocket connections and must never be reachable from the internet (ADR-0002, Consequences).
- One device and one driver slot: the PC can run one test at a time.
- Render's free web service sleeps after 15 minutes without inbound traffic and takes about a minute to wake ([render.com/docs/free](https://render.com/docs/free), checked 2026-10-08).

## Decision

The device agent (`pg_agent`) **pulls** work from the API. It never accepts a connection.

1. **Outbound HTTPS only.** The agent calls `POST /v1/jobs/claim`, `/heartbeat`, `/complete` and the artifact endpoints. No port is opened on the PC or the router, and AltTester Desktop stays reachable only through `adb reverse` on the PC.
2. **Claim, lease, heartbeat, complete.** A claim returns at most one job with a lease token and an expiry (ADR-0006). The agent heartbeats while it works; if it dies, the lease expires and the job is retried. Completions carry the lease token, so a late answer from a lost lease is rejected.
3. **The agent says what it can do.** Each claim sends the job types it handles (`INSTALL_BUILD`, `RUN_TEST`) and its capabilities, including `max_concurrency: 1`. The server never leases a second job to an agent that already holds `max_concurrency` leases.
4. **Health gates claiming.** Before each claim the agent runs the `pg doctor` checks that need no running game (device attached, package installed, reverse forward, AltTester port open, driver version). It does not probe the app: between jobs the game is not running, because every test launches it from a reset, so an app probe would always fail (found on the first live run, 2026-10-09). A launch failure inside a test is classified infra by the runner. A missing `adb reverse` forward (a USB reconnect drops it) is restored before the check, as every test does when it starts. An unhealthy agent claims nothing and reports why.
5. **Polling with backoff.** An idle agent polls every few seconds; on API errors or a cold start it backs off exponentially with jitter, up to a cap, and keeps going.
6. **Credentials.** One-time enrolment token → per-agent bearer token, stored hashed on the server and in a file readable only by Dheeru's Windows user on the PC (`pg agent enroll`).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Push: the API calls the agent | Needs an inbound port or a tunnel into a home network, and a public endpoint next to an unencrypted AltTester server. |
| A tunnel (ngrok, Cloudflare Tunnel) to a push endpoint on the PC | Another service and another credential, and the agent must still survive the tunnel dropping. Pulling needs neither. |
| WebSocket or long-poll from the agent | Fewer requests, but a long-lived connection through a sleeping free instance and its proxy is fragile; short polls are simpler to make correct. Can be revisited if polling costs matter. |
| Run tests from GitHub Actions | The free AltTester plan has no batch mode and CI has no phone (ADR-0002). |

## Consequences

- **No inbound exposure** of the PC or of AltTester Desktop.
- **Latency:** a new job waits up to one poll interval, plus a cold start if the API slept. Negligible next to a test (tens of seconds).
- **Polling keeps the free Render service awake** while the agent runs, which uses free instance hours (750 per workspace per month, [render.com/docs/free](https://render.com/docs/free)). One always-on service fits; a second one would not.
- **Crash recovery depends on lease expiry**, so a dead agent delays its job by up to one lease TTL. On restart the agent kills runner processes left from its previous life, because an orphaned test would hold the only driver slot.
- The same shape as a self-hosted CI runner, whose host only needs to "make outbound HTTPS connections over port 443" ([GitHub docs](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)), which keeps the design familiar.

## References

- `docs/BUILD_PLAN.md`, Phase 2 target architecture and M2.4
- ADR-0002 (free-plan constraints), ADR-0006 (Postgres job queue), ADR-0010 (free-tier hosting)
