"""GENERATE and SCORE jobs (M2.3), reusing `pg_generator` and the gate rules unchanged.

Each handler finishes its job itself: it re-checks the lease (`holds_lease`), writes the job's
domain rows and completes, fails or defers it, all in one transaction. A worker that lost its
lease writes nothing except the LLM calls it already paid for.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from pg_api import logs
from pg_core.budget import check_budget
from pg_core.gates.static import check_static, spec_ids_from_markdown
from pg_core.jobs import JobType
from pg_db import jobs as queue
from pg_db import pipeline, repo, scoring
from pg_db.models import GenerationRun, LlmCall, SuiteTest, Validation
from pg_db.session import session_scope
from pg_db.sync import SPECS_DIR
from pg_generator.generate import GenerationResult, generate
from pg_generator.llm import LLMClient, Rates, make_client
from pg_generator.prompt import parse_spec_file, render_prompt, select_specs
from pg_worker.settings import WorkerSettings

log = logs.get("pg_worker")
MANIFEST = Path("pg_sdk/manifest.json")


@dataclass(frozen=True)
class Lease:
    job_id: int
    token: str
    type: JobType
    key: str  # the job's idempotency key
    payload: dict[str, Any]


class LeaseLost(RuntimeError):
    """The job was re-leased (or expired) while this worker held it."""


@dataclass
class WorkerContext:
    settings: WorkerSettings
    engine: Engine
    now: Callable[[], datetime]
    jitter: Callable[[], float]
    root: Path = Path()
    llm_factory: Callable[[], LLMClient] | None = None
    _manifest: dict[str, Any] = field(default_factory=dict)

    def manifest(self) -> dict[str, Any]:
        if not self._manifest:
            self._manifest.update(json.loads((self.root / MANIFEST).read_text(encoding="utf-8")))
        return self._manifest

    def manifest_sha(self) -> str:
        return hashlib.sha256((self.root / MANIFEST).read_bytes()).hexdigest()

    def spec_ids(self) -> frozenset[str]:
        return spec_ids_from_markdown(
            p.read_text(encoding="utf-8") for p in (self.root / SPECS_DIR).glob("*.md")
        )

    def client(self) -> LLMClient:
        if self.llm_factory is not None:
            return self.llm_factory()
        s = self.settings
        if not s.pg_llm_provider or not s.pg_llm_model or s.pg_llm_api_key is None:
            raise RuntimeError("set PG_LLM_PROVIDER, PG_LLM_MODEL and PG_LLM_API_KEY")
        return make_client(
            s.pg_llm_provider,
            s.pg_llm_api_key.get_secret_value(),
            s.pg_llm_model,
            s.pg_llm_timeout_s,
        )


def _finish_or_lost(s: Session, lease: Lease) -> None:
    if pipeline.holds_lease(s, lease.job_id, lease.token) is None:
        raise LeaseLost(f"job {lease.job_id} is no longer leased to this worker")


# --- GENERATE -----------------------------------------------------------------------------


def spend(s: Session, *, build_id: int, since: datetime) -> tuple[Decimal, Decimal]:
    """(LLM spend on this build, LLM spend since `since`)."""
    build = s.execute(
        select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(LlmCall.build_id == build_id)
    ).scalar_one()
    day = s.execute(
        select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(LlmCall.created_at >= since)
    ).scalar_one()
    return Decimal(build), Decimal(day)


def handle_generate(ctx: WorkerContext, lease: Lease) -> None:
    feature = str(lease.payload["feature"])
    n = int(lease.payload["n"])
    seed = lease.payload.get("seed")
    now = ctx.now()
    with session_scope(ctx.engine) as s:
        _finish_or_lost(s, lease)
        validation = s.get_one(Validation, int(lease.payload["validation_id"]))
        if validation.manifest_sha != ctx.manifest_sha():
            pipeline.fail(
                s,
                lease.job_id,
                lease.token,
                error="the SDK manifest changed since the validation was planned; start a new one",
                retryable=False,
                now=now,
                jitter=ctx.jitter(),
            )
            return
        build_id = validation.build_id
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        build_spent, day_spent = spend(s, build_id=build_id, since=day_start)
        budget = check_budget(
            ctx.settings.budgets(), build_spent_usd=build_spent, day_spent_usd=day_spent, now=now
        )
        if not budget.allowed:
            reason = budget.reason or "LLM budget used"
            job = queue.locked(s, lease.job_id)
            if budget.retry_at is not None and job is not None:
                queue.defer(job, lease.token, until=budget.retry_at, reason=reason, now=now)
                log.info("generate_deferred", reason=reason, until=budget.retry_at.isoformat())
            else:
                pipeline.fail(
                    s, lease.job_id, lease.token, error=reason, retryable=False, now=now, jitter=0
                )
            return
        run = s.execute(
            select(GenerationRun).where(GenerationRun.job_key == lease.key)
        ).scalar_one_or_none()
        existing = [
            (t.name, list(t.spec_ids))
            for t in s.execute(
                select(SuiteTest).where(SuiteTest.status == "active", SuiteTest.origin != "human")
            ).scalars()
        ]
    spec_text = (ctx.root / SPECS_DIR / f"{feature}.md").read_text(encoding="utf-8")
    statements = select_specs(parse_spec_file(spec_text), None, False)
    prompt = render_prompt(
        feature=feature, specs=statements, manifest=ctx.manifest(), existing_tests=existing, n=n
    )
    settings = ctx.settings
    client = ctx.client()
    with session_scope(ctx.engine) as s:
        if run is None:
            run = repo.create_generation_run(
                s,
                build_id=build_id,
                feature=feature,
                provider=client.provider,
                model=client.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
                temperature=settings.pg_llm_temperature,
                seed=seed,
                seed_supported=client.seed_supported,
                n_requested=n,
                validation_id=validation.id,
                job_key=lease.key,
            )
        run_id = run.id
    log.info("generate_started", generation_run_id=run_id, feature=feature, n=n, model=client.model)
    result = generate(
        client,
        prompt,
        n=n,
        temperature=settings.pg_llm_temperature,
        seed=seed,
        max_output_tokens=settings.pg_llm_max_output_tokens,
        rates=Rates(
            input_usd_per_mtok=settings.pg_llm_input_usd_per_mtok,
            output_usd_per_mtok=settings.pg_llm_output_usd_per_mtok,
        ),
        budget_usd=budget.run_budget_usd,
    )
    _store_generation(ctx, lease, run_id, build_id, client, prompt.version, prompt.sha256, result)


def _store_generation(
    ctx: WorkerContext,
    lease: Lease,
    run_id: int,
    build_id: int,
    client: LLMClient,
    prompt_version: str,
    prompt_hash: str,
    result: GenerationResult,
) -> None:
    now = ctx.now()
    with session_scope(ctx.engine) as s:
        calls = [
            LlmCall(
                job_id=lease.job_id,
                generation_run_id=run_id,
                build_id=build_id,
                provider=client.provider,
                model=c.model,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                call_index=i,
                status="ok",
                tokens_in=c.tokens_in,
                tokens_out=c.tokens_out,
                cost_usd=c.cost_usd,
                latency_ms=c.latency_ms,
                attempts=c.attempts,
                finish_reason=c.finish_reason,
                returned_tests=c.returned_tests,
                error=c.error,
                created_at=now,
            )
            for i, c in enumerate(result.calls)
        ]
        if result.status == "failed" and result.error and not result.calls:
            calls.append(
                LlmCall(
                    job_id=lease.job_id,
                    generation_run_id=run_id,
                    build_id=build_id,
                    provider=client.provider,
                    model=client.model,
                    prompt_version=prompt_version,
                    prompt_hash=prompt_hash,
                    call_index=0,
                    status="error",
                    error=result.error[:1000],
                    created_at=now,
                )
            )
        s.add_all(calls)  # paid for even if the lease was lost: budgets must see them
        s.flush()
        if pipeline.holds_lease(s, lease.job_id, lease.token) is None:
            log.warning("generate_lease_lost", generation_run_id=run_id)
            return
        repo.finish_generation_run(
            s,
            run_id,
            status=result.status,
            finished_at=now,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            llm_calls=[c.model_dump(mode="json") for c in result.calls],
            error=result.error,
        )
        if not result.tests:
            retryable = result.status != "budget_exceeded"
            pipeline.fail(
                s,
                lease.job_id,
                lease.token,
                error=f"generation {result.status}: {result.error or 'no tests'}"[:1000],
                retryable=retryable,
                now=now,
                jitter=ctx.jitter(),
            )
            log.warning("generate_failed", status=result.status, retryable=retryable)
            return
        ids = []
        for idx, test in enumerate(result.tests):
            cand, _ = repo.add_candidate(
                s, run_id, test.name, test.intent, test.code, test.spec_ids, idx=idx
            )
            ids.append(cand.id)
        for call in calls:
            call.candidate_ids = ids
        pipeline.complete(
            s,
            lease.job_id,
            lease.token,
            {"generation_run_id": run_id, "status": result.status, "candidates": len(ids)},
            now,
        )
        log.info(
            "generate_done", generation_run_id=run_id, status=result.status, candidates=len(ids)
        )


# --- SCORE --------------------------------------------------------------------------------


def handle_score(ctx: WorkerContext, lease: Lease) -> None:
    stage = str(lease.payload["stage"])
    run_id = int(lease.payload["generation_run_id"])
    now = ctx.now()
    manifest, spec_ids = ctx.manifest(), ctx.spec_ids()
    with session_scope(ctx.engine) as s:
        _finish_or_lost(s, lease)
        validation = s.get_one(Validation, int(lease.payload["validation_id"]))
        if validation.manifest_sha != ctx.manifest_sha():
            pipeline.fail(
                s,
                lease.job_id,
                lease.token,
                error="the SDK manifest changed since the validation was planned; start a new one",
                retryable=False,
                now=now,
                jitter=0,
            )
            return
        if stage == "static":
            candidates = repo.candidates_of_run(s, run_id)
            passed = 0
            for cand in candidates:
                g1, report = check_static(cand.code, manifest, spec_ids)
                cand.gates = {**cand.gates, "G1": g1.model_dump(mode="json")}
                cand.pages_used = list(report.pages_used)
                passed += g1.passed
            result: dict[str, Any] = {
                "stage": stage,
                "g1_passed": passed,
                "g1_rejected": len(candidates) - passed,
            }
        else:
            verdicts = scoring.score_generation_run(
                s, run_id, manifest=manifest, spec_ids=spec_ids, dev_bugs=pipeline.dev_bug_refs(s)
            )
            counts: dict[str, int] = {}
            for v in verdicts:
                counts[v.decision.value] = counts.get(v.decision.value, 0) + 1
            result = {"stage": stage, "decisions": counts}
        pipeline.complete(s, lease.job_id, lease.token, result, now)
        log.info("score_done", generation_run_id=run_id, **result)


HANDLERS: dict[JobType, Callable[[WorkerContext, Lease], None]] = {
    JobType.GENERATE: handle_generate,
    JobType.SCORE: handle_score,
}
