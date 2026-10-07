"""`Game`: the root of the SDK, handed to every test as the `game` fixture."""

from __future__ import annotations

from typing import Protocol

from pg_sdk._screens import screen_shown
from pg_sdk._ui import Driver, Ui
from pg_sdk.game_over import GameOver
from pg_sdk.leaderboard import Leaderboard
from pg_sdk.main_menu import MainMenu
from pg_sdk.missions import Missions
from pg_sdk.player import Player
from pg_sdk.run import Run
from pg_sdk.settings import Settings
from pg_sdk.setup import Setup
from pg_sdk.store import Store
from pg_sdk.types import Screen

RESTART_TIMEOUT_S = 60.0


class AppControl(Protocol):
    """Closes and reopens the app, keeping its data, and returns a fresh driver connection."""

    def restart(self) -> Driver: ...


class Game:
    """The game under test. Every test starts on the main menu of a new game (data cleared).

    Pages are reached as attributes (`game.store`, `game.main_menu`, ...). Readers named
    `*_shown()` return what the screen displays; `*_state()` readers return game data. Tests
    assert on what the player sees and may cross-check game data.
    """

    def __init__(self, ui: Ui, app: AppControl) -> None:
        self._ui = ui
        self._app = app
        self._main_menu = MainMenu(ui)
        self._store = Store(ui)
        self._missions = Missions(ui)
        self._settings = Settings(ui)
        self._leaderboard = Leaderboard(ui)
        self._run = Run(ui)
        self._game_over = GameOver(ui)
        self._player = Player(ui)
        self._setup = Setup(ui)

    @property
    def main_menu(self) -> MainMenu:
        """The main menu: navigation and the character/theme/accessory/power-up selectors."""
        return self._main_menu

    @property
    def store(self) -> Store:
        """The store and its four sections."""
        return self._store

    @property
    def missions(self) -> Missions:
        """The missions popup."""
        return self._missions

    @property
    def settings(self) -> Settings:
        """The settings popup."""
        return self._settings

    @property
    def leaderboard(self) -> Leaderboard:
        """The leaderboard."""
        return self._leaderboard

    @property
    def run(self) -> Run:
        """The run in progress: HUD, pause, second chance."""
        return self._run

    @property
    def game_over(self) -> GameOver:
        """The game-over screen."""
        return self._game_over

    @property
    def player(self) -> Player:
        """The player's data as the game holds it (`*_state()` readers)."""
        return self._player

    @property
    def setup(self) -> Setup:
        """Test-setup helpers that write the player's data directly. Arrange only."""
        return self._setup

    def screen_shown(self) -> Screen:
        """Which screen is in front right now."""
        return screen_shown(self._ui)

    def restart(self) -> None:
        """Close the game and open it again, keeping its saved data (like a player quitting
        and coming back). Returns on the main menu, after pressing START."""
        self._ui.log("restart the game")
        self._ui.driver = self._app.restart()
        enter_main_menu(self._ui, RESTART_TIMEOUT_S)


def enter_main_menu(ui: Ui, timeout_s: float) -> None:
    """From the Start screen: press START and wait for the main menu."""
    start = ui.path("start.button")
    ui.wait_until("the Start screen", lambda: ui.visible(start), timeout_s)
    ui.press(start, "START button")
    ui.wait_until("the main menu", lambda: screen_shown(ui) is Screen.MAIN_MENU, timeout_s)
