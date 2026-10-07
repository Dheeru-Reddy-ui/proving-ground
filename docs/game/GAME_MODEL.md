# Game model: TrashCat

**Status: draft from the source pass (2026-10-07).** Screen paths and on-device behaviour are added from `pg spike dump` evidence once the instrumented APK runs (M0.4/M0.5).

**How to read this file:**
- Each claim cites a game script and member, by name only: Asset Store code is never pasted here. Scripts are under `Assets/Scripts/` in the Unity project (`PG_GAME_PROJECT_DIR`).
- **INFERRED** means read from the code but not yet observed on the phone.

## Scenes and game states

| Scene | What it holds | Evidence |
|---|---|---|
| `Start` | Licence popup and the Start button | Build settings list `Start`, `Main`, `Shop` (`ProjectSettings/EditorBuildSettings.asset`); `UI/LicenceDisplayer.cs`, `UI/StartButton.cs` |
| `Main` | `GameManager` state machine: `Loadout` → `Game` → `GameOver` | `GameManager/GameManager.cs` (`SwitchState`, `PushState`, `topState`); state names from each state's `GetName()` |
| `Shop` | Store, loaded **additively** on top of `Main` | `LoadoutState.GoToStore`, `GameOverState.GoToStore`; closed by `ShopUI.CloseScene` (unloads `shop`) |

- The tutorial is not a separate scene. It is the `Game` state with `isTutorial` set while `PlayerData.tutorialDone` is false (`GameState`, `TrackManager.isTutorial`).
- `GameState.FinishTutorial` sets `tutorialDone = true`.

## Screens

AltTester paths come from the scene dumps; until then the column reads "pending".

| Screen | How to reach it | Key interactions (handler → effect) | AltTester paths |
|---|---|---|---|
| Licence popup | First launch after `pm clear` | **Accept** → `LicenceDisplayer.Accepted` (sets `licenceAccepted`, saves, closes). **Refuse** → `Application.Quit()`: **never press**. Hidden on later launches once accepted. INFERRED | pending |
| Start | App launch | **Start** → `StartButton.StartGame` (first press sets `ftueLevel` 0→1 and saves), then loads `main` | pending |
| Loadout | `Main`, state `Loadout` | Store → `GoToStore`. Character ◀▶ → `ChangeCharacter(±1)`. Accessory ◀▶ → `ChangeAccessory`. Theme ◀▶ → `ChangeTheme`. Power-up ◀▶ → `ChangeConsumable`, `UnequipPowerup`. Leaderboard → `Openleaderboard`. Run → `StartGame` (state `Game`). A `tutorialBlocker` object is active while `tutorialDone` is false (`LoadoutState`); **what it covers must be observed**. | pending |
| Missions popup | From loadout | Lists `PlayerData.missions` (always topped up to 2, `CheckMissionsCount`). **Claim** → `MissionUI.Claim` → `PlayerData.ClaimMission`: premium += the mission's reward, the mission is replaced, then saved. INFERRED | pending |
| Settings popup | From loadout | Master, music and SFX volume sliders → `SettingPopup.*VolumeChangeValue` (sets the mixer and `PlayerData` volume fields); **saved on Close**. **Delete data** → `DataDeleteConfirmation`: Confirm → `PlayerData.NewSave()` (wipes progress), Deny closes. INFERRED | pending |
| Leaderboard | Loadout or game over | Shows `PlayerData.highscores` (`HighscoreUI`, `Leaderboard`) | pending |
| Shop | Loadout → Store, or Game over → Store | Tabs: Items (power-ups) → `OpenItemList`, Characters → `OpenCharacterList`, Accessories → `OpenAccessoriesList`, Themes → `OpenThemeList`. Each row has a price, an optional premium price and a **Buy** button (`ShopItemListItem.buyButton`). Coin and premium counters (`ShopUI.coinCounter`, `premiumCounter`) are refreshed every frame from `PlayerData`. Close → `ShopUI.CloseScene`. | pending |
| Run HUD | Loadout → Run | **Pause** → `GameState.Pause`; the pause menu has **Resume** → `Resume` and **Quit** → `QuitToLoadout`. Lives: 3 (`k_MaxLives`). Score, coin and premium counters (field names from the dump). | pending |
| Second chance | All lives lost | `OpenGameOverPopup`: **Premium for life** → `PremiumForLife` costs 3 premium; its button is interactable only when premium ≥ 3. Rewarded-ad option (`ShowRewardedAd`; services are off). **Game over** → `GameOver`. INFERRED | pending |
| Game over | After the second-chance popup | Leaderboard → `OpenLeaderboard`, Store → `GoToStore`, Loadout → `GoToLoadout`, Run again → `RunAgain`. The run's highscore is inserted and the save written (`FinishRun`, `CreditCoins`). INFERRED | pending |

## Store rules (from the code)

- **Buying** subtracts the coin price **and** the premium price, adds the item, then saves (`ShopItemList.Buy`, `ShopCharacterList.Buy`, `ShopThemeList.Buy`, `ShopAccessoriesList.Buy`).
- **Affordability:** a row's Buy button is not interactable when its price exceeds the player's coins or its premium price exceeds the player's premium. The unaffordable price text turns red (`RefreshButton` in each list).
- **Ownership:** an owned character shows **"Owned"** with a disabled button (`ShopCharacterList.RefreshButton`). Themes and accessories follow the same pattern (INFERRED until observed).
- Prices live in the item data (characters, themes, accessories, consumables), not in code. They are read from the shop UI at runtime.

## State: `PlayerData` (`Assets/Scripts/PlayerData.cs`)

Model reads use AltTester `get_static_property("PlayerData", "instance.<field>", "Assembly-CSharp")`. AltTester 2.3.2's server resolves dotted static paths (`AltReflectionMethodsCommand`); this is confirmed on the phone by `pg spike reset`.

| Field | Meaning | First launch (`NewSave`, field defaults) | Read it in the UI | Model path |
|---|---|---|---|---|
| `coins` | Soft currency | 0 | Shop coin counter | `instance.coins` |
| `premium` | Premium currency | 0 | Shop premium counter | `instance.premium` |
| `consumables` | Owned power-ups and counts | empty | Shop Items tab, loadout power-up selector | `instance.consumables` |
| `characters` | Owned characters | `["Trash Cat"]` | Shop Characters tab ("Owned") | `instance.characters` |
| `usedCharacter` | Selected character (index) | 0 | Loadout name text (`LoadoutState.charNameDisplay`) | `instance.usedCharacter` |
| `characterAccessories`, `usedAccessory` | Owned accessories (`"char:acc"`), selected one | empty, -1 | Shop Accessories tab, loadout | `instance.characterAccessories`, `instance.usedAccessory` |
| `themes`, `usedTheme` | Owned themes, selected one | `["Day"]`, 0 | Shop Themes tab, loadout | `instance.themes`, `instance.usedTheme` |
| `missions` | Active missions | 2 random missions (`CheckMissionsCount`, `AddMission`) | Missions popup | `instance.missions` |
| `highscores` | Leaderboard entries | empty | Leaderboard | `instance.highscores` |
| `licenceAccepted` | Licence popup accepted | false | Licence popup visible or not | `instance.licenceAccepted` |
| `tutorialDone` | Tutorial completed | false | `tutorialBlocker` visible or not | `instance.tutorialDone` |
| `ftueLevel` | First-time-user progress | 0 | none | `instance.ftueLevel` |
| `masterVolume`, `musicVolume`, `masterSFXVolume` | Volume settings | `float.MinValue` ("not set"). INFERRED: the mixer defaults are used | Settings sliders | `instance.masterVolume`, … |

### When coins and premium change

- **Coins:**
  - Run pickups: +1 per coin, added to `PlayerData` immediately (`CharacterCollider`).
  - Shop purchases subtract the price.
  - The development-build cheat adds coins (below).
- **Premium:**
  - Run pickups: +1 each (`CharacterCollider`).
  - Mission claims add the mission's reward (`ClaimMission`).
  - Shop purchases subtract the premium price.
  - Premium-for-life subtracts 3 (`GameState.PremiumForLife`).
  - The development-build cheat adds premium.
- The save file is written on purchases, mission claims, licence accept, first Start press, settings close and game over. INFERRED: run pickups reach disk only at the next save.

## Persistence

- All progress is one binary file: `Application.persistentDataPath + "/save.bin"` (`PlayerData.Create`, `Save`, `Read`; format version 12).
- The game scripts make no `PlayerPrefs` calls. The AltTester SDK may keep its own connection settings there.
- `pm clear` deletes the app's data, so the next launch creates a new save with the first-launch values above. INFERRED until `pg spike reset` confirms it on the phone.

## Determinism

- The track layout comes from `TrackManager.trackSeed`, a settable property. Phase 1 can pin it so runs repeat.
- Missions are random (`Random.Range` in `Missions.cs`): after each reset the two missions differ in type and target. Tests must read the mission shown, not assume one.

## Never press (tests and the Phase 3 crawler)

| Control | Why |
|---|---|
| Licence **Refuse** | Quits the app (`Application.Quit`) |
| Settings → **Delete data** → **Confirm** | Wipes all progress (`PlayerData.NewSave`). Only a test written for that feature may press it, and only on purpose. |
| Shop cheat (`ShopUI.CheatCoin`, if a button is wired to it) | Adds 1,000,000 coins and 1,000 premium in development builds. Setup helpers may call it; generated tests must not, or they would mask price bugs. |
| Rewarded-ad and in-app-purchase buttons | Services are disabled in this build |

## Development-build helpers (test setup only)

- `ShopUI.CheatCoin()` is active in development builds: it is disabled only when the build is neither the editor nor a development build. Instrumented builds are development builds (`game/BUILD_TRASHCAT.md` step 6), so it adds 1,000,000 coins and 1,000 premium, then saves.
- `PlayerData.GiveCoins` and `PlayerData.AddConsumables` are editor-only (inside `#if UNITY_EDITOR`) and do not exist on the phone.
