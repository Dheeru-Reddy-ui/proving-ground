"""One test per seeded bug, checking the intended behaviour the bug breaks (device only).

EVALUATION MATERIAL: these tests were written knowing the bug catalog. They exist only to
confirm that each hook shows its symptom (`pg bugs verify`): each must pass on the clean build
and fail with its bug's flag on. They are never part of a suite and never reach a prompt.
Test names start with the bug ID they verify.
"""

from __future__ import annotations

import pytest

from pg_sdk import Game, Screen

pytestmark = pytest.mark.device


def _buy_raccoon(game: Game) -> None:
    game.setup.set_coins(60000)
    game.setup.set_premium(30)
    game.main_menu.open_store()
    result = game.store.characters.buy("Rubbish Raccoon")
    assert result.item_after.owned


def test_sb01_power_up_costs_its_price(game: Game) -> None:
    game.setup.set_coins(1000)
    game.main_menu.open_store()
    price = game.store.power_ups.item("Magnet").coin_price
    result = game.store.power_ups.buy("Magnet")
    assert result.coins_after == result.coins_before - price


def test_sb02_character_costs_its_premium_price(game: Game) -> None:
    game.setup.set_coins(60000)
    game.setup.set_premium(30)
    game.main_menu.open_store()
    premium = game.store.characters.item("Rubbish Raccoon").premium_price
    result = game.store.characters.buy("Rubbish Raccoon")
    assert premium is not None
    assert result.premium_after == result.premium_before - premium


def test_sb03_bought_theme_is_owned(game: Game) -> None:
    game.setup.set_coins(2000)
    game.setup.set_premium(20)
    game.main_menu.open_store()
    result = game.store.themes.buy("NightTime")
    assert result.coins_after < result.coins_before
    assert result.item_after.owned
    game.store.close()
    assert game.main_menu.theme_arrows_shown()


def test_sb04_bought_accessory_is_owned(game: Game) -> None:
    game.setup.set_coins(2000)
    game.setup.set_premium(10)
    game.main_menu.open_store()
    result = game.store.accessories.buy("Safety", "Trash Cat")
    assert result.coins_after < result.coins_before
    assert any(a.endswith("Safety") for a in game.player.owned_accessories_state())
    game.store.close()
    assert game.main_menu.accessory_shown() is not None


def test_sb05_unaffordable_character_cannot_be_bought(game: Game) -> None:
    game.setup.set_coins(100)
    game.setup.set_premium(30)
    game.main_menu.open_store()
    raccoon = game.store.characters.item("Rubbish Raccoon")
    assert not raccoon.buy_enabled
    assert raccoon.coin_price_highlighted


def test_sb06_power_up_one_coin_short_cannot_be_bought(game: Game) -> None:
    game.setup.set_coins(749)
    game.main_menu.open_store()
    magnet = game.store.power_ups.item("Magnet")
    assert magnet.coin_price == 750
    assert not magnet.buy_enabled
    result = game.store.power_ups.buy("Magnet")
    assert result.coins_after == 749


def test_sb07_bought_character_survives_a_restart(game: Game) -> None:
    _buy_raccoon(game)
    game.store.close()
    game.restart()
    game.main_menu.open_store()
    assert game.store.characters.item("Rubbish Raccoon").owned


def test_sb08_completed_tutorial_stays_completed(game: Game) -> None:
    game.setup.complete_tutorial()
    game.restart()
    assert not game.main_menu.tutorial_shown()


def test_sb09_volume_survives_a_restart(game: Game) -> None:
    game.main_menu.open_settings()
    game.settings.set_music_volume(0.25)
    game.settings.close()
    game.restart()
    game.main_menu.open_settings()
    assert game.settings.volumes_shown().music == pytest.approx(0.25, abs=0.01)


def test_sb10_music_and_effects_volumes_stay_apart(game: Game) -> None:
    game.main_menu.open_settings()
    game.settings.set_music_volume(0.25)
    game.settings.set_sound_effects_volume(0.9)
    game.settings.close()
    game.restart()
    game.main_menu.open_settings()
    volumes = game.settings.volumes_shown()
    assert volumes.music == pytest.approx(0.25, abs=0.01)
    assert volumes.sound_effects == pytest.approx(0.9, abs=0.01)


def _claim_first_mission(game: Game) -> int:
    game.setup.complete_mission(0)
    game.main_menu.open_missions()
    reward = game.missions.missions_shown()[0].reward
    game.missions.claim(0)
    game.missions.close()
    return reward


def test_sb11_mission_reward_is_added_once(game: Game) -> None:
    reward = _claim_first_mission(game)
    game.main_menu.open_store()
    assert game.store.premium_shown() == reward


def test_sb12_mission_reward_is_added(game: Game) -> None:
    reward = _claim_first_mission(game)
    game.main_menu.open_store()
    assert game.store.premium_shown() > 0
    assert game.store.premium_shown() == reward


def test_sb13_game_over_shows_the_run_score(game: Game) -> None:
    game.setup.complete_tutorial()
    game.main_menu.start_run()
    game.run.play_until_out_of_lives()
    score = game.run.score_state()
    game.run.decline_continue()
    if game.missions.is_shown():
        game.missions.close()
    assert game.game_over.score_shown() == score


def test_sb14_selected_character_runs(game: Game) -> None:
    _buy_raccoon(game)
    game.store.close()
    assert game.main_menu.next_character() == "Rubbish Raccoon"
    game.setup.complete_tutorial()
    game.main_menu.start_run()
    assert game.run.character_state() == "Rubbish Raccoon"


def test_sb15_missions_popup_closes(game: Game) -> None:
    game.main_menu.open_missions()
    game.missions.close()
    assert game.screen_shown() is Screen.MAIN_MENU


def test_sb16_accessories_section_opens_cleanly(game: Game) -> None:
    game.main_menu.open_store()
    assert len(game.store.accessories.items()) > 0
