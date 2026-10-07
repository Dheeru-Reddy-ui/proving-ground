"""The missions popup, opened from the main menu or shown on the game-over screen."""

from __future__ import annotations

from typing import cast

from pg_sdk._screens import missions_root
from pg_sdk._ui import Element, Ui, parse_fraction, parse_int
from pg_sdk.types import Mission


class Missions:
    """The missions popup. The game-over screen opens it by itself when a mission is complete."""

    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    def is_shown(self) -> bool:
        """True when a missions popup is open."""
        return missions_root(self._ui) is not None

    def missions_shown(self) -> list[Mission]:
        """The missions listed, in display order. Precondition: missions popup open."""
        result: list[list[Mission]] = [[]]

        def read() -> bool:
            entries = self._entries()
            missions = [self._read(entry) for entry in entries]
            if not entries or any(m is None for m in missions):
                return False
            result[0] = [cast(Mission, m) for m in missions]
            return True

        self._ui.wait_until("the missions list", read)
        return result[0]

    def claim(self, index: int) -> None:
        """Press the claim button of the mission at `index` (0 = top) and wait until the list
        is rebuilt. Precondition: missions popup open and that mission claimable."""
        before = self.missions_shown()
        if not 0 <= index < len(before):
            raise ValueError(f"mission index {index} out of range 0..{len(before) - 1}")
        entry = self._entries()[index]
        self._ui.press(self._ui.child(entry, "mission.claim"), f"claim button of mission {index}")
        self._ui.wait_for_change(self._safe_snapshot, before)

    def close(self) -> None:
        """Close the popup. Precondition: missions popup open."""
        root = self._root()
        self._ui.press(f"{root}/{self._ui.path('missions.close')}", "missions close button")
        self._ui.wait_until("the missions popup to close", lambda: missions_root(self._ui) is None)

    # --- helpers --------------------------------------------------------------------------

    def _root(self) -> str:
        found: list[str | None] = [None]

        def open_() -> bool:
            found[0] = missions_root(self._ui)
            return found[0] is not None

        self._ui.wait_until("the missions popup", open_)
        return cast(str, found[0])

    def _entries(self) -> list[Element]:
        return self._ui.driver.find_all(f"{self._root()}/{self._ui.path('missions.entries')}")

    def _read(self, entry: Element) -> Mission | None:
        ui = self._ui
        description = ui.text_at(ui.child(entry, "mission.description"))
        reward = ui.text_at(ui.child(entry, "mission.reward"))
        if description is None or reward is None:
            return None
        progress_path = ui.child(entry, "mission.progress")
        fraction = parse_fraction(ui.text_at(progress_path)) if ui.visible(progress_path) else None
        return Mission(
            description=description,
            reward=parse_int(reward, "mission reward"),
            progress=fraction[0] if fraction else None,
            target=fraction[1] if fraction else None,
            claimable=ui.visible(ui.child(entry, "mission.claim")),
        )

    def _safe_snapshot(self) -> list[Mission | None]:
        return [self._read(entry) for entry in self._entries()]
