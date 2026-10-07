"""The game-over screen."""

from __future__ import annotations

from pg_sdk._screens import enter_screen, screen_shown
from pg_sdk._ui import Ui, parse_int
from pg_sdk.store import store_ready
from pg_sdk.types import Screen


class GameOver:
    """Shown after declining to continue. When a mission was completed, the game opens the
    missions popup over it; close that (`game.missions.close()`) before using these buttons."""

    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    def is_shown(self) -> bool:
        """True when the game-over screen is in front (no popup over it)."""
        return screen_shown(self._ui) is Screen.GAME_OVER

    def score_shown(self) -> int:
        """The final score shown for the run that just ended."""
        path = self._ui.path("game_over.score")
        self._ui.wait_until("the game-over score", lambda: self._ui.text_at(path) is not None)
        return parse_int(self._ui.text_at(path), "game-over score")

    def run_again(self) -> None:
        """Press Run! to start a new run; returns once the run HUD is shown."""
        self._require()
        enter_screen(self._ui, "game_over.run_again", "run again button", Screen.RUN)

    def open_store(self) -> None:
        """Open the store from the game-over screen."""
        self._require()
        enter_screen(self._ui, "game_over.store", "store button", Screen.STORE)
        self._ui.wait_until("the store's rows", lambda: store_ready(self._ui))

    def open_leaderboard(self) -> None:
        """Open the leaderboard from the game-over screen."""
        self._require()
        enter_screen(self._ui, "game_over.leaderboard", "leaderboard button", Screen.LEADERBOARD)

    def open_missions(self) -> None:
        """Open the missions popup from the game-over screen."""
        self._require()
        enter_screen(self._ui, "game_over.missions", "missions button", Screen.MISSIONS)

    def go_to_main_menu(self) -> None:
        """Press Main Menu; returns once the main menu is shown."""
        self._require()
        enter_screen(self._ui, "game_over.main_menu", "main menu button", Screen.MAIN_MENU)

    def _require(self) -> None:
        self._ui.wait_until("the game-over screen", self.is_shown)
