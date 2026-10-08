# Writing the human baseline suite

The human baseline (`suites/human_baseline/`) is written by Dheeru only (CLAUDE.md rule 4). It is benchmark arm A: what a person writes from the specs with the same SDK the generator uses. Claude never creates or edits files there; it only runs them.

## What to write

- **10–15 tests**, from `specs/*.md` only. Do not look at `benchmark/bugs.yaml`, `game/HOOKS.md` or any proving output first: the baseline must not be written against the seeded bugs.
- Cover at least the store and one more feature; spread the rest over what you think matters.
- Any number of tests per file, files named `test_*.py`.

## How a test looks

```python
import pytest
from pg_sdk import Game


@pytest.mark.spec("STORE-5")
def test_buying_magnet_charges_its_price(game: Game) -> None:
    game.setup.set_coins(1000)  # arrange through setup helpers
    game.main_menu.open_store()
    price = game.store.power_ups.item("Magnet").coin_price
    result = game.store.power_ups.buy("Magnet")
    assert result.coins_after == result.coins_before - price  # what the player saw
    assert game.store.coins_shown() == 1000 - price
```

- The `game` fixture starts every test on the main menu of a new game (data cleared).
- Use only `pg_sdk`: the full API with one-line docs is in `pg_sdk/manifest.json` (or run `uv run python -c "from pg_generator.prompt import sdk_reference; import json; print(sdk_reference(json.load(open('pg_sdk/manifest.json'))))"`).
- `*_shown()` reads the screen; `*_state()` reads game data. Assert on the screen; cross-check data if you like.
- `game.setup.*` arranges state (coins, premium, tutorial done, a completed mission) and saves it. Set balances before opening the store.
- For a normal run: `game.setup.complete_tutorial()` before `game.main_menu.start_run()`, then `game.run.play_until_out_of_lives()`.
- `game.restart()` closes and reopens the game with its data kept.
- Mark each test with `@pytest.mark.spec(...)`. The same G1 rules are not enforced on your tests, but they are a good guide: no sleeps, no `try`, assertions that can fail.

## How to run them

- One file, on the phone: `uv run pg run-test suites/human_baseline/test_store.py` (runs the file's tests through the same sandboxed runner; add `--runs 3` to check stability).
- The whole baseline through the gates' runs (3 clean, then 2 per relevant dev bug), recording kills in the database: `uv run pg baseline`.

The baseline is run before proving generated candidates, so G4 (novelty) compares candidates against it.
