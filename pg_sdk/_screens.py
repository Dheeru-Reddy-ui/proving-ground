"""Which screen is in front, and moving between screens. Internal to pg_sdk."""

from __future__ import annotations

from pg_sdk._ui import Ui
from pg_sdk.types import Screen

# Checked in order: overlays first, the main menu (always underneath) last.
_ANCHORS: tuple[tuple[Screen, tuple[str, ...]], ...] = (
    (Screen.START, ("start.button",)),
    (Screen.SECOND_CHANCE, ("run.death.game_over",)),
    (Screen.PAUSE_MENU, ("run.resume",)),
    (Screen.RUN, ("run.pause",)),
    (Screen.STORE, ("store.close",)),
    (Screen.MISSIONS, ("missions.main_menu.root", "missions.game_over.root")),
    (Screen.SETTINGS, ("settings.close",)),
    (Screen.LEADERBOARD, ("leaderboard.close",)),
    (Screen.GAME_OVER, ("game_over.run_again",)),
    (Screen.MAIN_MENU, ("main_menu.store_button",)),
)


def missions_root(ui: Ui) -> str | None:
    """Path of the missions popup that is open (main menu or game over), if any."""
    for key in ("missions.main_menu.root", "missions.game_over.root"):
        root = ui.path(key)
        if ui.visible(f"{root}/{ui.path('missions.close')}"):
            return root
    return None


def screen_shown(ui: Ui) -> Screen:
    for screen, keys in _ANCHORS:
        if screen is Screen.MISSIONS:
            if missions_root(ui) is not None:
                return screen
            continue
        if any(ui.visible(ui.path(key)) for key in keys):
            return screen
    return Screen.UNKNOWN


def enter_screen(
    ui: Ui, key: str, what: str, target: Screen, timeout_s: float | None = None
) -> None:
    """Press the button at locator `key` and wait until `target` is the screen in front."""
    ui.press(ui.path(key), what)
    ui.wait_until(
        f"the {target.value.replace('_', ' ')} screen",
        lambda: screen_shown(ui) is target,
        timeout_s,
    )
