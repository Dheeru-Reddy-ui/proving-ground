"""Provider-agnostic LLM client interface, the Gemini implementation, and cost accounting.

Prices never live in code: per-million-token rates come from config (`PG_LLM_INPUT_USD_PER_MTOK`,
`PG_LLM_OUTPUT_USD_PER_MTOK`); the free tier is recorded at the configured rate of 0.
Every call is bounded: an HTTP timeout, and a few retries with exponential backoff and jitter on
429 and 5xx responses.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 2.0


class LLMResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    model: str
    tokens_in: int
    tokens_out: int  # includes the model's thinking tokens, which are billed as output
    latency_ms: int
    finish_reason: str | None
    attempts: int


class LLMError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class LLMClient(Protocol):
    provider: str
    model: str
    seed_supported: bool

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        temperature: float,
        seed: int | None,
        max_output_tokens: int,
        json_schema: dict[str, Any],
    ) -> LLMResponse: ...


class Rates(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_usd_per_mtok: Decimal = Decimal(0)
    output_usd_per_mtok: Decimal = Decimal(0)

    def cost(self, tokens_in: int, tokens_out: int) -> Decimal:
        million = Decimal(1_000_000)
        return (
            Decimal(tokens_in) * self.input_usd_per_mtok
            + Decimal(tokens_out) * self.output_usd_per_mtok
        ) / million


def with_retries(
    call: Callable[[], LLMResponse],
    *,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> LLMResponse:
    """Run `call`, retrying retryable LLMErrors with exponential backoff and jitter."""
    rng = rng or random.Random()  # noqa: S311 - jitter, not security
    for attempt in range(1, max_attempts + 1):
        try:
            response = call()
        except LLMError as exc:
            if not exc.retryable or attempt == max_attempts:
                raise
            sleep(BACKOFF_BASE_S * 2 ** (attempt - 1) + rng.uniform(0, BACKOFF_BASE_S))
            continue
        return response.model_copy(update={"attempts": attempt})
    raise AssertionError("unreachable")  # pragma: no cover


class GeminiClient:
    """Google Gemini API through the official `google-genai` SDK."""

    provider = "gemini"
    seed_supported = True  # GenerateContentConfig.seed: "best effort" per the SDK documentation

    def __init__(self, api_key: str, model: str, timeout_s: float = 120.0) -> None:
        from google import genai
        from google.genai import types

        self.model = model
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000))
        )

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        temperature: float,
        seed: int | None,
        max_output_tokens: int,
        json_schema: dict[str, Any],
    ) -> LLMResponse:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            seed=seed,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            response_json_schema=json_schema,
        )

        def call() -> LLMResponse:
            started = time.monotonic()
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=prompt, config=config
                )
            except errors.APIError as exc:
                raise LLMError(
                    f"Gemini API error {exc.code}: {exc.message}",
                    status=exc.code,
                    retryable=exc.code in RETRY_STATUS,
                ) from exc
            usage = response.usage_metadata
            tokens_out = 0
            tokens_in = 0
            if usage is not None:
                tokens_in = usage.prompt_token_count or 0
                tokens_out = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
            finish = None
            if response.candidates and response.candidates[0].finish_reason is not None:
                finish = str(response.candidates[0].finish_reason.value)
            return LLMResponse(
                text=response.text or "",
                model=self.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=int((time.monotonic() - started) * 1000),
                finish_reason=finish,
                attempts=1,
            )

        return with_retries(call)


def make_client(provider: str, api_key: str, model: str, timeout_s: float) -> LLMClient:
    if provider == "gemini":
        return GeminiClient(api_key=api_key, model=model, timeout_s=timeout_s)
    raise ValueError(f"unsupported LLM provider {provider!r} (supported: gemini)")
