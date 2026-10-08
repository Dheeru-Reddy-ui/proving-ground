"""Prompt building, output parsing, budget, retries and the provider adapter's contract."""

from __future__ import annotations

import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pg_generator.generate import generate
from pg_generator.llm import LLMError, LLMResponse, Rates, make_client, with_retries
from pg_generator.prompt import (
    load_template,
    parse_spec_file,
    render_prompt,
    sdk_reference,
    select_specs,
)
from pg_generator.schema import ParseError, parse_output

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "pg_sdk/manifest.json").read_text(encoding="utf-8"))
SPEC_TEXT = "# Store\n\n- STORE-1: Opens.\n- STORE-2: Shows balances. [confirm]\n- STORE-3: (retired) Old.\n"

TEST_CODE = 'import pytest\nfrom pg_sdk import Game\n\n\n@pytest.mark.spec("STORE-1")\ndef test_a(game: Game) -> None:\n    game.main_menu.open_store()\n    assert game.store.is_shown()\n'


def reply(*codes: str) -> str:
    return json.dumps(
        {
            "tests": [
                {"name": f"test_{i}", "spec_ids": ["STORE-1"], "intent": "x", "code": c}
                for i, c in enumerate(codes)
            ]
        }
    )


class FakeClient:
    provider = "fake"
    model = "fake-model"
    seed_supported = True

    def __init__(
        self, replies: list[str | Exception], tokens: tuple[int, int] = (1000, 500)
    ) -> None:
        self.replies = replies
        self.tokens = tokens
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            text=item,
            model=self.model,
            tokens_in=self.tokens[0],
            tokens_out=self.tokens[1],
            latency_ms=10,
            finish_reason="STOP",
            attempts=1,
        )


def prompt() -> Any:
    specs = select_specs(parse_spec_file(SPEC_TEXT))
    return render_prompt(feature="store", specs=specs, manifest=MANIFEST, existing_tests=[], n=2)


# --- prompt -------------------------------------------------------------------------------


def test_spec_parsing_marks_unconfirmed_and_drops_retired() -> None:
    statements = parse_spec_file(SPEC_TEXT)
    assert [(s.spec_id, s.unconfirmed) for s in statements] == [
        ("STORE-1", False),
        ("STORE-2", True),
    ]
    assert statements[1].text == "Shows balances."
    assert [s.spec_id for s in select_specs(statements)] == ["STORE-1"]
    assert len(select_specs(statements, include_unconfirmed=True)) == 2
    with pytest.raises(ValueError, match="STORE-9"):
        select_specs(statements, only=["STORE-9"])


def test_prompt_is_deterministic_and_versioned() -> None:
    first, second = prompt(), prompt()
    assert first.sha256 == second.sha256
    assert first.version == "generate_tests_v2"
    assert "- STORE-1: Opens." in first.user
    assert "STORE-2" not in first.user
    assert (
        "StoreSection.buy(name: str, character: str | None = None) -> PurchaseResult" in first.user
    )
    assert "{" + "sdk_reference" + "}" not in first.user


def test_template_must_declare_its_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "bad.md").write_text("---\nversion: other\n---\n[system]\nS\n[user]\nU\n")
    monkeypatch.setattr("pg_generator.prompt.PROMPTS_DIR", tmp_path)
    with pytest.raises(ValueError, match="does not declare"):
        load_template("bad")


def test_sdk_reference_lists_every_class() -> None:
    text = sdk_reference(MANIFEST)
    for cls in MANIFEST["classes"]:
        assert f"### {cls}:" in text


# --- parsing ------------------------------------------------------------------------------


def test_output_parsing() -> None:
    assert len(parse_output(reply(TEST_CODE)).tests) == 1
    fenced = "```json\n" + reply(TEST_CODE) + "\n```"
    assert parse_output(fenced).tests[0].code == TEST_CODE
    with pytest.raises(ParseError):
        parse_output("not json")
    with pytest.raises(ParseError):
        parse_output(
            json.dumps(
                {"tests": [{"name": "Bad Name", "spec_ids": ["X-1"], "intent": "i", "code": "c"}]}
            )
        )


# --- generation run -----------------------------------------------------------------------


def test_generation_collects_n_distinct_tests_and_costs_them() -> None:
    client = FakeClient([reply(TEST_CODE, TEST_CODE.replace("test_a", "test_b"))])
    rates = Rates(input_usd_per_mtok=Decimal("2"), output_usd_per_mtok=Decimal("10"))
    result = generate(
        client,
        prompt(),
        n=2,
        temperature=0.4,
        seed=7,
        max_output_tokens=1000,
        rates=rates,
        budget_usd=Decimal("1"),
    )
    assert result.status == "succeeded"
    assert len(result.tests) == 2
    assert result.cost_usd == Decimal("0.007")  # 1000 * 2/1e6 + 500 * 10/1e6
    assert client.calls[0]["seed"] == 7
    assert client.calls[0]["json_schema"]["required"] == ["tests"]


def test_short_answers_are_topped_up_and_duplicates_dropped() -> None:
    client = FakeClient(
        [reply(TEST_CODE), reply(TEST_CODE), reply(TEST_CODE.replace("test_a", "test_c"))]
    )
    result = generate(
        client,
        prompt(),
        n=2,
        temperature=0.4,
        seed=1,
        max_output_tokens=1000,
        rates=Rates(),
        budget_usd=Decimal("1"),
    )
    assert result.status == "succeeded"
    assert [c.returned_tests for c in result.calls] == [1, 0, 1]
    assert [c["seed"] for c in client.calls] == [1, 2, 3]


def test_unparsable_output_is_recorded_not_raised() -> None:
    client = FakeClient(["oops", "oops", "oops"])
    result = generate(
        client,
        prompt(),
        n=1,
        temperature=0.4,
        seed=None,
        max_output_tokens=1000,
        rates=Rates(),
        budget_usd=Decimal("1"),
    )
    assert result.status == "failed"
    assert all(c.error for c in result.calls)


def test_budget_stops_the_run_before_an_expensive_call() -> None:
    client = FakeClient([reply(TEST_CODE)])
    rates = Rates(input_usd_per_mtok=Decimal("1000"), output_usd_per_mtok=Decimal("1000"))
    result = generate(
        client,
        prompt(),
        n=1,
        temperature=0.4,
        seed=None,
        max_output_tokens=10000,
        rates=rates,
        budget_usd=Decimal("0.5"),
    )
    assert result.status == "budget_exceeded"
    assert client.calls == []


def test_a_provider_error_ends_the_run_cleanly() -> None:
    client = FakeClient([LLMError("quota", status=429, retryable=True)])
    result = generate(
        client,
        prompt(),
        n=1,
        temperature=0.4,
        seed=None,
        max_output_tokens=10,
        rates=Rates(),
        budget_usd=Decimal("1"),
    )
    assert result.status == "failed"
    assert "quota" in (result.error or "")


def test_retries_back_off_on_retryable_errors_only() -> None:
    delays: list[float] = []
    outcomes: list[LLMResponse | LLMError] = [
        LLMError("busy", status=503, retryable=True),
        LLMError("rate", status=429, retryable=True),
        LLMResponse(
            text="{}",
            model="m",
            tokens_in=1,
            tokens_out=1,
            latency_ms=1,
            finish_reason=None,
            attempts=1,
        ),
    ]

    def call() -> LLMResponse:
        item = outcomes.pop(0)
        if isinstance(item, LLMError):
            raise item
        return item

    response = with_retries(call, sleep=delays.append, rng=random.Random(0))  # noqa: S311 - deterministic jitter in a test
    assert response.attempts == 3
    assert len(delays) == 2
    assert 2.0 <= delays[0] < 4.0
    assert 4.0 <= delays[1] < 6.0
    with pytest.raises(LLMError, match="bad"):
        with_retries(
            lambda: (_ for _ in ()).throw(LLMError("bad", status=400)), sleep=delays.append
        )


def test_unknown_provider_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        make_client("other", "k", "m", 10)
