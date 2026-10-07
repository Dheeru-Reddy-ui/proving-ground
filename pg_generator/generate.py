"""One generation run: render the prompt, call the model within budget, parse the candidates."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from pg_generator.llm import LLMClient, LLMError, Rates
from pg_generator.prompt import RenderedPrompt
from pg_generator.schema import OUTPUT_JSON_SCHEMA, GeneratedTest, ParseError, parse_output

CHARS_PER_TOKEN = 4  # rough estimate used only to refuse a call that could break the budget
MAX_CALLS = 3  # one call asks for all n tests; at most two more top up a short answer


class CallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: Decimal
    attempts: int
    finish_reason: str | None
    returned_tests: int
    error: str | None = None


class GenerationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str  # succeeded | partial | budget_exceeded | failed
    tests: tuple[GeneratedTest, ...]
    calls: tuple[CallRecord, ...]
    error: str | None = None

    @property
    def tokens_in(self) -> int:
        return sum(c.tokens_in for c in self.calls)

    @property
    def tokens_out(self) -> int:
        return sum(c.tokens_out for c in self.calls)

    @property
    def cost_usd(self) -> Decimal:
        return sum((c.cost_usd for c in self.calls), Decimal(0))

    @property
    def latency_ms(self) -> int:
        return sum(c.latency_ms for c in self.calls)


def estimate_cost(prompt: RenderedPrompt, max_output_tokens: int, rates: Rates) -> Decimal:
    tokens_in = (len(prompt.system) + len(prompt.user)) // CHARS_PER_TOKEN
    return rates.cost(tokens_in, max_output_tokens)


def generate(
    client: LLMClient,
    prompt: RenderedPrompt,
    *,
    n: int,
    temperature: float,
    seed: int | None,
    max_output_tokens: int,
    rates: Rates,
    budget_usd: Decimal,
) -> GenerationResult:
    """Ask for `n` tests; keep asking (up to MAX_CALLS) while fewer came back, never past budget.

    Duplicate code across calls is dropped. A call that could exceed the remaining budget is not
    made: the run ends with status `budget_exceeded` and whatever it already has.
    """
    tests: list[GeneratedTest] = []
    seen_code: set[str] = set()
    calls: list[CallRecord] = []
    spent = Decimal(0)
    status = "succeeded"
    error: str | None = None
    for call_index in range(MAX_CALLS):
        if len(tests) >= n:
            break
        if spent + estimate_cost(prompt, max_output_tokens, rates) > budget_usd:
            status, error = "budget_exceeded", f"next call could exceed the {budget_usd} USD budget"
            break
        try:
            response = client.complete(
                system=prompt.system,
                prompt=prompt.user,
                temperature=temperature,
                seed=None if seed is None else seed + call_index,
                max_output_tokens=max_output_tokens,
                json_schema=OUTPUT_JSON_SCHEMA,
            )
        except LLMError as exc:
            status, error = "failed", str(exc)
            break
        cost = rates.cost(response.tokens_in, response.tokens_out)
        spent += cost
        returned = 0
        call_error: str | None = None
        try:
            output = parse_output(response.text)
        except ParseError as exc:
            call_error = str(exc)[:500]
        else:
            for test in output.tests:
                if test.code not in seen_code and len(tests) < n:
                    seen_code.add(test.code)
                    tests.append(test)
                    returned += 1
        calls.append(
            CallRecord(
                model=response.model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                latency_ms=response.latency_ms,
                cost_usd=cost,
                attempts=response.attempts,
                finish_reason=response.finish_reason,
                returned_tests=returned,
                error=call_error,
            )
        )
    if status == "succeeded" and len(tests) < n:
        status = "partial" if tests else "failed"
        error = error or f"model returned {len(tests)} usable test(s) of {n}"
    return GenerationResult(status=status, tests=tuple(tests), calls=tuple(calls), error=error)


def call_log(result: GenerationResult) -> list[dict[str, Any]]:
    return [c.model_dump(mode="json") for c in result.calls]
