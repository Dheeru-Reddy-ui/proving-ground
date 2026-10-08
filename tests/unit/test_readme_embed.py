"""pg report --readme replaces only the text between the README's markers."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from pg_cli.report import END, START, embed_in_readme


def test_report_goes_between_the_markers(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(f"# Title\n\nintro\n\n{START}\nold\n{END}\n\n## Next\n", encoding="utf-8")
    embed_in_readme("# Report\n\n| a | b |\n", readme)
    text = readme.read_text(encoding="utf-8")
    assert "old" not in text
    assert f"{START}\n## Report\n\n| a | b |\n{END}" in text
    assert text.startswith("# Title")
    assert text.endswith("## Next\n")


def test_a_readme_without_markers_is_refused(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n", encoding="utf-8")
    with pytest.raises(typer.BadParameter):
        embed_in_readme("# Report\n", readme)
