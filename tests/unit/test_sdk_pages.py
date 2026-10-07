"""pg_sdk page logic against an in-memory fake of the game UI."""

from __future__ import annotations

from typing import Any

import pytest

from pg_sdk import PGTimeout, Screen
from pg_sdk._locators import LocatorError, Locators
from pg_sdk._screens import screen_shown
from pg_sdk._ui import is_red, parse_fraction, parse_int
from pg_sdk.missions import Missions
from pg_sdk.player import Player
from pg_sdk.run import Run
from pg_sdk.settings import Settings
from pg_sdk.setup import Setup, SetupError
from pg_sdk.store import Store
from tests.unit.sdk_fakes import OFF, FakeClock, FakeDriver, loc, make_ui

BUTTON = ("UnityEngine.UI.Button", "interactable")
TEXT_COLOR = ("UnityEngine.UI.Text", "color")
RED = {"r": 1.0, "g": 0.0, "b": 0.0, "a": 1.0}
BLACK = {"r": 0.0, "g": 0.0, "b": 0.0, "a": 1.0}


# --- helpers ------------------------------------------------------------------------------


def test_parsers() -> None:
    assert parse_int("750", "x") == 750
    assert parse_int("x 3", "x") == 3
    assert parse_int("120m", "x") == 120
    assert parse_int("-1", "x") == -1
    with pytest.raises(ValueError, match="no number"):
        parse_int("", "price")
    assert parse_fraction("12 / 2000") == (12, 2000)
    assert parse_fraction("CLAIM REWARD") is None
    assert is_red(RED)
    assert not is_red(BLACK)
    assert not is_red(None)


def test_locators_extend_and_fail_loudly(tmp_path: Any) -> None:
    (tmp_path / "base.yaml").write_text("build_tag: base\nlocators:\n  a: /A\n  b: /B\n")
    (tmp_path / "next.yaml").write_text("build_tag: next\nextends: base\nlocators:\n  b: /B2\n")
    (tmp_path / "loop.yaml").write_text("build_tag: loop\nextends: loop\nlocators: {}\n")
    (tmp_path / "bad.yaml").write_text("build_tag: other\nlocators: {}\n")
    merged = Locators.load("next", tmp_path)
    assert (merged["a"], merged["b"]) == ("/A", "/B2")
    with pytest.raises(LocatorError, match="no locator 'c'"):
        merged["c"]
    with pytest.raises(LocatorError, match="cycle"):
        Locators.load("loop", tmp_path)
    with pytest.raises(LocatorError, match="build_tag"):
        Locators.load("bad", tmp_path)
    with pytest.raises(LocatorError, match="no locator file"):
        Locators.load("missing", tmp_path)


# --- screens and waits --------------------------------------------------------------------


def test_screen_detection_prefers_overlays_over_the_main_menu() -> None:
    driver = FakeDriver()
    ui = make_ui(driver)
    assert screen_shown(ui) is Screen.UNKNOWN
    driver.add(loc("main_menu.store_button"))
    assert screen_shown(ui) is Screen.MAIN_MENU
    driver.add(loc("store.close"))
    assert screen_shown(ui) is Screen.STORE
    driver.show(loc("store.close"), False)
    driver.add(loc("missions.game_over.root") + "/" + loc("missions.close"))
    assert screen_shown(ui) is Screen.MISSIONS


def test_timeout_names_what_and_where() -> None:
    driver = FakeDriver()
    driver.add(loc("main_menu.store_button"))
    clock = FakeClock()
    ui = make_ui(driver, clock)
    with pytest.raises(PGTimeout) as info:
        ui.wait_until("the store", lambda: False)
    assert info.value.timeout_s == 2.0
    assert "main_menu" in str(info.value)
    assert clock.now >= 2.0


def test_press_waits_for_an_enabled_button() -> None:
    driver = FakeDriver()
    button = driver.add("/B", props={BUTTON: False})
    ui = make_ui(driver)
    with pytest.raises(PGTimeout, match="close button to be enabled"):
        ui.press("/B", "close button")
    button.props[BUTTON] = True
    ui.press("/B", "close button")
    assert driver.taps == ["/B"]
    assert ui.actions == ["tap close button"]


# --- store --------------------------------------------------------------------------------


def add_store(driver: FakeDriver, coins: int, premium: int) -> dict[str, int]:
    balance = {"coins": coins, "premium": premium}
    driver.add(loc("store.close"))
    driver.add(loc("store.coins"), str(coins))
    driver.add(loc("store.premium"), str(premium))
    for key in ("power_ups", "characters", "accessories", "themes"):
        driver.add(loc(f"store.list.{key}"), pos=(500.0, 500.0) if key == "power_ups" else OFF)
    return balance


def add_row(
    driver: FakeDriver,
    section: str,
    index: int,
    name: str,
    coin: int,
    premium: int | None = None,
    *,
    count: str = "0",
    label: str = "BUY",
    enabled: bool = True,
    red: bool = False,
) -> str:
    row = f"{loc(f'store.list.{section}')}/Container/ItemEntry(Clone)#{index}"
    driver.add(row)
    driver.add(f"{row}/{loc('row.name')}", name)
    driver.add(
        f"{row}/{loc('row.coin_price')}", str(coin), props={TEXT_COLOR: RED if red else BLACK}
    )
    if premium is not None:
        driver.add(f"{row}/{loc('row.premium_price')}", str(premium), props={TEXT_COLOR: BLACK})
    driver.add(f"{row}/{loc('row.count')}", count)
    driver.add(f"{row}/{loc('row.buy_button')}", props={BUTTON: enabled})
    driver.add(f"{row}/{loc('row.buy_label')}", label)
    return row


def test_store_rows_are_read_as_shown() -> None:
    driver = FakeDriver()
    add_store(driver, coins=700, premium=0)
    add_row(driver, "power_ups", 0, "Magnet", 750, enabled=False, red=True)
    add_row(driver, "power_ups", 1, "Life", 2000, 5, count="2")
    store = Store(make_ui(driver))
    assert store.coins_shown() == 700
    magnet, life = store.power_ups.items()
    assert (magnet.coin_price, magnet.premium_price, magnet.count) == (750, None, 0)
    assert magnet.coin_price_highlighted
    assert not magnet.buy_enabled
    assert (life.premium_price, life.count, life.buy_enabled) == (5, 2, True)
    with pytest.raises(PGTimeout, match="store item 'Rocket'"):
        store.power_ups.item("Rocket")


def test_accessories_are_grouped_by_their_character_header() -> None:
    driver = FakeDriver()
    add_store(driver, 0, 0)
    driver.show(loc("store.list.power_ups"), False)
    driver.show(loc("store.list.accessories"))
    base = loc("store.list.accessories") + "/Container"
    driver.add(f"{base}/Header(Clone)#0")
    driver.add(f"{base}/Header(Clone)#0/{loc('store.header.name')}", "Trash Cat")
    add_row(driver, "accessories", 1, "Safety", 1500, 5)
    driver.add(f"{base}/Header(Clone)#2")
    driver.add(f"{base}/Header(Clone)#2/{loc('store.header.name')}", "Rubbish Raccoon")
    add_row(driver, "accessories", 3, "Safety", 20000, 10)
    section = Store(make_ui(driver)).accessories
    items = section.items()
    assert [(i.character, i.coin_price) for i in items] == [
        ("Trash Cat", 1500),
        ("Rubbish Raccoon", 20000),
    ]
    assert section.item("Safety", "Rubbish Raccoon").premium_price == 10


def test_buy_reports_the_change_the_store_showed() -> None:
    driver = FakeDriver()
    add_store(driver, coins=1000, premium=0)
    row = add_row(driver, "power_ups", 0, "Magnet", 750)

    def purchase() -> None:
        driver.nodes[loc("store.coins")].text = "250"
        driver.nodes[f"{row}/{loc('row.count')}"].text = "1"

    driver.nodes[f"{row}/{loc('row.buy_button')}"].on_tap = purchase
    result = Store(make_ui(driver)).power_ups.buy("Magnet")
    assert (result.coins_before, result.coins_after) == (1000, 250)
    assert (result.item_before.count, result.item_after.count) == (0, 1)


def test_refused_purchase_shows_no_change() -> None:
    driver = FakeDriver()
    add_store(driver, coins=100, premium=0)
    row = add_row(driver, "power_ups", 0, "Magnet", 750, enabled=False, red=True)
    driver.nodes[f"{row}/{loc('row.buy_button')}"].on_tap = lambda: pytest.fail("disabled")
    result = Store(make_ui(driver)).power_ups.buy("Magnet")
    assert result.coins_after == result.coins_before == 100
    assert result.item_after == result.item_before


# --- missions -----------------------------------------------------------------------------


def add_missions(driver: FakeDriver, root_key: str = "missions.main_menu.root") -> str:
    root = loc(root_key)
    driver.add(f"{root}/{loc('missions.close')}", props={BUTTON: True})
    entries = f"{root}/{loc('missions.entries')}"
    for i, (desc, reward, progress) in enumerate(
        [("Jump 75", "3", None), ("Run 2000m", "2", "12 / 2000")]
    ):
        entry = f"{entries}#{i}"
        driver.add(entry)
        driver.add(f"{entry}/{loc('mission.description')}", desc)
        driver.add(f"{entry}/{loc('mission.reward')}", reward)
        if progress is None:
            driver.add(f"{entry}/{loc('mission.claim')}", props={BUTTON: True})
        else:
            driver.add(f"{entry}/{loc('mission.progress')}", progress)
    return root


def test_missions_are_listed_with_progress_and_claimability() -> None:
    driver = FakeDriver()
    add_missions(driver)
    done, running = Missions(make_ui(driver)).missions_shown()
    assert (done.claimable, done.progress, done.reward) == (True, None, 3)
    assert (running.claimable, running.progress, running.target) == (False, 12, 2000)


def test_missions_close_times_out_when_the_button_is_disabled() -> None:
    driver = FakeDriver()
    root = add_missions(driver, "missions.game_over.root")
    driver.nodes[f"{root}/{loc('missions.close')}"].props[BUTTON] = False
    with pytest.raises(PGTimeout, match="missions close button to be enabled"):
        Missions(make_ui(driver)).close()


def test_claim_rejects_an_index_out_of_range() -> None:
    driver = FakeDriver()
    add_missions(driver)
    with pytest.raises(ValueError, match="out of range"):
        Missions(make_ui(driver)).claim(5)


# --- settings, run, player, setup ---------------------------------------------------------


def test_volume_levels_are_validated_and_applied() -> None:
    driver = FakeDriver()
    driver.add(loc("settings.close"))
    for key in ("master", "music", "sound_effects"):
        driver.add(loc(f"settings.{key}"), props={("UnityEngine.UI.Slider", "value"): 1.0})
    settings = Settings(make_ui(driver))
    with pytest.raises(ValueError, match=r"between 0.0 and 1.0"):
        settings.set_music_volume(1.5)
    settings.set_music_volume(0.25)
    assert settings.volumes_shown().music == 0.25


def test_lives_are_the_white_hearts() -> None:
    driver = FakeDriver()
    white = {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}
    for i, color in enumerate([white, white, BLACK]):
        driver.add(
            f"/UICamera/Game/WholeUI/Life/Image{i}",
            props={("UnityEngine.UI.Image", "color"): color},
        )
    assert Run(make_ui(driver)).lives_shown() == 2


def test_player_state_is_mapped_to_store_names_and_slider_scale() -> None:
    driver = FakeDriver()
    driver.statics[("PlayerData", "instance.consumables")] = {"1": 2, "EXTRALIFE": 1}
    driver.statics[("PlayerData", "instance.musicVolume")] = -40.0
    driver.statics[("PlayerData", "instance.masterVolume")] = 0.0
    driver.statics[("PlayerData", "instance.masterSFXVolume")] = -80.0
    driver.statics[("PlayerData", "instance.characters")] = ["Trash Cat", "Rubbish Raccoon"]
    driver.statics[("PlayerData", "instance.usedCharacter")] = 1
    player = Player(make_ui(driver))
    assert player.power_ups_state() == {"Magnet": 2, "Life": 1}
    volumes = player.volumes_state()
    assert (volumes.master, volumes.music, volumes.sound_effects) == (1.0, 0.5, 0.0)
    assert player.selected_character_state() == "Rubbish Raccoon"


def test_setup_writes_verifies_and_saves() -> None:
    driver = FakeDriver()
    driver.statics[("PlayerData", "instance.coins")] = 0

    def apply(type_name: str, member: str, value: Any) -> None:
        driver.statics[(type_name, member.replace("m_Instance", "instance"))] = value

    driver.set_static_hook = apply
    setup = Setup(make_ui(driver))
    setup.set_coins(5000)
    assert driver.static_writes == [("PlayerData", "m_Instance.coins", 5000)]
    assert driver.static_calls == [("PlayerData", "m_Instance.Save")]
    with pytest.raises(ValueError, match=">= 0"):
        setup.set_premium(-1)


def test_setup_reports_a_write_that_did_not_take() -> None:
    driver = FakeDriver()
    driver.statics[("PlayerData", "instance.coins")] = 0
    with pytest.raises(SetupError, match="did not change"):
        Setup(make_ui(driver)).set_coins(10)
    assert driver.static_calls == []


def test_complete_mission_tolerates_the_server_error_but_checks_the_value() -> None:
    driver = FakeDriver()
    driver.statics[("PlayerData", "instance.missions")] = [{"max": 75.0}, {"max": 3.0}]
    progress = {"value": 0.0}
    driver.statics[("PlayerData", "instance.missions[1].progress")] = lambda: progress["value"]

    def apply_then_fail(type_name: str, member: str, value: Any) -> None:
        progress["value"] = value
        raise RuntimeError("Object of type 'X' cannot be converted to type 'PlayerData'")

    driver.set_static_hook = apply_then_fail
    Setup(make_ui(driver)).complete_mission(1)
    assert progress["value"] == 3.0
    assert driver.static_calls == [("PlayerData", "m_Instance.Save")]

    driver.set_static_hook = lambda *_: None
    progress["value"] = 0.0
    with pytest.raises(SetupError, match=r"expected 3\.0"):
        Setup(make_ui(driver)).complete_mission(1)
    with pytest.raises(ValueError, match="no active mission"):
        Setup(make_ui(driver)).complete_mission(2)
