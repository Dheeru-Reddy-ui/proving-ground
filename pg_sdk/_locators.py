"""Locator maps: logical element names -> AltTester paths, one YAML file per build tag.

Pages never hard-code a path; they ask for a key. A build whose UI moved gets a new file
(optionally `extends:` an older tag and overrides only the changed keys), which is what keeps
Phase 3 repairs to locator-map patches.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


class LocatorError(LookupError):
    """A locator file or key is missing or malformed."""


class Locators:
    def __init__(self, tag: str, mapping: Mapping[str, str]) -> None:
        self.tag = tag
        self._mapping = dict(mapping)

    def __getitem__(self, key: str) -> str:
        try:
            return self._mapping[key]
        except KeyError:
            raise LocatorError(f"no locator {key!r} for build {self.tag}") from None

    def keys(self) -> frozenset[str]:
        return frozenset(self._mapping)

    @classmethod
    def load(cls, tag: str, directory: Path | None = None) -> Locators:
        mapping = _load_mapping(tag, directory, seen=())
        return cls(tag, mapping)


def _read(tag: str, directory: Path | None) -> str:
    name = f"{tag}.yaml"
    if directory is not None:
        path = directory / name
        if not path.is_file():
            raise LocatorError(f"no locator file {path}")
        return path.read_text(encoding="utf-8")
    resource = resources.files("pg_sdk").joinpath("locators", name)
    if not resource.is_file():
        raise LocatorError(f"no locator file pg_sdk/locators/{name}")
    return resource.read_text(encoding="utf-8")


def _load_mapping(tag: str, directory: Path | None, seen: tuple[str, ...]) -> dict[str, str]:
    if tag in seen:
        raise LocatorError(f"locator files extend each other in a cycle: {' -> '.join(seen)}")
    raw: Any = yaml.safe_load(_read(tag, directory))
    if not isinstance(raw, dict) or raw.get("build_tag") != tag:
        raise LocatorError(f"locator file {tag}.yaml must declare build_tag: {tag}")
    own = raw.get("locators") or {}
    if not isinstance(own, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in own.items()
    ):
        raise LocatorError(f"locator file {tag}.yaml: `locators` must map names to paths")
    base: dict[str, str] = {}
    parent = raw.get("extends")
    if parent is not None:
        base = _load_mapping(str(parent), directory, (*seen, tag))
    return {**base, **own}
