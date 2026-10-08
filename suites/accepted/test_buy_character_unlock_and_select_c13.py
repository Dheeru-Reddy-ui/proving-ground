# Accepted by Proving Ground (ACCEPT).
# candidate 13, generation run 5, model gemini-3.5-flash, prompt generate_tests_v2. Generated code: do not edit by hand.
import pytest
from pg_sdk import Game

@pytest.mark.spec("STORE-5", "STORE-7", "STORE-10")
def test_buy_character_unlock_and_select(game: Game):
    game.setup.set_coins(50000)
    game.setup.set_premium(25)
    
    game.main_menu.open_store()
    game.store.characters.open()
    
    res = game.store.characters.buy("Rubbish Raccoon")
    
    assert res.coins_before == 50000
    assert res.coins_after == 0
    assert res.premium_before == 25
    assert res.premium_after == 5
    
    assert not res.item_before.owned
    assert res.item_after.owned
    
    game.store.close()
    
    assert game.main_menu.character_arrows_shown()
    assert game.main_menu.character_shown() == "Trash Cat"
    
    next_char = game.main_menu.next_character()
    assert next_char == "Rubbish Raccoon"
    assert game.player.selected_character_state() == "Rubbish Raccoon"