"""The generator's output contract: strict JSON, validated with Pydantic."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class GeneratedTest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120, pattern=r"^test_[a-z0-9_]+$")
    spec_ids: list[str] = Field(min_length=1)
    intent: str = Field(min_length=1, max_length=600)
    code: str = Field(min_length=1, max_length=8000)


class GenerationOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tests: list[GeneratedTest]


# JSON Schema sent to the model (response_json_schema); kept minimal and provider-neutral.
OUTPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "spec_ids": {"type": "array", "items": {"type": "string"}},
                    "intent": {"type": "string"},
                    "code": {"type": "string"},
                },
                "required": ["name", "spec_ids", "intent", "code"],
            },
        }
    },
    "required": ["tests"],
}


class ParseError(ValueError):
    pass


def parse_output(text: str) -> GenerationOutput:
    """Parse the model's reply. Tolerates a Markdown code fence around the JSON, nothing else."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    try:
        return GenerationOutput.model_validate_json(body)
    except ValidationError as exc:
        raise ParseError(
            f"model output is not valid: {exc.error_count()} problem(s): {exc.errors()[:3]}"
        ) from exc
