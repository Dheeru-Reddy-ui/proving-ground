"""Live checks of pg_sdk against the phone. Never run in CI (no device there).

Run through the plugin with a run context, for example:
    uv run pg sdk-live   (writes the context and runs this file)
"""

from __future__ import annotations

import pytest

from pg_sdk import Game, Screen

pytestmark = pytest.mark.device


def test_fresh_game_main_menu(game: Game) -> None:
    assert game.screen_shown() is Screen.MAIN_MENU
    assert game.main_menu.character_shown() == "Trash Cat"
    assert game.main_menu.theme_shown() == "Day"
    assert game.main_menu.tutorial_shown()
    assert not game.main_menu.character_arrows_shown()
    assert game.player.coins_state() == 0
    assert game.player.owned_characters_state() == ["Trash Cat"]


def test_store_reads_and_buys(game: Game) -> None:
    game.setup.set_coins(60000)
    game.setup.set_premium(30)
    game.main_menu.open_store()
    store = game.store
    assert store.sections_shown() == ["Items", "Characters", "Accessories", "Themes"]
    assert store.coins_shown() == 60000
    magnet = store.power_ups.item("Magnet")
    assert magnet.coin_price == 750
    assert magnet.count == 0
    result = store.power_ups.buy("Magnet")
    assert result.coins_after == 60000 - 750
    assert result.item_after.count == 1
    raccoon = store.characters.buy("Rubbish Raccoon")
    assert raccoon.item_after.owned
    assert raccoon.premium_after == 30 - 20
    accessories = store.accessories.items()
    assert {i.character for i in accessories} == {"Trash Cat", "Rubbish Raccoon"}
    store.close()
    assert game.main_menu.character_arrows_shown()
    assert game.main_menu.next_character() == "Rubbish Raccoon"


def test_unaffordable_item_is_refused(game: Game) -> None:
    game.setup.set_coins(100)
    game.main_menu.open_store()
    magnet = game.store.power_ups.item("Magnet")
    assert not magnet.buy_enabled
    assert magnet.coin_price_highlighted
    result = game.store.power_ups.buy("Magnet")
    assert result.coins_after == 100
    assert result.item_after.count == 0


def test_mission_claim_and_settings(game: Game) -> None:
    game.setup.complete_mission(0)
    reward = game.player.missions_state()[0].reward
    game.main_menu.open_missions()
    missions = game.missions.missions_shown()
    assert missions[0].claimable
    assert missions[1].progress == 0
    game.missions.claim(0)
    assert game.player.premium_state() == reward
    assert len(game.missions.missions_shown()) == 2
    game.missions.close()
    game.main_menu.open_settings()
    game.settings.set_music_volume(0.25)
    assert game.settings.volumes_shown().music == pytest.approx(0.25, abs=1e-3)
    game.settings.close()
    assert game.player.volumes_state().music == pytest.approx(0.25, abs=1e-3)


def test_restart_keeps_purchases(game: Game) -> None:
    game.setup.set_coins(2000)
    game.main_menu.open_store()
    game.store.power_ups.buy("Magnet")
    game.store.close()
    game.restart()
    assert game.screen_shown() is Screen.MAIN_MENU
    assert game.player.coins_state() == 1250
    assert game.player.power_ups_state() == {"Magnet": 1}


def test_run_to_game_over(game: Game) -> None:
    game.setup.complete_tutorial()
    game.main_menu.start_run()
    assert game.run.lives_shown() == 3
    assert game.run.character_state() == "Trash Cat"
    game.run.pause()
    game.run.resume()
    game.run.play_until_out_of_lives()
    assert game.run.continue_cost_shown() == 3
    assert not game.run.continue_enabled()
    score = game.run.score_state()
    game.run.decline_continue()
    if game.missions.is_shown():
        game.missions.close()
    assert game.game_over.score_shown() == score
    game.game_over.go_to_main_menu()
    assert game.main_menu.is_shown()
