"""A run: the HUD, pausing, losing all lives and the second-chance offer."""

from __future__ import annotations

from typing import Any

from pg_sdk._screens import screen_shown
from pg_sdk._ui import ASSEMBLY, UI_ASSEMBLY, Ui, parse_int
from pg_sdk.types import HudReadout, Screen


class Run:
    """The run in progress, from pressing Run until the game-over screen."""

    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    def is_shown(self) -> bool:
        """True while the run HUD is in front (not paused, no second-chance offer)."""
        return screen_shown(self._ui) is Screen.RUN

    # --- HUD (what the player sees) -------------------------------------------------------

    def hud_shown(self) -> HudReadout:
        """Every HUD value at once. Precondition: run in progress or paused."""
        return HudReadout(
            score=self.score_shown(),
            distance_m=self.distance_shown(),
            multiplier=self.multiplier_shown(),
            coins=self.coins_shown(),
            premium=self.premium_shown(),
            lives=self.lives_shown(),
        )

    def score_shown(self) -> int:
        """The score on the HUD."""
        return self._int("run.score", "run score")

    def distance_shown(self) -> int:
        """The distance on the HUD, in metres."""
        return self._int("run.distance", "run distance")

    def multiplier_shown(self) -> int:
        """The score multiplier on the HUD."""
        return self._int("run.multiplier", "score multiplier")

    def coins_shown(self) -> int:
        """Coins collected in this run, as shown on the HUD."""
        return self._int("run.coins", "run coins")

    def premium_shown(self) -> int:
        """Premium currency collected in this run, as shown on the HUD."""
        return self._int("run.premium", "run premium")

    def lives_shown(self) -> int:
        """Remaining lives: the HUD hearts still drawn white (lost lives turn black)."""
        hearts = self._ui.driver.find_all(self._ui.path("run.hearts"))
        alive = 0
        for heart in hearts:
            color = self._ui.driver.component_property(
                f"//*[@id={heart.id}]", "UnityEngine.UI.Image", "color", UI_ASSEMBLY
            )
            if isinstance(color, dict) and float(color.get("g", 0)) > 0.5:
                alive += 1
        return alive

    # --- pause ----------------------------------------------------------------------------

    def pause(self) -> None:
        """Press pause. Precondition: run in front. Returns once the pause menu is shown."""
        self._ui.wait_until("the run", self.is_shown)
        self._ui.press(self._ui.path("run.pause"), "pause button")
        self._ui.wait_until("the pause menu", self.is_paused)

    def is_paused(self) -> bool:
        """True while the pause menu is shown."""
        return screen_shown(self._ui) is Screen.PAUSE_MENU

    def resume(self) -> None:
        """Press Resume on the pause menu. Returns once the run HUD is back."""
        self._ui.wait_until("the pause menu", self.is_paused)
        self._ui.press(self._ui.path("run.resume"), "resume button")
        self._ui.wait_until("the run to resume", self.is_shown)

    def quit_to_main_menu(self) -> None:
        """Press Exit on the pause menu. Returns once the main menu is shown."""
        self._ui.wait_until("the pause menu", self.is_paused)
        self._ui.press(self._ui.path("run.exit"), "exit button")
        self._ui.wait_until("the main menu", lambda: screen_shown(self._ui) is Screen.MAIN_MENU)

    # --- end of the run -------------------------------------------------------------------

    def play_until_out_of_lives(self) -> None:
        """Let the run go on without input until every life is lost (the runner hits
        obstacles on its own). Returns when the second-chance offer is shown.
        Precondition: a normal run in progress (not the tutorial, which waits for swipes)."""
        self._ui.log("play until out of lives")
        self._ui.wait_until(
            "the second-chance offer",
            self.second_chance_shown,
            timeout_s=self._ui.timeouts.run_s,
        )

    def second_chance_shown(self) -> bool:
        """True while the "another chance?" offer is shown."""
        return screen_shown(self._ui) is Screen.SECOND_CHANCE

    def continue_cost_shown(self) -> int:
        """Premium price of continuing, as shown on the offer. Precondition: offer shown."""
        self._require_offer()
        return self._int("run.death.cost", "continue cost")

    def premium_owned_shown(self) -> int:
        """The player's premium balance as shown on the offer. Precondition: offer shown."""
        self._require_offer()
        return self._int("run.death.premium_owned", "premium owned")

    def continue_enabled(self) -> bool:
        """True when the pay-to-continue button can be pressed. Precondition: offer shown."""
        self._require_offer()
        return self._ui.interactable(self._ui.path("run.death.premium_button"))

    def continue_with_premium(self) -> None:
        """Pay premium to continue the same run; returns once the run HUD is back.
        Precondition: offer shown and continuing enabled."""
        self._require_offer()
        self._ui.press(self._ui.path("run.death.premium_button"), "continue button")
        self._ui.wait_until("the run to continue", self.is_shown)

    def decline_continue(self) -> None:
        """Press Game over on the offer; returns once the game-over screen is shown (with the
        missions popup over it if a mission was completed). Precondition: offer shown."""
        self._require_offer()
        self._ui.press(self._ui.path("run.death.game_over"), "game over button")
        self._ui.wait_until(
            "the game-over screen",
            lambda: screen_shown(self._ui) in (Screen.GAME_OVER, Screen.MISSIONS),
        )

    # --- model state ----------------------------------------------------------------------

    def character_state(self) -> str:
        """Name of the character actually running (game state). Precondition: run started."""
        return str(self._track("instance.characterController.character.characterName"))

    def theme_state(self) -> str:
        """Name of the theme the run takes place in (game state). Precondition: run started."""
        return str(self._track("instance.currentTheme.themeName"))

    def score_state(self) -> int:
        """The run's score (game state). Precondition: run started."""
        return int(self._track("instance.score"))

    def coins_state(self) -> int:
        """Coins collected in this run (game state). Precondition: run started."""
        return int(self._track("instance.characterController.coins"))

    def lives_state(self) -> int:
        """Remaining lives (game state). Precondition: run started."""
        return int(self._track("instance.characterController.currentLife"))

    # --- helpers --------------------------------------------------------------------------

    def _int(self, key: str, what: str) -> int:
        path = self._ui.path(key)
        self._ui.wait_until(what, lambda: self._ui.text_at(path) is not None)
        return parse_int(self._ui.text_at(path), what)

    def _require_offer(self) -> None:
        self._ui.wait_until("the second-chance offer", self.second_chance_shown)

    def _track(self, member: str) -> Any:
        return self._ui.driver.static_property("TrackManager", member, ASSEMBLY)
