"""The SDK manifest: every public callable a generated test may use, built from the code.

`pg_sdk/manifest.json` is what the generator shows the LLM and what G1 resolves calls against.
It is derived by introspection (signatures, annotations as written, docstrings), never edited by
hand; `tests/unit/test_sdk_manifest.py` fails when the committed file is stale.

Regenerate with: `uv run python -m pg_sdk.manifest`
"""

from __future__ import annotations

import enum
import inspect
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

import pg_sdk
from pg_sdk import errors, types
from pg_sdk.game import Game

MANIFEST_PATH = Path(__file__).with_name("manifest.json")
SCHEMA = 1
FIXTURE = "game"


def _doc(obj: object) -> str:
    return inspect.cleandoc(inspect.getdoc(obj) or "")


def _annotation(value: object) -> str:
    if value is inspect.Parameter.empty or value is inspect.Signature.empty:
        return "Any"
    return value if isinstance(value, str) else getattr(value, "__name__", repr(value))


def _member(cls: type, name: str) -> dict[str, Any] | None:
    raw = inspect.getattr_static(cls, name)
    if isinstance(raw, property):
        if raw.fget is None:
            return None
        returns = inspect.signature(raw.fget).return_annotation
        return {"kind": "property", "returns": _annotation(returns), "doc": _doc(raw)}
    if inspect.isfunction(raw):
        signature = inspect.signature(raw)
        params = [
            {
                "name": p.name,
                "type": _annotation(p.annotation),
                "required": p.default is inspect.Parameter.empty,
                **({} if p.default is inspect.Parameter.empty else {"default": repr(p.default)}),
            }
            for p in list(signature.parameters.values())[1:]  # drop self
        ]
        return {
            "kind": "method",
            "params": params,
            "returns": _annotation(signature.return_annotation),
            "doc": _doc(raw),
        }
    return None


def _page_classes() -> dict[str, type]:
    """Game and every class reachable from it through public members' return types."""
    namespace = {name: obj for name, obj in vars(pg_sdk).items() if inspect.isclass(obj)}
    for module_name in (
        "main_menu",
        "store",
        "missions",
        "settings",
        "leaderboard",
        "run",
        "game_over",
        "player",
        "setup",
    ):
        module = __import__(f"pg_sdk.{module_name}", fromlist=["_"])
        namespace.update({n: o for n, o in vars(module).items() if inspect.isclass(o)})

    found: dict[str, type] = {}
    queue: list[type] = [Game]
    while queue:
        cls = queue.pop()
        if cls.__name__ in found:
            continue
        found[cls.__name__] = cls
        for name in sorted(vars(cls)):
            if name.startswith("_"):
                continue
            member = _member(cls, name)
            if member is None:
                continue
            target = namespace.get(member["returns"])
            if target is not None and _is_page(target):
                queue.append(target)
    return found


def _is_page(cls: type) -> bool:
    return (
        cls.__module__.startswith("pg_sdk.")
        and not issubclass(cls, BaseModel | enum.Enum | BaseException)
        and not cls.__name__.startswith("_")
    )


def build_manifest() -> dict[str, Any]:
    classes = {
        name: {
            "doc": _doc(cls),
            "members": {
                member_name: member
                for member_name in sorted(vars(cls))
                if not member_name.startswith("_")
                and (member := _member(cls, member_name)) is not None
            },
        }
        for name, cls in sorted(_page_classes().items())
    }
    records = {
        name: {
            "doc": _doc(cls),
            "fields": {
                field: _annotation(info.annotation)
                if not isinstance(info.annotation, str)
                else info.annotation
                for field, info in cls.model_fields.items()
            },
        }
        for name, cls in sorted(vars(types).items())
        if inspect.isclass(cls)
        and issubclass(cls, BaseModel)
        and cls.__module__ == types.__name__
        and not name.startswith("_")
    }
    enums = {
        name: [member.value for member in cls]
        for name, cls in sorted(vars(types).items())
        if inspect.isclass(cls) and issubclass(cls, enum.Enum) and cls.__module__ == types.__name__
    }
    exceptions = {
        name: _doc(cls)
        for name, cls in sorted(vars(errors).items())
        if inspect.isclass(cls)
        and issubclass(cls, BaseException)
        and cls.__module__ == errors.__name__
    }
    return {
        "schema": SCHEMA,
        "fixture": {"name": FIXTURE, "type": "Game"},
        "exports": sorted(pg_sdk.__all__),
        "classes": classes,
        "records": records,
        "enums": enums,
        "exceptions": exceptions,
    }


def render(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    MANIFEST_PATH.write_text(render(build_manifest()), encoding="utf-8", newline="\n")
    print(f"wrote {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
