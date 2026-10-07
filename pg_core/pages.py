"""Names of the pages a test can touch through `pg_sdk`, shared by the SDK, G3 and the catalog.

A page is the first attribute a test reads off the `game` fixture (`game.store...` -> `store`);
`game.restart()` and other calls on the root count as the `app` page. The bug catalog lists the
pages where each bug shows, and G3 runs a test only against bugs whose pages it touches.
"""

from __future__ import annotations

APP = "app"

# Root attributes of `game` that are not screens: model-state reads and test setup. No seeded bug
# lists them, since a bug shows on a screen.
HELPERS: frozenset[str] = frozenset({"player", "setup"})

PAGES: frozenset[str] = frozenset(
    {
        APP,
        "main_menu",
        "store",
        "missions",
        "settings",
        "leaderboard",
        "run",
        "game_over",
    }
)
