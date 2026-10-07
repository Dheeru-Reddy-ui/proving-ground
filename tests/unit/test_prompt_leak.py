"""No rendered prompt may contain a bug ID, a flag name or other catalog material (rule 3)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pg_core.catalog import CATEGORIES, hook_flags, parse_catalog
from pg_generator.prompt import parse_spec_file, render_prompt, select_specs

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "pg_sdk/manifest.json").read_text(encoding="utf-8"))
CATALOG = parse_catalog((ROOT / "benchmark/bugs.yaml").read_text(encoding="utf-8"))
HOOKS = hook_flags((ROOT / "game/HOOKS.md").read_text(encoding="utf-8"))
SPEC_FILES = sorted(p for p in (ROOT / "specs").glob("*.md") if p.stem.lower() != "readme")

FORBIDDEN = sorted(
    {b.id for b in CATALOG.bugs}
    | {b.flag for b in CATALOG.bugs}
    | set(HOOKS)
    | set(HOOKS.values())
    | set(CATEGORIES)
    | {"PGBugFlags", "PG hook", "seeded", "holdout", "bugs.yaml", "HOOKS.md"}
)


def rendered_prompts() -> list[tuple[str, str]]:
    prompts = []
    existing = [("test_buying_magnet_charges_its_price", ["STORE-5"])]
    for path in SPEC_FILES:
        for include_unconfirmed in (False, True):
            specs = select_specs(
                parse_spec_file(path.read_text(encoding="utf-8")),
                include_unconfirmed=include_unconfirmed,
            )
            if not specs:
                continue
            prompt = render_prompt(
                feature=path.stem, specs=specs, manifest=MANIFEST, existing_tests=existing, n=8
            )
            prompts.append(
                (
                    f"{path.stem}{'+unconfirmed' if include_unconfirmed else ''}",
                    prompt.system + "\n" + prompt.user,
                )
            )
    return prompts


PROMPTS = rendered_prompts()


def test_every_spec_file_renders() -> None:
    assert len({name.split("+")[0] for name, _ in PROMPTS}) == len(SPEC_FILES) == 6


@pytest.mark.parametrize(("name", "text"), PROMPTS, ids=[n for n, _ in PROMPTS])
def test_prompt_contains_no_catalog_material(name: str, text: str) -> None:
    leaks = [term for term in FORBIDDEN if re.search(rf"(?i)\b{re.escape(term)}\b", text)]
    assert leaks == [], f"{name} prompt leaks {leaks}"
    assert not re.search(r"\bSB\d{2}\b", text)
    assert not re.search(r"\bsb_[a-z_]+", text)


def test_the_leak_check_would_catch_a_leak() -> None:
    poisoned = PROMPTS[0][1] + "\nhint: sb_item_double_charge"
    assert any(re.search(rf"(?i)\b{re.escape(t)}\b", poisoned) for t in FORBIDDEN)
