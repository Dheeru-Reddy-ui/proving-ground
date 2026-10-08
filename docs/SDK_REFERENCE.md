# pg_sdk reference

Generated from `pg_sdk/manifest.json` by `uv run pg sdk-reference`; do not edit by hand.
Every test gets a `Game` as its `game` fixture, on the main menu of a new game.
Members are listed as `Class.member(params) -> return type: description`.

## Game: The game under test. Every test starts on the main menu of a new game (data cleared).
- Game.game_over -> GameOver: The game-over screen.
- Game.leaderboard -> Leaderboard: The leaderboard.
- Game.main_menu -> MainMenu: The main menu: navigation and the character/theme/accessory/power-up selectors.
- Game.missions -> Missions: The missions popup.
- Game.player -> Player: The player's data as the game holds it (`*_state()` readers).
- Game.restart() -> None: Close the game and open it again, keeping its saved data (like a player quitting and coming back).
- Game.run -> Run: The run in progress: HUD, pause, second chance.
- Game.screen_shown() -> Screen: Which screen is in front right now.
- Game.settings -> Settings: The settings popup.
- Game.setup -> Setup: Test-setup helpers that write the player's data directly.
- Game.store -> Store: The store and its four sections.

## GameOver: Shown after declining to continue. When a mission was completed, the game opens the
- GameOver.go_to_main_menu() -> None: Press Main Menu; returns once the main menu is shown.
- GameOver.is_shown() -> bool: True when the game-over screen is in front (no popup over it).
- GameOver.open_leaderboard() -> None: Open the leaderboard from the game-over screen.
- GameOver.open_missions() -> None: Open the missions popup from the game-over screen.
- GameOver.open_store() -> None: Open the store from the game-over screen.
- GameOver.run_again() -> None: Press Run! to start a new run; returns once the run HUD is shown.
- GameOver.score_shown() -> int: The final score shown for the run that just ended.

## Leaderboard: The leaderboard, opened from the main menu or the game-over screen.
- Leaderboard.close() -> None: Close the leaderboard.
- Leaderboard.entries_shown() -> list[LeaderboardEntry]: Leaderboard lines that are on screen and have a score, top to bottom.
- Leaderboard.is_shown() -> bool: True when the leaderboard is open.

## MainMenu: The screen shown after START and after leaving a run or the game-over screen.
- MainMenu.accessory_shown() -> str | None: Name in the accessory selector, or None when the selector is not shown (the selected character has no owned accessory).
- MainMenu.character_arrows_shown() -> bool: True when the character arrows are shown (the game shows them only when the player owns more than one character).
- MainMenu.character_shown() -> str: Name of the selected character as displayed.
- MainMenu.is_shown() -> bool: True when the main menu is the screen in front (no store, popup or run over it).
- MainMenu.next_accessory() -> str | None: Press the accessory up arrow; returns the accessory shown afterwards.
- MainMenu.next_character() -> str: Press the right character arrow; returns the character name shown afterwards.
- MainMenu.next_power_up() -> int | None: Press the right power-up arrow to equip the next owned power-up (or none); returns the count shown afterwards.
- MainMenu.next_theme() -> str: Press the right theme arrow; returns the theme name shown afterwards.
- MainMenu.open_leaderboard() -> None: Open the leaderboard.
- MainMenu.open_missions() -> None: Open the missions popup.
- MainMenu.open_settings() -> None: Open the settings popup.
- MainMenu.open_store() -> None: Open the store.
- MainMenu.power_up_count_shown() -> int | None: How many of the equipped power-up the player owns, as displayed; None when no power-up is equipped or the selector is not shown.
- MainMenu.power_up_selector_shown() -> bool: True when the power-up selector is shown (only when the player owns power-ups).
- MainMenu.previous_accessory() -> str | None: Press the accessory down arrow; returns the accessory shown afterwards.
- MainMenu.previous_character() -> str: Press the left character arrow; returns the character name shown afterwards.
- MainMenu.previous_power_up() -> int | None: Press the left power-up arrow; returns the count shown afterwards.
- MainMenu.previous_theme() -> str: Press the left theme arrow; returns the theme name shown afterwards.
- MainMenu.start_run() -> None: Press Run.
- MainMenu.theme_arrows_shown() -> bool: True when the theme arrows are shown (only when more than one theme is owned).
- MainMenu.theme_shown() -> str: Name of the selected theme as displayed.
- MainMenu.tutorial_shown() -> bool: True when the tutorial overlay covers the main menu (tutorial not completed).

## Missions: The missions popup. The game-over screen opens it by itself when a mission is complete.
- Missions.claim(index: int) -> None: Press the claim button of the mission at `index` (0 = top) and wait until the list is rebuilt.
- Missions.close() -> None: Close the popup.
- Missions.is_shown() -> bool: True when a missions popup is open.
- Missions.missions_shown() -> list[Mission]: The missions listed, in display order.

## Player: The player's saved progress and settings as the game holds them in memory.
- Player.coins_state() -> int: Coin balance.
- Player.high_scores_state() -> list[LeaderboardEntry]: Stored high scores, best first.
- Player.missions_state() -> list[MissionState]: Active missions with their progress, target and reward.
- Player.owned_accessories_state() -> list[str]: Owned accessories as stored by the game ("<character>:<accessory>" per entry).
- Player.owned_characters_state() -> list[str]: Names of the owned characters, in the order they were acquired.
- Player.owned_themes_state() -> list[str]: Names of the owned themes.
- Player.power_ups_state() -> dict[str, int]: Owned power-ups by store name ("Magnet", "x2", "Invincible", "Life") and count.
- Player.premium_state() -> int: Premium balance.
- Player.selected_character_state() -> str: Name of the selected character.
- Player.selected_theme_state() -> str: Name of the selected theme.
- Player.tutorial_completed_state() -> bool: True once the tutorial has been completed.
- Player.volumes_state() -> Volumes: Stored volumes converted to the settings sliders' 0.0-1.0 scale.

## Run: The run in progress, from pressing Run until the game-over screen.
- Run.character_state() -> str: Name of the character actually running (game state).
- Run.coins_shown() -> int: Coins collected in this run, as shown on the HUD.
- Run.coins_state() -> int: Coins collected in this run (game state).
- Run.continue_cost_shown() -> int: Premium price of continuing, as shown on the offer.
- Run.continue_enabled() -> bool: True when the pay-to-continue button can be pressed.
- Run.continue_with_premium() -> None: Pay premium to continue the same run; returns once the run HUD is back.
- Run.decline_continue() -> None: Press Game over on the offer; returns once the game-over screen is shown (with the missions popup over it if a mission was completed).
- Run.distance_shown() -> int: The distance on the HUD, in metres.
- Run.hud_shown() -> HudReadout: Every HUD value at once.
- Run.is_paused() -> bool: True while the pause menu is shown.
- Run.is_shown() -> bool: True while the run HUD is in front (not paused, no second-chance offer).
- Run.lives_shown() -> int: Remaining lives: the HUD hearts still drawn white (lost lives turn black).
- Run.lives_state() -> int: Remaining lives (game state).
- Run.multiplier_shown() -> int: The score multiplier on the HUD.
- Run.pause() -> None: Press pause.
- Run.play_until_out_of_lives() -> None: Let the run go on without input until every life is lost (the runner hits obstacles on its own).
- Run.premium_owned_shown() -> int: The player's premium balance as shown on the offer.
- Run.premium_shown() -> int: Premium currency collected in this run, as shown on the HUD.
- Run.quit_to_main_menu() -> None: Press Exit on the pause menu.
- Run.resume() -> None: Press Resume on the pause menu.
- Run.score_shown() -> int: The score on the HUD.
- Run.score_state() -> int: The run's score (game state).
- Run.second_chance_shown() -> bool: True while the "another chance?" offer is shown.
- Run.theme_state() -> str: Name of the theme the run takes place in (game state).

## Settings: The settings popup, opened from the main menu.
- Settings.cancel_delete() -> None: Answer NO to the delete confirmation.
- Settings.close() -> None: Close the popup (the game saves settings here).
- Settings.confirm_delete() -> None: Answer YES to the delete confirmation.
- Settings.confirmation_shown() -> bool: True while the delete-data confirmation question is shown.
- Settings.delete_data() -> None: Press Delete data; returns once the confirmation question is shown.
- Settings.is_shown() -> bool: True when the settings popup is open.
- Settings.set_master_volume(level: float) -> None: Move the master slider to `level` (0.0 to 1.0); the game applies it at once.
- Settings.set_music_volume(level: float) -> None: Move the music slider to `level` (0.0 to 1.0).
- Settings.set_sound_effects_volume(level: float) -> None: Move the sound-effects slider to `level` (0.0 to 1.0).
- Settings.volumes_shown() -> Volumes: Positions of the master, music and sound-effects sliders (0.0 to 1.0).

## Setup: Arrange-only helpers that change the player's data directly.
- Setup.complete_mission(index: int) -> None: Make the active mission at `index` (0 = first) complete by setting its progress to its target.
- Setup.complete_tutorial() -> None: Mark the tutorial as completed, so the next run is a normal run.
- Setup.set_coins(amount: int) -> None: Set the coin balance to `amount` (>= 0) and save.
- Setup.set_premium(amount: int) -> None: Set the premium balance to `amount` (>= 0) and save.

## Store: The store, opened from the main menu or the game-over screen.
- Store.accessories -> StoreSection: The accessories section; rows are grouped under the character they belong to.
- Store.characters -> StoreSection: The characters section.
- Store.close() -> None: Close the store; the screen it was opened from comes back.
- Store.coins_shown() -> int: The coin balance displayed in the store.
- Store.is_shown() -> bool: True when the store is the screen in front.
- Store.power_ups -> StoreSection: The power-ups section (store tab "Items"): Magnet, x2, Invincible, Life.
- Store.premium_shown() -> int: The premium balance displayed in the store.
- Store.sections_shown() -> list[str]: Labels of the section tabs, in display order.
- Store.themes -> StoreSection: The themes section.

## StoreSection: One section (tab) of the store: power-ups, characters, accessories or themes.
- StoreSection.buy(name: str, character: str | None = None) -> PurchaseResult: Press Buy on the row `name` and report what the store showed before and after.
- StoreSection.item(name: str, character: str | None = None) -> StoreItem: The row named `name` (in the accessories section also give the `character` it belongs to).
- StoreSection.items() -> list[StoreItem]: Every row of this section in display order, as shown.
- StoreSection.open() -> None: Show this section.

## Records (fields)
- HudReadout(coins: int, distance_m: int, lives: int, multiplier: int, premium: int, score: int): The run HUD's values as shown on screen.
- LeaderboardEntry(name: str, rank: int, score: int): One leaderboard line: its rank as shown, the player name and the score.
- Mission(claimable: bool, description: str, progress: int | None, reward: int, target: int | None): One mission as listed in the missions popup.
- MissionState(complete: bool, progress: float, reward: int, target: float): One active mission as stored in the player's data (model state).
- PurchaseResult(coins_after: int, coins_before: int, item_after: StoreItem, item_before: StoreItem, premium_after: int, premium_before: int): What the store showed just before and just after pressing Buy on one row.
- StoreItem(buy_enabled: bool, character: str | None, coin_price: int, coin_price_highlighted: bool, count: int | None, name: str, owned: bool, premium_price: int | None, premium_price_highlighted: bool): One row of a store section, as the player sees it.
- Volumes(master: float, music: float, sound_effects: float): Volume levels on the settings sliders' scale: 0.0 (silent) to 1.0 (full).

## Enums and errors
- Screen: START, MAIN_MENU, STORE, MISSIONS, SETTINGS, LEADERBOARD, RUN, PAUSE_MENU, SECOND_CHANCE, GAME_OVER, UNKNOWN
- PGError: Base class of every error raised by pg_sdk.
- PGGameError: The game logged an error or exception while the test ran (raised by the pytest plugin).
- PGInfraError: The device chain failed: driver connection, adb, or the app not running. Never a kill.
- PGTimeout: A post-condition did not hold in time (screen not shown, item not listed, ...).

Importable from pg_sdk: Game, HudReadout, LeaderboardEntry, Mission, MissionState, PGError, PGGameError, PGInfraError, PGTimeout, PurchaseResult, Screen, StoreItem, Volumes
