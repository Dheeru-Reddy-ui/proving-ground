"""Values returned by pg_sdk methods. Every one is immutable and holds only plain data."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Screen(StrEnum):
    """Which screen the player is looking at."""

    START = "start"
    MAIN_MENU = "main_menu"
    STORE = "store"
    MISSIONS = "missions"
    SETTINGS = "settings"
    LEADERBOARD = "leaderboard"
    RUN = "run"
    PAUSE_MENU = "pause_menu"
    SECOND_CHANCE = "second_chance"
    GAME_OVER = "game_over"
    UNKNOWN = "unknown"


class StoreItem(_Record):
    """One row of a store section, as the player sees it.

    `character` is set only in the accessories section, where rows are grouped under the
    character they belong to. `count` is the number owned, shown on power-up rows only.
    `owned` is True when the row's button reads "Owned". A price is "highlighted" when the
    store shows it in red because the player cannot cover it.
    """

    name: str
    character: str | None
    coin_price: int
    premium_price: int | None
    count: int | None
    owned: bool
    buy_enabled: bool
    coin_price_highlighted: bool
    premium_price_highlighted: bool


class PurchaseResult(_Record):
    """What the store showed just before and just after pressing Buy on one row."""

    item_before: StoreItem
    item_after: StoreItem
    coins_before: int
    coins_after: int
    premium_before: int
    premium_after: int


class Mission(_Record):
    """One mission as listed in the missions popup.

    `progress` and `target` come from the "progress / target" text, which the game shows only
    while the mission is not complete; both are None for a completed mission. `claimable` is
    True when the mission's claim button is shown.
    """

    description: str
    reward: int
    progress: int | None
    target: int | None
    claimable: bool


class MissionState(_Record):
    """One active mission as stored in the player's data (model state)."""

    progress: float
    target: float
    reward: int
    complete: bool


class Volumes(_Record):
    """Volume levels on the settings sliders' scale: 0.0 (silent) to 1.0 (full)."""

    master: float
    music: float
    sound_effects: float


class HudReadout(_Record):
    """The run HUD's values as shown on screen."""

    score: int
    distance_m: int
    multiplier: int
    coins: int
    premium: int
    lives: int


class LeaderboardEntry(_Record):
    """One leaderboard line: its rank as shown, the player name and the score."""

    rank: int
    name: str
    score: int
