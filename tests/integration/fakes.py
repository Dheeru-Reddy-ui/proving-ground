"""Fakes shared by integration tests: a clock, the writes pg_worker makes for GENERATE and SCORE
jobs (fake LLM, fake gates), and RUN_TEST results as the agent reports them."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from pg_core.gates.base import Outcome
from pg_core.job_results import AttemptReport, RunTestResult
from pg_core.jobs import CompletionAction
from pg_db import pipeline, repo
from pg_db.models import Job, Validation
from pg_generator.llm import LLMError, LLMResponse

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def tick(self, seconds: float = 1.0) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


def worker_step(s: Session, job: Job, clock: Clock, fail_g1: Collection[int]) -> None:
    """What pg_worker writes for GENERATE and SCORE jobs (fake LLM, fake gates)."""
    token = job.lease_token or ""
    assert pipeline.holds_lease(s, job.id, token) is not None
    payload = job.payload
    if job.type == "GENERATE":
        validation = s.get_one(Validation, payload["validation_id"])
        run = repo.create_generation_run(
            s,
            build_id=validation.build_id,
            feature=payload["feature"],
            provider="fake",
            model="fake",
            prompt_version="v2",
            prompt_hash="h",
            n_requested=payload["n"],
            validation_id=validation.id,
            job_key=job.idempotency_key,
        )
        for i in range(payload["n"]):
            repo.add_candidate(s, run.id, f"test_{i}", "", f"code {i}", [f"STORE-{i + 1}"], idx=i)
        result = {"generation_run_id": run.id}
    elif payload["stage"] == "static":
        for cand in repo.candidates_of_run(s, payload["generation_run_id"]):
            passed = cand.idx not in fail_g1
            cand.gates = {"G1": {"gate": "G1", "passed": passed}}
            cand.pages_used = ["main_menu", "store"]
        result = {"scored": "static"}
    else:
        result = {"scored": "final"}
    assert pipeline.complete(s, job.id, token, result, clock.tick()) is CompletionAction.STORE


def run_result(outcome: Outcome, artifacts: dict[str, str] | None = None) -> RunTestResult:
    attempts = [outcome] if outcome is not Outcome.INFRA else [Outcome.INFRA] * 3
    return RunTestResult(
        attempts=tuple(
            AttemptReport(
                attempt=i,
                outcome=o,
                rule="plugin_passed" if o is Outcome.PASSED else f"rule_{o.value}",
                detail="" if o is not Outcome.INFRA else "NoAppConnected\ndevice offline",
                duration_s=12.5,
                wall_s=30.0,
                artifacts=artifacts or {},
            )
            for i, o in enumerate(attempts, 1)
        ),
        started_at=T0,
        finished_at=T0,
    )


# --- a fake LLM and the candidates it returns ---------------------------------------------

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
