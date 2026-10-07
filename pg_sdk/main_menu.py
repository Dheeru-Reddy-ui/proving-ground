"""The main menu (the game's loadout screen): navigation and the character, theme, accessory and
power-up selectors."""

from __future__ import annotations

from pg_sdk._screens import enter_screen, screen_shown
from pg_sdk._ui import Ui, parse_int
from pg_sdk.store import store_ready as _store_ready
from pg_sdk.types import Screen


class MainMenu:
    """The screen shown after START and after leaving a run or the game-over screen."""

    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    def is_shown(self) -> bool:
        """True when the main menu is the screen in front (no store, popup or run over it)."""
        return screen_shown(self._ui) is Screen.MAIN_MENU

    # --- navigation -----------------------------------------------------------------------

    def open_store(self) -> None:
        """Open the store. Precondition: main menu shown. Ends with the store showing its
        power-ups section."""
        self._require()
        enter_screen(self._ui, "main_menu.store_button", "store button", Screen.STORE)
        self._ui.wait_until("the store's rows", lambda: _store_ready(self._ui))

    def open_missions(self) -> None:
        """Open the missions popup. Precondition: main menu shown."""
        self._require()
        enter_screen(self._ui, "main_menu.missions_button", "missions button", Screen.MISSIONS)

    def open_settings(self) -> None:
        """Open the settings popup. Precondition: main menu shown."""
        self._require()
        enter_screen(self._ui, "main_menu.settings_button", "settings button", Screen.SETTINGS)

    def open_leaderboard(self) -> None:
        """Open the leaderboard. Precondition: main menu shown."""
        self._require()
        enter_screen(
            self._ui, "main_menu.leaderboard_button", "leaderboard button", Screen.LEADERBOARD
        )

    def start_run(self) -> None:
        """Press Run. Precondition: main menu shown. Returns once the run HUD is on screen.
        On a new game the first run is the tutorial, which pauses at each obstacle until the
        player swipes; call `game.setup.complete_tutorial()` first for a normal run."""
        self._require()
        enter_screen(self._ui, "main_menu.run_button", "run button", Screen.RUN)

    # --- tutorial -------------------------------------------------------------------------

    def tutorial_shown(self) -> bool:
        """True when the tutorial overlay covers the main menu (tutorial not completed)."""
        return self._ui.visible(self._ui.path("main_menu.tutorial_overlay"))

    # --- character ------------------------------------------------------------------------

    def character_shown(self) -> str:
        """Name of the selected character as displayed. Precondition: main menu shown."""
        self._require()
        return self._read("main_menu.character_name", "character name")

    def character_arrows_shown(self) -> bool:
        """True when the character arrows are shown (the game shows them only when the player
        owns more than one character)."""
        return self._ui.visible(self._ui.path("main_menu.character_right"))

    def next_character(self) -> str:
        """Press the right character arrow; returns the character name shown afterwards.
        Precondition: character arrows shown."""
        return self._cycle("main_menu.character_right", "main_menu.character_name", "character")

    def previous_character(self) -> str:
        """Press the left character arrow; returns the character name shown afterwards.
        Precondition: character arrows shown."""
        return self._cycle("main_menu.character_left", "main_menu.character_name", "character")

    # --- theme ----------------------------------------------------------------------------

    def theme_shown(self) -> str:
        """Name of the selected theme as displayed. Precondition: main menu shown."""
        self._require()
        return self._read("main_menu.theme_name", "theme name")

    def theme_arrows_shown(self) -> bool:
        """True when the theme arrows are shown (only when more than one theme is owned)."""
        return self._ui.visible(self._ui.path("main_menu.theme_right"))

    def next_theme(self) -> str:
        """Press the right theme arrow; returns the theme name shown afterwards.
        Precondition: theme arrows shown."""
        return self._cycle("main_menu.theme_right", "main_menu.theme_name", "theme")

    def previous_theme(self) -> str:
        """Press the left theme arrow; returns the theme name shown afterwards.
        Precondition: theme arrows shown."""
        return self._cycle("main_menu.theme_left", "main_menu.theme_name", "theme")

    # --- accessory ------------------------------------------------------------------------

    def accessory_shown(self) -> str | None:
        """Name in the accessory selector, or None when the selector is not shown (the
        selected character has no owned accessory)."""
        self._require()
        path = self._ui.path("main_menu.accessory_name")
        return self._ui.text_at(path) if self._ui.visible(path) else None

    def next_accessory(self) -> str | None:
        """Press the accessory up arrow; returns the accessory shown afterwards.
        Precondition: accessory selector shown."""
        self._cycle("main_menu.accessory_up", "main_menu.accessory_name", "accessory")
        return self.accessory_shown()

    def previous_accessory(self) -> str | None:
        """Press the accessory down arrow; returns the accessory shown afterwards.
        Precondition: accessory selector shown."""
        self._cycle("main_menu.accessory_down", "main_menu.accessory_name", "accessory")
        return self.accessory_shown()

    # --- power-up -------------------------------------------------------------------------

    def power_up_selector_shown(self) -> bool:
        """True when the power-up selector is shown (only when the player owns power-ups)."""
        return self._ui.visible(self._ui.path("main_menu.power_up_right"))

    def power_up_count_shown(self) -> int | None:
        """How many of the equipped power-up the player owns, as displayed; None when no
        power-up is equipped or the selector is not shown."""
        self._require()
        path = self._ui.path("main_menu.power_up_amount")
        if not self._ui.visible(path):
            return None
        text = (self._ui.text_at(path) or "").strip()
        return parse_int(text, "power-up count") if text else None

    def next_power_up(self) -> int | None:
        """Press the right power-up arrow to equip the next owned power-up (or none); returns
        the count shown afterwards. Precondition: power-up selector shown."""
        self._tap_selector("main_menu.power_up_right", "power-up arrow")
        return self.power_up_count_shown()

    def previous_power_up(self) -> int | None:
        """Press the left power-up arrow; returns the count shown afterwards.
        Precondition: power-up selector shown."""
        self._tap_selector("main_menu.power_up_left", "power-up arrow")
        return self.power_up_count_shown()

    # --- helpers --------------------------------------------------------------------------

    def _require(self) -> None:
        self._ui.wait_until("the main menu", lambda: screen_shown(self._ui) is Screen.MAIN_MENU)

    def _read(self, key: str, what: str) -> str:
        path = self._ui.path(key)
        self._ui.wait_until(what, lambda: self._ui.visible(path))
        return self._ui.text_at(path) or ""

    def _tap_selector(self, key: str, what: str) -> None:
        self._require()
        self._ui.tap(self._ui.path(key), what)

    def _cycle(self, arrow_key: str, name_key: str, what: str) -> str:
        self._require()
        name_path = self._ui.path(name_key)
        before = self._ui.text_at(name_path)
        self._ui.tap(self._ui.path(arrow_key), f"{what} arrow")
        self._ui.wait_for_change(lambda: self._ui.text_at(name_path), before)
        return self._ui.text_at(name_path) or ""
