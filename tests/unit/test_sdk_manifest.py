"""The committed SDK manifest matches the code, and its pages match pg_core.pages."""

from __future__ import annotations

import json

from pg_core.pages import APP, HELPERS, PAGES
from pg_sdk.manifest import MANIFEST_PATH, build_manifest, render


def test_committed_manifest_is_current() -> None:
    expected = render(build_manifest())
    actual = MANIFEST_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "pg_sdk/manifest.json is stale: run `uv run python -m pg_sdk.manifest`"
    )


def test_game_properties_are_the_known_pages_and_helpers() -> None:
    members = build_manifest()["classes"]["Game"]["members"]
    properties = {name for name, m in members.items() if m["kind"] == "property"}
    assert properties == (PAGES - {APP}) | HELPERS


def test_every_public_member_is_documented() -> None:
    manifest = build_manifest()
    undocumented = [
        f"{cls}.{name}"
        for cls, info in manifest["classes"].items()
        for name, member in info["members"].items()
        if not member["doc"]
    ]
    assert undocumented == []


def test_manifest_exposes_no_private_names() -> None:
    text = json.dumps(build_manifest())
    assert '"_' not in text
    assert "_driver" not in text
    assert "AltTester" not in text.replace("AltTester Desktop", "")


def test_sdk_reference_doc_is_current() -> None:
    from pg_generator.prompt import REFERENCE_DOC, reference_doc

    expected = reference_doc(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    assert REFERENCE_DOC.read_text(encoding="utf-8") == expected, "run `uv run pg sdk-reference`"
