"""The worker (M2.3) with a fake LLM, the real gates and the real catalog, against Postgres.

A validation runs end to end (worker + fake agent) and its candidates are decided by the same
gate rules as `pg prove`. Also: an LLM error storm gives one set of candidates, budgets stop or
defer generation, the kill switch leaves GENERATE queued, and the worker's prompts never carry a
bug ID or flag.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select

from pg_core.catalog import parse_catalog
from pg_core.gates.base import Outcome
from pg_core.gates.static import check_static, spec_ids_from_markdown
from pg_core.jobs import DEVICE_JOB_TYPES, CompletionAction, JobType
from pg_db import jobs as queue
from pg_db import pipeline, repo
from pg_db.models import (
    Build,
    Candidate,
    Execution,
    GenerationRun,
    Job,
    Kill,
    LlmCall,
    Validation,
    Worker,
)
from pg_db.session import session_scope
from pg_db.sync import sync_specs_and_bugs
from pg_generator.llm import LLMError, LLMResponse
from pg_worker.handlers import WorkerContext
from pg_worker.loop import WorkerLoop
from pg_worker.settings import WorkerSettings
from tests.integration.fakes import Clock, run_result

ROOT = Path(__file__).resolve().parents[2]
SHA = "f" * 64
MANIFEST = json.loads((ROOT / "pg_sdk/manifest.json").read_text(encoding="utf-8"))
MANIFEST_SHA = hashlib.sha256((ROOT / "pg_sdk/manifest.json").read_bytes()).hexdigest()
SPEC_IDS = spec_ids_from_markdown(
    p.read_text(encoding="utf-8") for p in (ROOT / "specs").glob("*.md")
)
GOOD = (ROOT / "suites/accepted/test_buy_character_unlock_and_select_c13.py").read_text(
    encoding="utf-8"
)
GOOD = "\n".join(line for line in GOOD.splitlines() if not line.startswith("# "))
OTHER_SPEC = GOOD.replace('"STORE-5", "STORE-7", "STORE-10"', '"STORE-2"').replace(
    "def test_buy_character_unlock_and_select", "def test_buy_raccoon_other_spec"
)
HALLUCINATED = GOOD.replace(
    'res = game.store.characters.buy("Rubbish Raccoon")',
    'res = game.store.characters.buy_everything("Rubbish Raccoon")',
).replace("def test_buy_character_unlock_and_select", "def test_buy_everything")
FLAKY = GOOD.replace("def test_buy_character_unlock_and_select", "def test_buy_flaky").replace(
    '"STORE-5", "STORE-7", "STORE-10"', '"STORE-3"'
)
CODES = [GOOD, OTHER_SPEC, HALLUCINATED, FLAKY]


def reply(codes: list[str]) -> str:
    tests = []
    for code in codes:
        name = code.split("def ", 1)[1].split("(", 1)[0]
        spec_ids = (
            code.split("@pytest.mark.spec(", 1)[1].split(")", 1)[0].replace('"', "").split(", ")
        )
        tests.append(
            {"name": name, "spec_ids": spec_ids, "intent": "buy a character", "code": code}
        )
    return json.dumps({"tests": tests})


class FakeLLM:
    provider = "fake"
    model = "fake-model"
    seed_supported = False

    def __init__(
        self, replies: list[str | LLMError], on_call: Callable[[], None] | None = None
    ) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.on_call = on_call

    def complete(self, *, system: str, prompt: str, **_: Any) -> LLMResponse:
        self.prompts.append(system + "\n" + prompt)
        if self.on_call is not None:
            self.on_call()
        answer = self.replies.pop(0)
        if isinstance(answer, LLMError):
            raise answer
        return LLMResponse(
            text=answer,
            model=self.model,
            tokens_in=1200,
            tokens_out=800,
            latency_ms=7,
            finish_reason="STOP",
            attempts=1,
        )


@pytest.fixture(autouse=True)
def fresh(engine: Engine) -> None:
    with session_scope(engine) as s:
        for table in (
            LlmCall,
            Kill,
            Execution,
            Job,
            Candidate,
            GenerationRun,
            Validation,
            Worker,
            Build,
        ):
            s.execute(delete(table))
        sync_specs_and_bugs(s, ROOT)
        repo.register_build(s, SHA, "worker test build", "87d396162a05")


def make_worker(
    engine: Engine, database_url: str, clock: Clock, llm: FakeLLM, **settings: Any
) -> WorkerLoop:
    values: dict[str, Any] = {"database_url": database_url, "pg_worker_name": "worker-test"}
    values.update(settings)
    ctx = WorkerContext(
        settings=WorkerSettings(_env_file=None, **values),  # type: ignore[call-arg]
        engine=engine,
        now=lambda: clock.now,
        jitter=lambda: 0.0,
        root=ROOT,
        llm_factory=lambda: llm,
    )
    return WorkerLoop(ctx, sleep=lambda _: False)


def validate(engine: Engine, clock: Clock, n: int = 4, manifest_sha: str = MANIFEST_SHA) -> int:
    with session_scope(engine) as s:
        build = s.execute(select(Build)).scalar_one()
        validation, _ = pipeline.create_validation(
            s,
            build_id=build.id,
            features=("store",),
            n=n,
            seed=None,
            manifest_sha=manifest_sha,
            run_timeout_s=300,
            requested_by="test",
            now=clock.now,
        )
        return validation.id


def agent_turn(engine: Engine, clock: Clock, oracle: Callable[[int, str, int], Outcome]) -> bool:
    with session_scope(engine) as s:
        job = queue.claim(
            s,
            owner="agent-1",
            types=list(DEVICE_JOB_TYPES),
            capabilities={"platform": "android"},
            now=clock.tick(),
        )
        if job is None:
            return False
        token = job.lease_token or ""
        if job.type == JobType.INSTALL_BUILD.value:
            pipeline.complete(s, job.id, token, {"installed_sha256": SHA}, clock.now)
            return True
        cand = s.get_one(Candidate, job.payload["candidate_id"])
        flags = job.payload["flags"]
        outcome = oracle(cand.idx or 0, flags[0] if flags else "clean", job.payload["repeat"])
        action = pipeline.complete_run_test(s, job.id, token, run_result(outcome), clock.now, 0.0)
        assert action is CompletionAction.STORE
        return True


def drive(
    engine: Engine, clock: Clock, worker: WorkerLoop, oracle: Callable[[int, str, int], Outcome]
) -> None:
    for _ in range(400):
        clock.tick(11)  # lets the worker reap and record itself every turn
        progressed = worker.run_once()
        progressed = agent_turn(engine, clock, oracle) or progressed
        if not progressed:
            with session_scope(engine) as s:
                if not s.execute(select(Job).where(Job.status == "queued")).first():
                    return
            clock.tick(60)
    raise AssertionError("the validation did not finish")


def killed_bug() -> str:
    """A dev bug on the pages the good test touches: the one the oracle makes it kill."""
    _, report = check_static(GOOD, MANIFEST, SPEC_IDS)
    catalog = parse_catalog((ROOT / "benchmark/bugs.yaml").read_text(encoding="utf-8"))
    relevant = [b for b in catalog.dev if set(b.pages) & set(report.pages_used)]
    return relevant[0].flag


def test_a_validation_is_generated_proved_and_decided(engine: Engine, database_url: str) -> None:
    clock = Clock()
    llm = FakeLLM([reply(CODES)])
    worker = make_worker(engine, database_url, clock, llm)
    target = killed_bug()

    def oracle(idx: int, key: str, repeat: int) -> Outcome:
        if idx == 3 and key == "clean" and repeat == 2:
            return Outcome.ASSERTION  # the flaky one
        if idx == 0 and key == target:
            return Outcome.ASSERTION
        return Outcome.PASSED

    vid = validate(engine, clock)
    drive(engine, clock, worker, oracle)
    with session_scope(engine) as s:
        validation = s.get_one(Validation, vid)
        assert validation.status == "succeeded", validation.problems
        run = s.execute(select(GenerationRun)).scalar_one()
        assert run.validation_id == vid
        assert run.status == "succeeded"
        cands = repo.candidates_of_run(s, run.id)
        decisions = {c.idx: (c.decision, [r["code"] for r in c.reasons]) for c in cands}
        assert decisions[0] == ("accept", ["kills_dev_bugs"])
        assert decisions[1][0] == "review"
        assert decisions[2] == ("reject", ["unknown_sdk_member"])
        assert decisions[3] == ("reject", ["not_deterministic"])
        kills = s.execute(select(Kill).where(Kill.killed.is_(True))).scalars().all()
        assert [(k.candidate_id, k.run_group) for k in kills] == [(cands[0].id, f"prove-{run.id}")]
        (call,) = s.execute(select(LlmCall)).scalars().all()
        assert call.status == "ok"
        assert call.candidate_ids == [c.id for c in cands]
        assert call.prompt_hash == run.prompt_hash
        assert s.get_one(Worker, "worker-test").generation_enabled is True
    catalog = parse_catalog((ROOT / "benchmark/bugs.yaml").read_text(encoding="utf-8"))
    for prompt in llm.prompts:
        for bug in catalog.bugs:  # rule 3: no bug ID or flag in any prompt
            assert bug.id not in prompt
            assert bug.flag not in prompt


def test_an_llm_error_storm_gives_one_set_of_candidates(engine: Engine, database_url: str) -> None:
    clock = Clock()
    storm = [LLMError("Gemini API error 503: high demand", status=503, retryable=True)] * 2
    llm = FakeLLM([*storm, reply(CODES[:2])])
    worker = make_worker(engine, database_url, clock, llm)
    validate(engine, clock, n=2)
    for _ in range(3):
        assert worker.run_once()
        clock.tick(120)  # past the job's retry backoff
    with session_scope(engine) as s:
        job = s.execute(select(Job).where(Job.type == "GENERATE")).scalar_one()
        assert (job.status, job.attempts) == ("succeeded", 3)
        assert s.query(GenerationRun).count() == 1
        assert s.query(Candidate).count() == 2
        statuses = [c.status for c in s.execute(select(LlmCall).order_by(LlmCall.id)).scalars()]
        assert statuses == ["error", "error", "ok"]


def test_a_spent_build_budget_stops_generation(engine: Engine, database_url: str) -> None:
    clock = Clock()
    worker = make_worker(
        engine, database_url, clock, FakeLLM([]), pg_max_cost_per_build_usd=Decimal("0.01")
    )
    validate(engine, clock)
    _spend(engine, clock.now - timedelta(days=3), Decimal("0.02"))
    assert worker.run_once()
    with session_scope(engine) as s:
        job = s.execute(select(Job).where(Job.type == "GENERATE")).scalar_one()
        assert job.status == "failed"
        assert job.last_error == "build LLM budget used: 0.020000 of 0.01 USD"


def test_a_spent_daily_budget_waits_for_tomorrow(engine: Engine, database_url: str) -> None:
    clock = Clock()
    worker = make_worker(
        engine, database_url, clock, FakeLLM([]), pg_max_cost_per_day_usd=Decimal("0.01")
    )
    validate(engine, clock)
    _spend(engine, clock.now, Decimal("0.02"))
    assert worker.run_once()
    with session_scope(engine) as s:
        job = s.execute(select(Job).where(Job.type == "GENERATE")).scalar_one()
        assert (job.status, job.attempts) == ("queued", 0)
        assert job.run_after == datetime(2026, 10, 9, tzinfo=UTC)
        assert job.last_error is not None
        assert job.last_error.startswith("daily LLM budget used")


def _spend(engine: Engine, when: datetime, usd: Decimal) -> None:
    with session_scope(engine) as s:
        build = s.execute(select(Build)).scalar_one()
        s.add(
            LlmCall(
                build_id=build.id,
                provider="fake",
                model="m",
                prompt_version="v2",
                prompt_hash="h",
                call_index=0,
                status="ok",
                cost_usd=usd,
                created_at=when,
            )
        )


def test_the_kill_switch_leaves_generation_queued(engine: Engine, database_url: str) -> None:
    clock = Clock()
    worker = make_worker(engine, database_url, clock, FakeLLM([]), pg_generation_enabled=False)
    validate(engine, clock)
    assert worker.run_once() is False
    with session_scope(engine) as s:
        assert s.execute(select(Job.status).where(Job.type == "GENERATE")).scalar_one() == "queued"
        assert s.get_one(Worker, "worker-test").generation_enabled is False


def test_a_changed_sdk_manifest_fails_the_job(engine: Engine, database_url: str) -> None:
    clock = Clock()
    worker = make_worker(engine, database_url, clock, FakeLLM([reply(CODES)]))
    validate(engine, clock, manifest_sha="0" * 64)
    assert worker.run_once()
    with session_scope(engine) as s:
        job = s.execute(select(Job).where(Job.type == "GENERATE")).scalar_one()
        assert job.status == "failed"
        assert "manifest changed" in (job.last_error or "")


def test_a_lost_lease_writes_only_the_paid_llm_call(engine: Engine, database_url: str) -> None:
    clock = Clock()

    def steal() -> None:  # the lease expires and another worker takes the job mid-call
        with session_scope(engine) as s:
            job = s.execute(select(Job).where(Job.type == "GENERATE")).scalar_one()
            job.lease_token = "someone-else"  # noqa: S105 (not a credential)

    worker = make_worker(engine, database_url, clock, FakeLLM([reply(CODES)], on_call=steal))
    validate(engine, clock)
    assert worker.run_once()
    with session_scope(engine) as s:
        assert s.query(Candidate).count() == 0
        assert s.query(LlmCall).count() == 1
        job = s.execute(select(Job).where(Job.type == "GENERATE")).scalar_one()
        assert job.status == "leased"  # left to the other holder
