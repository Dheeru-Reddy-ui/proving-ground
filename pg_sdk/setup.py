"""Test setup helpers (`game.setup.*`): put the game into a starting state through its data.

These write model state directly and are for **arranging** a test only. Never use them as the
thing a test checks. Every helper saves the player's data afterwards: the game reloads its save
file whenever the store opens (`ShopUI.Start` calls `PlayerData.Create`), so an unsaved change
would vanish. The arranged state therefore looks like progress saved in an earlier session.
"""

from __future__ import annotations

import contextlib

from pg_sdk._ui import ASSEMBLY, Ui
from pg_sdk.errors import PGError

# Writes go through the backing field `m_Instance`: AltTester 2.3.2 cannot set a member reached
# through the static property `instance` (docs/VERSIONS.md).
_ROOT = "m_Instance"


class SetupError(PGError):
    """A setup write did not take effect."""


class Setup:
    """Arrange-only helpers that change the player's data directly."""

    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    def set_coins(self, amount: int) -> None:
        """Set the coin balance to `amount` (>= 0) and save."""
        self._set_int("coins", amount)

    def set_premium(self, amount: int) -> None:
        """Set the premium balance to `amount` (>= 0) and save."""
        self._set_int("premium", amount)

    def complete_tutorial(self) -> None:
        """Mark the tutorial as completed, so the next run is a normal run."""
        driver = self._ui.driver
        driver.set_static_property("PlayerData", f"{_ROOT}.tutorialDone", ASSEMBLY, "true")
        if driver.static_property("PlayerData", "instance.tutorialDone", ASSEMBLY) is not True:
            raise SetupError("tutorialDone did not change")
        self._save()
        self._ui.log("setup complete tutorial")

    def complete_mission(self, index: int) -> None:
        """Make the active mission at `index` (0 = first) complete by setting its progress to
        its target. The missions popup shows it as claimable the next time it opens."""
        driver = self._ui.driver
        missions = driver.static_property("PlayerData", "instance.missions", ASSEMBLY)
        if not isinstance(missions, list) or not 0 <= index < len(missions):
            raise ValueError(f"no active mission at index {index}")
        target = float(missions[index]["max"])
        member = f"{_ROOT}.missions[{index}].progress"
        # AltTester 2.3.2 sets a list element's field and then raises (docs/VERSIONS.md), so the
        # write is checked by reading it back instead of trusting the call.
        with contextlib.suppress(Exception):
            driver.set_static_property("PlayerData", member, ASSEMBLY, target)
        progress = driver.static_property(
            "PlayerData", f"instance.missions[{index}].progress", ASSEMBLY
        )
        if float(progress) < target:
            raise SetupError(f"mission {index} progress is {progress}, expected {target}")
        self._save()
        self._ui.log(f"setup complete mission {index}")

    def _set_int(self, field: str, amount: int) -> None:
        if amount < 0:
            raise ValueError(f"{field} must be >= 0, got {amount}")
        driver = self._ui.driver
        driver.set_static_property("PlayerData", f"{_ROOT}.{field}", ASSEMBLY, amount)
        if int(driver.static_property("PlayerData", f"instance.{field}", ASSEMBLY)) != amount:
            raise SetupError(f"{field} did not change to {amount}")
        self._save()
        self._ui.log(f"setup {field} = {amount}")

    def _save(self) -> None:
        self._ui.driver.call_static_method("PlayerData", f"{_ROOT}.Save", ASSEMBLY, [])
