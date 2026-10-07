"""The leaderboard popup."""

from __future__ import annotations

from pg_sdk._screens import screen_shown
from pg_sdk._ui import Ui, parse_int
from pg_sdk.types import LeaderboardEntry, Screen


class Leaderboard:
    """The leaderboard, opened from the main menu or the game-over screen."""

    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    def is_shown(self) -> bool:
        """True when the leaderboard is open."""
        return screen_shown(self._ui) is Screen.LEADERBOARD

    def entries_shown(self) -> list[LeaderboardEntry]:
        """Leaderboard lines that are on screen and have a score, top to bottom.
        Precondition: leaderboard open."""
        self._ui.wait_until("the leaderboard", self.is_shown)
        ui = self._ui
        entries: list[LeaderboardEntry] = []
        for line in ui.driver.find_all(ui.path("leaderboard.entries")):
            if not ui.on_screen(line):
                continue
            score = ui.text_at(ui.child(line, "leaderboard.entry.score"))
            name = ui.text_at(ui.child(line, "leaderboard.entry.name"))
            number = ui.text_at(ui.child(line, "leaderboard.entry.number"))
            if not score or not score.strip() or name is None or not number:
                continue
            entries.append(
                LeaderboardEntry(
                    rank=parse_int(number, "leaderboard rank"),
                    name=name,
                    score=parse_int(score, "leaderboard score"),
                )
            )
        return sorted(entries, key=lambda e: e.rank)

    def close(self) -> None:
        """Close the leaderboard. Precondition: leaderboard open."""
        self._ui.wait_until("the leaderboard", self.is_shown)
        self._ui.press(self._ui.path("leaderboard.close"), "leaderboard close button")
        self._ui.wait_until("the leaderboard to close", lambda: not self.is_shown())
