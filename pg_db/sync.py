"""Keep the specs and bugs tables in step with `specs/` and `benchmark/bugs.yaml`.

Shared by the CLI and the API. Imports nothing that needs a device, so the cloud image can use
it without the AltTester driver (ADR-0010).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from pg_cli.bugs import load_catalog
from pg_db import repo
from pg_generator.prompt import parse_spec_file

SPECS_DIR = Path("specs")


def spec_features(root: Path) -> list[str]:
    """Feature names that have a spec file (`specs/<feature>.md`, README excluded)."""
    return sorted(p.stem for p in (root / SPECS_DIR).glob("*.md") if p.stem.lower() != "readme")


def sync_specs_and_bugs(session: Session, root: Path = Path()) -> None:
    rows = []
    for feature in spec_features(root):
        text = (root / SPECS_DIR / f"{feature}.md").read_text(encoding="utf-8")
        sha = repo.sha256_text(text)
        rows += [(s.spec_id, feature, s.text, sha) for s in parse_spec_file(text)]
    repo.sync_specs(session, rows)
    repo.sync_bugs(session, load_catalog(root))
