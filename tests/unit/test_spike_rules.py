"""Unit tests for the pure spike checks (pg_core.spike)."""

import pytest

from pg_core.spike import TimeScaleSample, build_tag, check_time_scale, diff_expected, lines_with


def test_build_tag() -> None:
    assert build_tag("0123456789abcdef" * 4) == "0123456789ab"
    assert build_tag(None) == "unknown-build"
    assert build_tag("") == "unknown-build"


def test_lines_with_marker() -> None:
    lines = ["a PG_X 1", "b", "c PG_X 1 again"]
    assert lines_with("PG_X 1", lines) == ["a PG_X 1", "c PG_X 1 again"]
    assert lines_with("missing", lines) == []


def test_diff_expected_reports_each_mismatch() -> None:
    expected = {"coins": 0, "characters": ["Trash Cat"], "tutorialDone": False}
    assert diff_expected(expected, expected) == []
    diffs = diff_expected({"coins": 1000, "characters": ["Trash Cat"]}, expected)
    assert diffs == ["coins: expected 0, got 1000", "tutorialDone: expected False, got None"]


def sample(scale: float, game: float, wall: float = 5.0) -> TimeScaleSample:
    return TimeScaleSample(scale=scale, wall_seconds=wall, game_seconds=game)


def test_time_scale_doubles_game_time() -> None:
    ok, detail = check_time_scale(sample(1.0, 5.0), sample(2.0, 10.1))
    assert ok
    assert "expected x2.00" in detail


@pytest.mark.parametrize("scaled_game", [5.0, 7.0, 13.0])
def test_time_scale_outside_tolerance_fails(scaled_game: float) -> None:
    ok, _ = check_time_scale(sample(1.0, 5.0), sample(2.0, scaled_game))
    assert not ok


def test_time_scale_with_frozen_game_time_fails() -> None:
    ok, detail = check_time_scale(sample(1.0, 0.0), sample(2.0, 10.0))
    assert not ok
    assert "did not advance" in detail


def test_zero_wall_time_gives_zero_rate() -> None:
    assert sample(1.0, 5.0, wall=0.0).game_per_wall == 0.0
