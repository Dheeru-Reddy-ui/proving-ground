"""Model-state reads of the player's data (`*_state()`): what the game stores, not what a screen
shows. Use them to cross-check the screens; assert first on what the player sees."""

from __future__ import annotations

from typing import Any

from pg_sdk._ui import ASSEMBLY, Ui
from pg_sdk.types import LeaderboardEntry, MissionState, Volumes

# PlayerData.consumables is keyed by Consumable.ConsumableType; these are the store's names.
_POWER_UPS = {1: "Magnet", 2: "x2", 3: "Invincible", 4: "Life"}
_POWER_UP_ENUM = {"COIN_MAG": 1, "SCORE_MULTIPLAYER": 2, "INVINCIBILITY": 3, "EXTRALIFE": 4}
# Settings sliders map to mixer decibels as dB = -80 * (1 - level) (SettingPopup).
_MIN_DB = -80.0


class Player:
    """The player's saved progress and settings as the game holds them in memory."""

    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    def coins_state(self) -> int:
        """Coin balance."""
        return int(self._get("coins"))

    def premium_state(self) -> int:
        """Premium balance."""
        return int(self._get("premium"))

    def owned_characters_state(self) -> list[str]:
        """Names of the owned characters, in the order they were acquired."""
        return [str(c) for c in self._get("characters")]

    def selected_character_state(self) -> str:
        """Name of the selected character."""
        owned = self.owned_characters_state()
        return owned[int(self._get("usedCharacter"))]

    def owned_themes_state(self) -> list[str]:
        """Names of the owned themes."""
        return [str(t) for t in self._get("themes")]

    def selected_theme_state(self) -> str:
        """Name of the selected theme."""
        owned = self.owned_themes_state()
        return owned[int(self._get("usedTheme"))]

    def owned_accessories_state(self) -> list[str]:
        """Owned accessories as stored by the game ("<character>:<accessory>" per entry)."""
        return [str(a) for a in self._get("characterAccessories")]

    def power_ups_state(self) -> dict[str, int]:
        """Owned power-ups by store name ("Magnet", "x2", "Invincible", "Life") and count."""
        raw = self._get("consumables")
        result: dict[str, int] = {}
        if isinstance(raw, dict):
            for key, count in raw.items():
                result[_power_up_name(key)] = int(count)
        return result

    def missions_state(self) -> list[MissionState]:
        """Active missions with their progress, target and reward."""
        raw = self._get("missions")
        return [
            MissionState(
                progress=float(m["progress"]),
                target=float(m["max"]),
                reward=int(m["reward"]),
                complete=bool(m["isComplete"]),
            )
            for m in raw
        ]

    def volumes_state(self) -> Volumes:
        """Stored volumes converted to the settings sliders' 0.0-1.0 scale."""
        return Volumes(
            master=_level(self._get("masterVolume")),
            music=_level(self._get("musicVolume")),
            sound_effects=_level(self._get("masterSFXVolume")),
        )

    def tutorial_completed_state(self) -> bool:
        """True once the tutorial has been completed."""
        return self._get("tutorialDone") is True

    def high_scores_state(self) -> list[LeaderboardEntry]:
        """Stored high scores, best first."""
        raw = self._get("highscores")
        return [
            LeaderboardEntry(rank=i + 1, name=str(e["name"]), score=int(e["score"]))
            for i, e in enumerate(raw)
        ]

    def _get(self, field: str) -> Any:
        return self._ui.driver.static_property("PlayerData", f"instance.{field}", ASSEMBLY)


def _power_up_name(key: object) -> str:
    text = str(key)
    number = int(text) if text.lstrip("-").isdigit() else _POWER_UP_ENUM.get(text, -1)
    return _POWER_UPS.get(number, text)


def _level(decibels: object) -> float:
    return round(1.0 - float(str(decibels)) / _MIN_DB, 4)
