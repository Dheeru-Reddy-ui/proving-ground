"""Seeded-bug catalog rules: parsing, validation, the seeded split and the holdout freeze."""

from __future__ import annotations

from pathlib import Path

import pytest

from pg_core.catalog import (
    Bug,
    Catalog,
    CatalogError,
    FreezeRecord,
    Split,
    holdout_digest,
    hook_flags,
    parse_catalog,
    parse_freeze,
    render_freeze,
    stratified_split,
    validate_catalog,
    verify_freeze,
)
from pg_core.pages import PAGES

SEED = "0123456789abcdef0123456789abcdef01234567"
FEATURES = frozenset({"store", "settings"})


def make_bugs(categories: list[str]) -> list[tuple[str, str]]:
    return [(f"SB{i + 1:02d}", category) for i, category in enumerate(categories)]


def make_catalog(categories: list[str], seed: str = SEED, share: float = 0.4) -> Catalog:
    draw = stratified_split(make_bugs(categories), seed, share)
    return Catalog(
        version=1,
        split_seed=seed,
        holdout_share=share,
        bugs=tuple(
            Bug(
                id=bug_id,
                flag=f"sb_flag_{bug_id.lower()}",
                category=category,
                feature="store",
                pages=("store",),
                symptom="something visible",
                split=draw[bug_id],
            )
            for bug_id, category in make_bugs(categories)
        ),
    )


def hooks_for(catalog: Catalog) -> dict[str, str]:
    return {bug.id: bug.flag for bug in catalog.bugs}


PAIRED = [
    "price_charged_wrong",
    "price_charged_wrong",
    "purchase_not_granted",
    "purchase_not_granted",
    "purchase_insufficient_funds",
    "purchase_insufficient_funds",
    "progress_not_persisted",
    "progress_not_persisted",
    "settings_not_persisted",
    "settings_not_persisted",
    "mission_reward",
    "mission_reward",
    "gameover_wrong_value",
    "selection_not_applied",
    "soft_lock",
    "error_log",
]


# --- stratified_split -------------------------------------------------------------------


def test_split_is_deterministic_and_order_independent() -> None:
    bugs = make_bugs(PAIRED)
    first = stratified_split(bugs, SEED, 0.4)
    assert first == stratified_split(list(reversed(bugs)), SEED, 0.4)


def test_split_puts_one_of_each_pair_in_holdout_and_singletons_in_dev() -> None:
    draw = stratified_split(make_bugs(PAIRED), SEED, 0.4)
    assert sum(split is Split.HOLDOUT for split in draw.values()) == 6  # round(16 * 0.4)
    for first in range(0, 12, 2):
        pair = [f"SB{first + 1:02d}", f"SB{first + 2:02d}"]
        assert sorted(draw[b] for b in pair) == [Split.DEV, Split.HOLDOUT]
    assert all(draw[f"SB{i:02d}"] is Split.DEV for i in range(13, 17))


def test_split_depends_on_the_seed() -> None:
    bugs = make_bugs(PAIRED)
    draws = {
        tuple(
            sorted(k for k, v in stratified_split(bugs, f"seed-{i}", 0.4).items() if v == "holdout")
        )
        for i in range(8)
    }
    assert len(draws) > 1


def test_split_fills_the_target_with_singletons() -> None:
    draw = stratified_split(make_bugs(["a", "b", "c", "d", "e"]), SEED, 0.4)
    assert sum(split is Split.HOLDOUT for split in draw.values()) == 2


def test_split_trims_large_categories_back_to_the_target() -> None:
    # One category of 5 asks for 2 holdout; a second of 2 asks for 1; target round(7*0.2)=1.
    draw = stratified_split(make_bugs(["a"] * 5 + ["b"] * 2), SEED, 0.2)
    holdout = [k for k, v in draw.items() if v is Split.HOLDOUT]
    assert len(holdout) >= 1
    # Trimming never removes a category's last holdout bug.
    assert any(k in holdout for k in ("SB06", "SB07"))


def test_split_rejects_an_id_with_two_categories() -> None:
    with pytest.raises(ValueError, match="two categories"):
        stratified_split([("SB01", "a"), ("SB01", "b")], SEED, 0.4)


# --- parse_catalog ----------------------------------------------------------------------


def test_parse_reports_invalid_yaml() -> None:
    with pytest.raises(CatalogError, match="not valid YAML"):
        parse_catalog("bugs: [unclosed")


def test_parse_reports_schema_problems() -> None:
    text = "version: 1\nsplit_seed: abcdefg\nholdout_share: 0.4\nbugs:\n  - id: X1\n"
    with pytest.raises(CatalogError) as info:
        parse_catalog(text)
    assert any("bugs.0.id" in p for p in info.value.problems)
    assert any("bugs.0.flag" in p for p in info.value.problems)


def test_parse_rejects_unknown_fields() -> None:
    catalog = make_catalog(["a", "a"])
    data = catalog.model_dump(mode="json")
    data["bugs"][0]["hint"] = "never"
    import yaml

    with pytest.raises(CatalogError, match="hint"):
        parse_catalog(yaml.safe_dump(data))


# --- validate_catalog -------------------------------------------------------------------


def test_valid_catalog_has_no_problems() -> None:
    catalog = make_catalog(PAIRED)
    assert validate_catalog(catalog, hooks=hooks_for(catalog), features=FEATURES, pages=PAGES) == []


def _with(catalog: Catalog, index: int, **changes: object) -> Catalog:
    bugs = list(catalog.bugs)
    bugs[index] = bugs[index].model_copy(update=changes)
    return catalog.model_copy(update={"bugs": tuple(bugs)})


def test_duplicate_ids_and_flags_are_reported() -> None:
    catalog = make_catalog(PAIRED)
    dup = _with(catalog, 1, id="SB01", flag=catalog.bugs[0].flag)
    problems = validate_catalog(dup, hooks=hooks_for(catalog), features=FEATURES, pages=PAGES)
    assert "SB01: duplicate id" in problems
    assert any("duplicate flag" in p for p in problems)


def test_flags_must_match_hooks_md_both_ways() -> None:
    catalog = make_catalog(PAIRED)
    hooks = hooks_for(catalog)
    hooks["SB02"] = "sb_other"
    del hooks["SB03"]
    hooks["SB99"] = "sb_orphan"
    problems = validate_catalog(catalog, hooks=hooks, features=FEATURES, pages=PAGES)
    assert any(p.startswith("SB02: flag") for p in problems)
    assert "SB03: no hook point in HOOKS.md" in problems
    assert "SB99: hook point in HOOKS.md has no catalog entry" in problems


def test_unknown_category_feature_and_page_are_reported() -> None:
    catalog = make_catalog(PAIRED)
    bad = _with(catalog, 0, category="vibes", feature="casino", pages=("store", "lobby"))
    problems = validate_catalog(bad, hooks=hooks_for(catalog), features=FEATURES, pages=PAGES)
    assert "SB01: unknown category vibes" in problems
    assert "SB01: unknown feature casino (no spec file)" in problems
    assert "SB01: unknown pages lobby" in problems


def test_hand_edited_split_is_rejected() -> None:
    catalog = make_catalog(PAIRED)
    flipped = Split.DEV if catalog.bugs[0].split is Split.HOLDOUT else Split.HOLDOUT
    bad = _with(catalog, 0, split=flipped)
    problems = validate_catalog(bad, hooks=hooks_for(catalog), features=FEATURES, pages=PAGES)
    assert any("SB01: split" in p and "seeded draw" in p for p in problems)


def test_holdout_share_out_of_range_is_reported() -> None:
    catalog = make_catalog(["a", "a", "b", "b"], share=0.5)
    problems = validate_catalog(catalog, hooks=hooks_for(catalog), features=FEATURES, pages=PAGES)
    assert any("holdout share 0.50" in p for p in problems)


def test_paired_category_in_one_split_is_reported() -> None:
    catalog = make_catalog(PAIRED)
    # Force SB01 and SB02 (same category) into dev; the draw check also fires.
    bad = _with(_with(catalog, 0, split=Split.DEV), 1, split=Split.DEV)
    problems = validate_catalog(bad, hooks=hooks_for(catalog), features=FEATURES, pages=PAGES)
    assert "category price_charged_wrong has 2 bugs but one split" in problems


# --- hook_flags -------------------------------------------------------------------------


def test_hook_flags_reads_the_table_rows_only() -> None:
    text = (
        "| ID | Flag |\n|---|---|\n"
        "| SB01 | `sb_one` | price | store |\n"
        "| SB02 | `sb_two` | x |\n"
        "Text mentioning `sb_three` outside the table.\n"
    )
    assert hook_flags(text) == {"SB01": "sb_one", "SB02": "sb_two"}


# --- freeze -----------------------------------------------------------------------------


def test_digest_covers_holdout_only_and_is_stable() -> None:
    catalog = make_catalog(PAIRED)
    digest = holdout_digest(catalog)
    assert digest == holdout_digest(catalog.model_copy(update={"bugs": catalog.bugs[::-1]}))
    dev_index = next(i for i, b in enumerate(catalog.bugs) if b.split is Split.DEV)
    assert holdout_digest(_with(catalog, dev_index, symptom="edited")) == digest
    hold_index = next(i for i, b in enumerate(catalog.bugs) if b.split is Split.HOLDOUT)
    assert holdout_digest(_with(catalog, hold_index, symptom="edited")) != digest


def test_freeze_round_trip_and_verification() -> None:
    catalog = make_catalog(PAIRED)
    digest = holdout_digest(catalog)
    text = render_freeze(digest, "a" * 40, "2026-10-08", [b.id for b in catalog.holdout])
    record = parse_freeze(text)
    assert record == FreezeRecord(
        holdout_sha256=digest, bugs_yaml_commit="a" * 40, frozen_on="2026-10-08"
    )
    assert verify_freeze(catalog, record) == []
    hold_index = next(i for i, b in enumerate(catalog.bugs) if b.split is Split.HOLDOUT)
    assert verify_freeze(_with(catalog, hold_index, pages=("run",)), record)


def test_parse_freeze_rejects_a_damaged_record() -> None:
    with pytest.raises(CatalogError, match="holdout_sha256"):
        parse_freeze("- bugs_yaml_commit: `" + "a" * 40 + "`\n- frozen_on: `2026-10-08`\n")


# --- the real catalog -------------------------------------------------------------------


def test_repository_catalog_is_valid() -> None:
    from pg_cli.bugs import load_catalog

    root = Path(__file__).resolve().parents[2]
    catalog = load_catalog(root)
    assert len(catalog.bugs) == 16
    assert len(catalog.holdout) == 6
