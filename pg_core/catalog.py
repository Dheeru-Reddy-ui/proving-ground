"""Seeded-bug catalog (M1.2): models, validation, the stratified split and the holdout freeze.

Pure: callers pass file contents in. The catalog itself (`benchmark/bugs.yaml`) is evaluation
material and must never reach an LLM prompt.

The dev/holdout split is not chosen by hand. `stratified_split` ranks the bugs of each category
by `sha256("<seed>:<bug id>")` and puts about `holdout_share` of every category with two or more
bugs into holdout; singletons fill or trim the total. The seed recorded in bugs.yaml is the hash
of the commit that added the specs, which was fixed before any bug existed, and validation fails
if the file's split differs from the draw.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CATEGORIES: frozenset[str] = frozenset(
    {
        "price_charged_wrong",
        "purchase_not_granted",
        "purchase_insufficient_funds",
        "progress_not_persisted",
        "settings_not_persisted",
        "mission_reward",
        "gameover_wrong_value",
        "selection_not_applied",
        "soft_lock",
        "error_log",
    }
)

DEFAULT_HOLDOUT_SHARE = 0.4
# Accepted holdout share of the whole catalog ("about 60% dev, 40% holdout").
HOLDOUT_SHARE_RANGE = (0.3, 0.45)


class Split(StrEnum):
    DEV = "dev"
    HOLDOUT = "holdout"


class Bug(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^SB\d{2}$")
    flag: str = Field(pattern=r"^sb_[a-z0-9_]+$")
    category: str
    feature: str
    pages: tuple[str, ...] = Field(min_length=1)
    symptom: str = Field(min_length=1)
    split: Split


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    split_seed: str = Field(min_length=7)
    holdout_share: float = Field(gt=0, lt=1)
    bugs: tuple[Bug, ...] = Field(min_length=1)

    def by_split(self, split: Split) -> tuple[Bug, ...]:
        return tuple(bug for bug in self.bugs if bug.split is split)

    @property
    def dev(self) -> tuple[Bug, ...]:
        return self.by_split(Split.DEV)

    @property
    def holdout(self) -> tuple[Bug, ...]:
        return self.by_split(Split.HOLDOUT)


class CatalogError(ValueError):
    """The catalog cannot be parsed or breaks a rule; `problems` lists every reason."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


def parse_catalog(text: str) -> Catalog:
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError([f"not valid YAML: {exc}"]) from exc
    try:
        return Catalog.model_validate(raw)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
        raise CatalogError(problems) from exc


_HOOK_ROW = re.compile(r"^\|\s*(SB\d{2})\s*\|\s*`(sb_[a-z0-9_]+)`\s*\|", re.MULTILINE)


def hook_flags(hooks_md: str) -> dict[str, str]:
    """Bug ID -> flag, from the hook-point table rows of game/HOOKS.md."""
    flags: dict[str, str] = {}
    for bug_id, flag in _HOOK_ROW.findall(hooks_md):
        flags.setdefault(bug_id, flag)
    return flags


def _rank_key(seed: str, bug_id: str) -> str:
    return hashlib.sha256(f"{seed}:{bug_id}".encode()).hexdigest()


def stratified_split(
    bugs: Iterable[tuple[str, str]], seed: str, holdout_share: float
) -> dict[str, Split]:
    """Assign each (bug id, category) to dev or holdout, deterministically from `seed`.

    1. Within each category, order bugs by `sha256("<seed>:<id>")`.
    2. A category with n >= 2 bugs sends round(n * share) of them, clamped to 1..n-1, to holdout,
       so it appears in both splits.
    3. The holdout total is round(N * share): singletons (in key order) are added to reach it,
       or the last-ranked holdout bugs of the largest categories go back to dev.
    """
    entries = sorted(set(bugs))
    ids = [bug_id for bug_id, _ in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("a bug id appears with two categories")
    by_category: dict[str, list[str]] = {}
    for bug_id, category in entries:
        by_category.setdefault(category, []).append(bug_id)
    for members in by_category.values():
        members.sort(key=lambda bug_id: _rank_key(seed, bug_id))

    holdout: set[str] = set()
    for members in by_category.values():
        n = len(members)
        if n >= 2:
            k = min(n - 1, max(1, round(n * holdout_share)))
            holdout.update(members[:k])

    target = round(len(ids) * holdout_share)
    singletons = sorted(
        (members[0] for members in by_category.values() if len(members) == 1),
        key=lambda bug_id: _rank_key(seed, bug_id),
    )
    for bug_id in singletons:
        if len(holdout) >= target:
            break
        holdout.add(bug_id)
    while len(holdout) > target:
        # Largest category first; its lowest-ranked holdout bug goes back to dev, keeping >= 1.
        candidates = [
            (len(members), [m for m in members if m in holdout])
            for members in by_category.values()
            if sum(m in holdout for m in members) > 1
        ]
        if not candidates:
            break
        _, chosen = max(candidates, key=lambda c: (c[0], c[1][-1]))
        holdout.discard(chosen[-1])

    return {bug_id: Split.HOLDOUT if bug_id in holdout else Split.DEV for bug_id in ids}


def validate_catalog(
    catalog: Catalog,
    *,
    hooks: Mapping[str, str],
    features: frozenset[str],
    pages: frozenset[str],
) -> list[str]:
    """Every rule the catalog must satisfy; an empty list means it is valid."""
    problems: list[str] = []
    seen_ids: set[str] = set()
    seen_flags: set[str] = set()
    for bug in catalog.bugs:
        if bug.id in seen_ids:
            problems.append(f"{bug.id}: duplicate id")
        seen_ids.add(bug.id)
        if bug.flag in seen_flags:
            problems.append(f"{bug.id}: duplicate flag {bug.flag}")
        seen_flags.add(bug.flag)
        if bug.id not in hooks:
            problems.append(f"{bug.id}: no hook point in HOOKS.md")
        elif hooks[bug.id] != bug.flag:
            problems.append(f"{bug.id}: flag {bug.flag} differs from HOOKS.md ({hooks[bug.id]})")
        if bug.category not in CATEGORIES:
            problems.append(f"{bug.id}: unknown category {bug.category}")
        if bug.feature not in features:
            problems.append(f"{bug.id}: unknown feature {bug.feature} (no spec file)")
        unknown_pages = sorted(set(bug.pages) - pages)
        if unknown_pages:
            problems.append(f"{bug.id}: unknown pages {', '.join(unknown_pages)}")
    for bug_id in sorted(set(hooks) - seen_ids):
        problems.append(f"{bug_id}: hook point in HOOKS.md has no catalog entry")

    share = len(catalog.holdout) / len(catalog.bugs)
    low, high = HOLDOUT_SHARE_RANGE
    if not low <= share <= high:
        problems.append(f"holdout share {share:.2f} outside {low:.2f}-{high:.2f}")

    categories: dict[str, set[Split]] = {}
    for bug in catalog.bugs:
        categories.setdefault(bug.category, set()).add(bug.split)
    counts = {c: sum(b.category == c for b in catalog.bugs) for c in categories}
    for category, splits in sorted(categories.items()):
        if counts[category] >= 2 and splits != {Split.DEV, Split.HOLDOUT}:
            problems.append(f"category {category} has {counts[category]} bugs but one split")

    if len(seen_ids) == len(catalog.bugs):
        drawn = stratified_split(
            ((b.id, b.category) for b in catalog.bugs), catalog.split_seed, catalog.holdout_share
        )
        for bug in catalog.bugs:
            if drawn[bug.id] is not bug.split:
                problems.append(f"{bug.id}: split {bug.split} differs from the seeded draw")
    return problems


def holdout_digest(catalog: Catalog) -> str:
    """sha256 over the holdout entries in canonical JSON (sorted by id and by key)."""
    entries = [bug.model_dump(mode="json") for bug in sorted(catalog.holdout, key=lambda b: b.id)]
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class FreezeRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    holdout_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bugs_yaml_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    frozen_on: str


_FREEZE_FIELD = re.compile(r"^- (holdout_sha256|bugs_yaml_commit|frozen_on): `([^`]+)`$", re.M)


def parse_freeze(text: str) -> FreezeRecord:
    fields = dict(_FREEZE_FIELD.findall(text))
    try:
        return FreezeRecord.model_validate(fields)
    except ValidationError as exc:
        problems = [f"freeze record: {err['loc'][0]} {err['msg']}" for err in exc.errors()]
        raise CatalogError(problems) from exc


def render_freeze(digest: str, commit: str, frozen_on: str, holdout_ids: Sequence[str]) -> str:
    return (
        "# Holdout freeze\n\n"
        "The holdout bugs of `benchmark/bugs.yaml` are frozen. Every benchmark run recomputes the\n"
        "digest below (`pg bugs check`) and refuses to run if it differs. Changing the holdout\n"
        "split after this point needs Dheeru's approval and a new freeze (CLAUDE.md rule 9).\n\n"
        f"- holdout_sha256: `{digest}`\n"
        f"- bugs_yaml_commit: `{commit}`\n"
        f"- frozen_on: `{frozen_on}`\n"
        f"- holdout_ids: {', '.join(holdout_ids)}\n\n"
        "The digest is sha256 over the holdout entries as canonical JSON (sorted by id, keys "
        "sorted), computed by `pg_core.catalog.holdout_digest`.\n"
    )


def verify_freeze(catalog: Catalog, record: FreezeRecord) -> list[str]:
    actual = holdout_digest(catalog)
    if actual != record.holdout_sha256:
        return [f"holdout digest {actual} differs from the frozen {record.holdout_sha256}"]
    return []
