# Game hooks: seeded bugs and telemetry

> **Evaluation material.** This file names every seeded bug and its flag. It must never reach an LLM prompt (rule 3 in `CLAUDE.md`); the generator's prompt-leak test checks that.

The instrumented TrashCat build carries 16 seeded bugs, each behind a runtime flag, so one APK serves the clean build and every bug variant (Phase 1 design principle 3). Our own C# lives in `game/unity_scripts/`; the hooks are small edits to the game's scripts in the Unity project at `PG_GAME_PROJECT_DIR`. This file describes each edit by file, method and the statement it attaches to. **It never reproduces Asset Store code**; the snippets below are ours.

## Install

1. Copy `game/unity_scripts/PGBugFlags.cs` and `game/unity_scripts/PGTelemetry.cs` into `Assets/Scripts/PG/` of the Unity project. They compile into `Assembly-CSharp`, the assembly AltTester's `call_static_method` is pointed at.
2. Apply the 16 edits in [Hook points](#hook-points). Every edited line ends with `// PG hook SBxx`, so `grep -rn "PG hook" Assets/Scripts` lists them all.
3. Build the APK as in `game/BUILD_TRASHCAT.md` and record its sha256 in `docs/VERSIONS.md`.

The original scripts are restored by re-importing the Asset Store package; a local backup of each edited file is also kept under `artifacts/hook_backups/` (gitignored, never committed).

## `PGBugFlags`: the switches

| Member | Behaviour |
|---|---|
| `Configure(string csvFlagIds)` | Replaces the active set with the comma-separated flag IDs (an empty string clears it), saves it to PlayerPrefs and logs `PGFLAGS <sorted csv>`. |
| `On(string id)` | True when the flag is active. Every hook asks this. |
| `Active()` | The active flags, sorted and comma-separated; `""` when none. |

- **Why the set is saved:** SB08 and SB10 run inside `PlayerData.Read`, which happens at app start, before a driver can connect. A `[RuntimeInitializeOnLoadMethod(BeforeSceneLoad)]` method reloads the saved set (and logs `PGFLAGS`) before the first scene. `adb shell pm clear` deletes PlayerPrefs with the rest of the app's data, so every reset starts with no flags; the pytest plugin then calls `Configure` and checks `Active()`.
- **Threading:** AltTester runs every command from `AltRunner.Update` through its response queue (`AltTester/Runtime/Commands/AltRunner.cs`, `CommandHandler.OnMessage` → `AltRunner._responseQueue.ScheduleResponse`), so `Configure` runs on Unity's main thread, as PlayerPrefs requires.
- **Stripping:** classes and methods carry `UnityEngine.Scripting.PreserveAttribute` ([`[Preserve]`](https://docs.unity3d.com/2021.3/Documentation/Manual/ManagedCodeStripping.html): "Preserves the method, the type that declares the method, the type the method returns, and the types of all of its arguments"). The build uses Managed Stripping Level **Minimal**, at which "Unity doesn't remove any user-written code" (same page), so `[Preserve]` is a second safeguard. [`RuntimeInitializeOnLoadMethodAttribute`](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/RuntimeInitializeOnLoadMethodAttribute.html) inherits from `PreserveAttribute`; load types are listed on [`RuntimeInitializeLoadType`](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/RuntimeInitializeLoadType.html).

## `PGTelemetry`: performance lines

Created at startup by a `[RuntimeInitializeOnLoadMethod(AfterSceneLoad)]` method on a `DontDestroyOnLoad` object named `PGTelemetry`. Every 5 s of real time it logs one line:

```
PGTELEM {"v":1,"ts":"2026-10-08T09:30:00.000Z","t":42.17,"scene":"Main","window_s":5.00,"frames":300,"dropped":0,"frame_ms_p50":16.67,"frame_ms_p95":17.10,"frame_ms_p99":24.90,"mem_managed_bytes":51234816,"time_scale":1.00}
```

- Frame times use `Time.unscaledDeltaTime`, because the game sets the time scale to 0 while paused and tests may speed it up.
- Percentiles are nearest-rank over a preallocated buffer of 4,096 samples; frames beyond that are counted in `dropped`. Nothing is allocated per frame; sorting and the log line happen once per window.
- Managed memory is `System.GC.GetTotalMemory(false)` ([.NET docs](https://learn.microsoft.com/dotnet/api/system.gc.gettotalmemory)), which does not force a collection.

## Hook points

Categories match `benchmark/bugs.yaml`. "Edit" names the statement the hook attaches to; the code shown is the line we add or the guard we put in front of the existing statement.

| ID | Flag | Category | Feature | File → method | Edit | Observable symptom |
|---|---|---|---|---|---|---|
| SB01 | `sb_item_double_charge` | price_charged_wrong | store | `UI/Shop/ShopItemList.cs` → `Buy` | After the statement that subtracts the coin price: `if (PGBugFlags.On("sb_item_double_charge")) { PlayerData.instance.coins -= c.GetPrice(); }` | Buying a power-up takes twice its coin price (Magnet 750 → coins drop by 1,500). |
| SB02 | `sb_character_premium_not_charged` | price_charged_wrong | store | `UI/Shop/ShopCharacterList.cs` → `Buy` | Guard the statement that subtracts the premium price: `if (!PGBugFlags.On("sb_character_premium_not_charged"))` | Buying a character leaves the premium balance unchanged (Rubbish Raccoon costs 20 premium). |
| SB03 | `sb_theme_not_granted` | purchase_not_granted | store | `UI/Shop/ShopThemeList.cs` → `Buy` | Guard the `AddTheme` call: `if (!PGBugFlags.On("sb_theme_not_granted"))` | Buying a theme charges the price but the theme is not owned: the row is not "Owned" and the theme cannot be selected. |
| SB04 | `sb_accessory_not_granted` | purchase_not_granted | store | `UI/Shop/ShopAccessoriesList.cs` → `Buy` | Guard the `AddAccessory` call: `if (!PGBugFlags.On("sb_accessory_not_granted"))` | Buying an accessory charges the price but the accessory is not owned. |
| SB05 | `sb_character_coin_check_skipped` | purchase_insufficient_funds | store | `UI/Shop/ShopCharacterList.cs` → `RefreshButton` | Coin affordability condition becomes `c.cost > PlayerData.instance.coins && !PGBugFlags.On("sb_character_coin_check_skipped")` | A character the player cannot afford in coins has an enabled Buy button and a non-red price; buying it drives coins negative. |
| SB06 | `sb_item_affordable_one_short` | purchase_insufficient_funds | store | `UI/Shop/ShopItemList.cs` → `RefreshButton` | Coin affordability condition becomes `c.GetPrice() > PlayerData.instance.coins + (PGBugFlags.On("sb_item_affordable_one_short") ? 1 : 0)` | With exactly one coin less than a power-up's price, Buy is enabled; buying leaves coins at -1. |
| SB07 | `sb_character_purchase_not_saved` | progress_not_persisted | persistence | `UI/Shop/ShopCharacterList.cs` → `Buy` | Guard the `Save()` call: `if (!PGBugFlags.On("sb_character_purchase_not_saved"))` | A bought character and the coins spent on it revert after the game is closed and reopened (unless something else saved in between). |
| SB08 | `sb_tutorial_done_not_loaded` | progress_not_persisted | persistence | `PlayerData.cs` → `Read` | Inside the version-12 block, after the tutorial flag is read: `if (PGBugFlags.On("sb_tutorial_done_not_loaded")) { tutorialDone = false; }` | After the tutorial is completed, closing and reopening the game shows the tutorial again (tutorial overlay on the main menu; the next run is the tutorial). |
| SB09 | `sb_settings_not_saved_on_close` | settings_not_persisted | settings | `UI/Settings/SettingPopup.cs` → `Close` | Guard the `Save()` call: `if (!PGBugFlags.On("sb_settings_not_saved_on_close"))` | Volume changes are lost when the game is closed and reopened. |
| SB10 | `sb_volumes_swapped_on_load` | settings_not_persisted | settings | `PlayerData.cs` → `Read` | Inside the version-9 block, after the three volumes are read: `if (PGBugFlags.On("sb_volumes_swapped_on_load")) { float pgMusic = musicVolume; musicVolume = masterSFXVolume; masterSFXVolume = pgMusic; }` | After reopening the game, the music and sound-effects sliders show each other's values. |
| SB11 | `sb_mission_reward_doubled` | mission_reward | missions | `PlayerData.cs` → `ClaimMission` | After the statement that adds the reward: `if (PGBugFlags.On("sb_mission_reward_doubled")) { premium += mission.reward; }` | Claiming a mission adds twice its premium reward. |
| SB12 | `sb_mission_reward_not_granted` | mission_reward | missions | `PlayerData.cs` → `ClaimMission` | Guard the statement that adds the reward: `if (!PGBugFlags.On("sb_mission_reward_not_granted"))` | Claiming a mission removes it but adds no premium. |
| SB13 | `sb_gameover_shows_coins` | gameover_wrong_value | run_and_gameover | `GameManager/GameOverState.cs` → `Enter` | After the statement that writes the score into the mini leaderboard's player entry: `if (PGBugFlags.On("sb_gameover_shows_coins")) { miniLeaderboard.playerEntry.score.text = trackManager.characterController.coins.ToString(); }` | The game-over screen shows the run's coin count where the final score belongs. |
| SB14 | `sb_run_ignores_selected_character` | selection_not_applied | character_select | `Tracks/TrackManager.cs` → `Begin` | Before the player is spawned: `int pgCharacter = PlayerData.instance.usedCharacter; if (PGBugFlags.On("sb_run_ignores_selected_character")) { pgCharacter = 0; }`; the spawn call's character index becomes `pgCharacter` | The run always uses the first owned character (Trash Cat), whatever the main menu selected. |
| SB15 | `sb_missions_close_locked` | soft_lock | missions | `UI/MissionUI.cs` → `Open` | After the popup is activated: `if (PGBugFlags.On("sb_missions_close_locked")) { Transform pgClose = transform.Find("MissionBackground/CloseButton"); if (pgClose != null) { pgClose.GetComponent<UnityEngine.UI.Button>().interactable = false; } }` | The missions popup's close button does nothing, so the player is stuck on the popup. |
| SB16 | `sb_accessories_tab_throws` | error_log | store | `UI/Shop/ShopUI.cs` → `OpenAccessoriesList` | At the end of the method: `if (PGBugFlags.On("sb_accessories_tab_throws")) { throw new System.InvalidOperationException("Accessory list owner not resolved"); }` | Opening the Accessories tab logs an exception; the tab still opens and works. |

**Notes on individual hooks**
- **SB13** shows coins rather than distance: the score grows by one point per unit of distance at multiplier 1 (`TrackManager.AddScore`), so a distance-for-score mix-up would be invisible in short runs.
- **SB14** is safe to combine with any accessory: `Character.SetupAccesory` only loops over the spawned character's own accessories.
- **SB15**'s path is relative to the `MissionUI` object (`/UICamera/Loadout/MissionPopup` and `/UICamera/GameOver/MissionPopup`); taken from `docs/evidence/phase0/scenes/scene_Main_missions_popup.json`.
- **SB16** throws inside a UI click handler; Unity's event system catches and logs the exception, and the tab has already switched.

## Verification

Each bug has a symptom test in `tests/device/test_seeded_bug_symptoms.py` that checks the intended behaviour. `pg bugs verify` runs it on build `87d396162a05` once clean and once with the bug's flag on; a bug is verified when the clean run passes, the flagged run fails in a product-visible way (assertion, `PGTimeout` or a logged game error), and the game reports exactly that flag through `Active()` and a `PGFLAGS` log line. This table is generated from the evidence file, runs `verify-20261008T061828-9b4660`, `verify-20261008T061923-fd97b9` (2026-10-08): [`docs/evidence/phase1/bug_symptoms_87d396162a05.json`](../docs/evidence/phase1/bug_symptoms_87d396162a05.json).

| ID | Flag toggles (`Active()` + `PGFLAGS`) | Clean build | Flag on | Verified |
|---|---|---|---|---|
| SB01 | yes | passed | assertion | yes |
| SB02 | yes | passed | assertion | yes |
| SB03 | yes | passed | assertion | yes |
| SB04 | yes | passed | assertion | yes |
| SB05 | yes | passed | assertion | yes |
| SB06 | yes | passed | assertion | yes |
| SB07 | yes | passed | assertion | yes |
| SB08 | yes | passed | assertion | yes |
| SB09 | yes | passed | assertion | yes |
| SB10 | yes | passed | assertion | yes |
| SB11 | yes | passed | assertion | yes |
| SB12 | yes | passed | assertion | yes |
| SB13 | yes | passed | assertion | yes |
| SB14 | yes | passed | assertion | yes |
| SB15 | yes | passed | pg_timeout | yes |
| SB16 | yes | passed | game_error | yes |
