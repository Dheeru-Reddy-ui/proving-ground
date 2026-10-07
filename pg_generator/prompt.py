"""Build the generation prompt from versioned templates, specs and the SDK manifest.

Inputs are limited to the spec file, the SDK manifest, the game summary and the names/spec IDs of
accepted tests. The bug catalog, HOOKS.md, flag names and benchmark output are never read here;
`tests/unit/test_prompt_leak.py` checks every rendered prompt for bug IDs and flags.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

PROMPTS_DIR = Path(__file__).with_name("prompts")
DEFAULT_TEMPLATE = "generate_tests_v1"
CONFIRM_MARK = "[confirm]"


class SpecStatement(BaseModel):
    model_config = ConfigDict(frozen=True)

    spec_id: str
    text: str
    unconfirmed: bool


class RenderedPrompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    system: str
    user: str
    sha256: str  # over system + user exactly as sent


def parse_spec_file(text: str) -> list[SpecStatement]:
    statements = []
    for match in re.finditer(r"^- ([A-Z]+-\d+): (.*)$", text, re.MULTILINE):
        body = match.group(2).strip()
        if "(retired)" in body:
            continue
        unconfirmed = CONFIRM_MARK in body
        statements.append(
            SpecStatement(
                spec_id=match.group(1),
                text=body.replace(CONFIRM_MARK, "").strip(),
                unconfirmed=unconfirmed,
            )
        )
    return statements


def select_specs(
    statements: Sequence[SpecStatement],
    only: Sequence[str] | None = None,
    include_unconfirmed: bool = False,
) -> list[SpecStatement]:
    """By default statements still marked [confirm] are left out (approved 2026-10-07)."""
    chosen = [s for s in statements if include_unconfirmed or not s.unconfirmed]
    if only:
        wanted = set(only)
        unknown = wanted - {s.spec_id for s in statements}
        if unknown:
            raise ValueError(f"unknown spec IDs: {', '.join(sorted(unknown))}")
        chosen = [s for s in chosen if s.spec_id in wanted]
    return chosen


def sdk_reference(manifest: Mapping[str, Any]) -> str:
    """A compact, deterministic rendering of the manifest for the prompt."""
    lines: list[str] = []
    for cls, info in sorted(manifest["classes"].items()):
        lines.append(f"\n### {cls}: {_first_line(info['doc'])}")
        for name, member in sorted(info["members"].items()):
            doc = _first_sentence(member["doc"])
            if member["kind"] == "property":
                lines.append(f"- {cls}.{name} -> {member['returns']}: {doc}")
            else:
                params = ", ".join(
                    f"{p['name']}: {p['type']}" + ("" if p["required"] else f" = {p['default']}")
                    for p in member["params"]
                )
                lines.append(f"- {cls}.{name}({params}) -> {member['returns']}: {doc}")
    lines.append("\n### Records (fields)")
    for name, info in sorted(manifest["records"].items()):
        fields = ", ".join(f"{f}: {t}" for f, t in info["fields"].items())
        lines.append(f"- {name}({fields}): {_first_line(info['doc'])}")
    lines.append("\n### Enums and errors")
    for name, values in sorted(manifest["enums"].items()):
        lines.append(f"- {name}: {', '.join(v.upper() for v in values)}")
    for name, doc in sorted(manifest["exceptions"].items()):
        lines.append(f"- {name}: {_first_line(doc)}")
    lines.append(f"\nImportable from pg_sdk: {', '.join(manifest['exports'])}")
    return "\n".join(lines).strip()


def _first_line(doc: str) -> str:
    return doc.strip().splitlines()[0] if doc.strip() else ""


def _first_sentence(doc: str) -> str:
    flat = " ".join(doc.split())
    match = re.match(r"(.+?\.)(\s|$)", flat)
    return match.group(1) if match else flat


def load_template(version: str = DEFAULT_TEMPLATE) -> tuple[str, str, str]:
    """(version, system, user) from prompts/<version>.md."""
    text = (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")
    header, body = text.split("\n---\n", 1)
    declared = re.search(r"^version: (\S+)$", header, re.MULTILINE)
    if declared is None or declared.group(1) != version:
        raise ValueError(f"prompt {version}.md does not declare `version: {version}`")
    system, user = body.split("[user]\n", 1)
    return version, system.replace("[system]\n", "", 1).strip(), user.strip()


def render_prompt(
    *,
    feature: str,
    specs: Sequence[SpecStatement],
    manifest: Mapping[str, Any],
    existing_tests: Sequence[tuple[str, Sequence[str]]],
    n: int,
    template: str = DEFAULT_TEMPLATE,
) -> RenderedPrompt:
    if not specs:
        raise ValueError("no spec statements to generate tests for")
    version, system, user = load_template(template)
    summary = (PROMPTS_DIR / "game_summary.md").read_text(encoding="utf-8").strip()
    existing = (
        "\n".join(f"- {name} ({', '.join(ids)})" for name, ids in sorted(existing_tests))
        or "(none yet)"
    )
    filled = (
        user.replace("{n}", str(n))
        .replace("{feature}", feature)
        .replace("{specs}", "\n".join(f"- {s.spec_id}: {s.text}" for s in specs))
        .replace("{game_summary}", summary)
        .replace("{sdk_reference}", sdk_reference(manifest))
        .replace("{existing_tests}", existing)
    )
    digest = hashlib.sha256((system + "\n\n" + filled).encode("utf-8")).hexdigest()
    return RenderedPrompt(version=version, system=system, user=filled, sha256=digest)
