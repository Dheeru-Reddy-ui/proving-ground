"""Pure checks used by the Phase 0 connectivity spike (`pg spike ...`)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

TIME_SCALE_TOLERANCE = 0.15


def build_tag(apk_sha256: str | None) -> str:
    """Short build identifier used to group artifacts (first 12 hex chars of the APK hash)."""
    return apk_sha256[:12] if apk_sha256 else "unknown-build"


def lines_with(marker: str, lines: Iterable[str]) -> list[str]:
    return [line for line in lines if marker in line]


def diff_expected(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> list[str]:
    """Human-readable differences between observed state and the expected values."""
    return [
        f"{key}: expected {want!r}, got {actual.get(key)!r}"
        for key, want in expected.items()
        if actual.get(key) != want
    ]


class TimeScaleSample(BaseModel):
    """How far game time (and optionally a gameplay metric) moved during a wall-clock window."""

    model_config = ConfigDict(frozen=True)

    scale: float
    wall_seconds: float
    game_seconds: float
    metric_delta: float | None = None

    @property
    def game_per_wall(self) -> float:
        return self.game_seconds / self.wall_seconds if self.wall_seconds > 0 else 0.0


def check_time_scale(
    base: TimeScaleSample, scaled: TimeScaleSample, tolerance: float = TIME_SCALE_TOLERANCE
) -> tuple[bool, str]:
    """Did game time speed up by the requested factor, within a relative tolerance?"""
    if base.scale <= 0 or base.game_per_wall <= 0:
        return False, "game time did not advance at the base time scale"
    expected = scaled.scale / base.scale
    measured = scaled.game_per_wall / base.game_per_wall
    ok = abs(measured - expected) <= tolerance * expected
    return ok, f"expected x{expected:.2f}, measured x{measured:.2f} (tolerance {tolerance:.0%})"
