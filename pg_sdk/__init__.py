"""Typed page-object SDK for TrashCat: the only API generated tests may call.

Tests receive a `Game` through the `game` fixture of `pg_sdk.pytest_plugin`.
"""

from pg_sdk.errors import PGError, PGGameError, PGInfraError, PGTimeout
from pg_sdk.game import Game
from pg_sdk.types import (
    HudReadout,
    LeaderboardEntry,
    Mission,
    MissionState,
    PurchaseResult,
    Screen,
    StoreItem,
    Volumes,
)

__all__ = [
    "Game",
    "HudReadout",
    "LeaderboardEntry",
    "Mission",
    "MissionState",
    "PGError",
    "PGGameError",
    "PGInfraError",
    "PGTimeout",
    "PurchaseResult",
    "Screen",
    "StoreItem",
    "Volumes",
]
