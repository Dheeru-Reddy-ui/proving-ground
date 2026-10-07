TrashCat is a mobile endless runner. The player runs along a track, collects coins (fishbones) and premium currency, and loses a life on each obstacle hit.

Screens and flow:
- A test starts on the main menu of a brand-new game: 0 coins, 0 premium, character Trash Cat and theme Day owned and selected, two random missions, tutorial not completed (a tutorial overlay covers the main menu).
- From the main menu the player opens the store, the missions popup, the settings popup or the leaderboard, or starts a run.
- The store has four sections: Items (power-ups: Magnet, x2, Invincible, Life), Characters (Trash Cat, Rubbish Raccoon), Accessories (grouped under the character they belong to; names repeat across characters) and Themes (Day, NightTime). Rows show a coin price, a premium price for some items, and a Buy button or "Owned". Power-up rows show how many the player owns.
- Prices seen in this build: Magnet 750, x2 750, Invincible 1500 + 5 premium, Life 2000 + 5 premium, Rubbish Raccoon 50000 + 20 premium, NightTime 1000 + 15 premium. Read prices from the store rather than hard-coding them when the test does not depend on a specific value.
- The character, theme and power-up arrows on the main menu appear only when the player owns more than one option.
- A run on a new game is the tutorial, which waits for swipes; call `game.setup.complete_tutorial()` before `game.main_menu.start_run()` for a normal run. In a normal run the runner hits obstacles by itself, so `game.run.play_until_out_of_lives()` ends the run in about 20-60 seconds and shows the "another chance?" offer (continue for 3 premium, or Game over).
- After the game-over screen appears, the game may open the missions popup over it when a mission was completed; close it with `game.missions.close()` first.
- Missions are random: read them from `game.missions.missions_shown()` instead of assuming one. `game.setup.complete_mission(index)` makes an active mission complete.
- `game.restart()` closes and reopens the game keeping saved data, like a player quitting and coming back, and returns to the main menu.

Test setup:
- `game.setup.*` writes the player's data directly (coins, premium, tutorial, mission progress) and saves it. Use it only to arrange the starting state, never as the thing you check.
- Set balances before opening the store: the store computes which rows are affordable when a section opens.
