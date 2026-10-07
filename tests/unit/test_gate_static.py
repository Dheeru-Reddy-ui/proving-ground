"""G1 static gate: valid tests pass; adversarial samples are rejected with the right reason."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from pg_core.gates.static import Limits, check_static, parse_type, spec_ids_from_markdown

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "pg_sdk/manifest.json").read_text(encoding="utf-8"))
SPECS = spec_ids_from_markdown(p.read_text(encoding="utf-8") for p in (ROOT / "specs").glob("*.md"))

HEAD = "import pytest\nfrom pg_sdk import Game, PGTimeout, Screen\n\n\n"


def run(
    body: str,
    *,
    head: str = HEAD,
    marker: str = '@pytest.mark.spec("STORE-5")\n',
    limits: Limits | None = None,
):  # type: ignore[no-untyped-def]
    code = head + marker + dedent(body)
    return check_static(code, MANIFEST, SPECS, limits)


def codes(body: str, **kwargs: object) -> set[str]:
    result, _ = run(body, **kwargs)  # type: ignore[arg-type]
    return {r.code for r in result.reasons}


VALID_PURCHASE = """\
def test_buying_magnet_charges_its_price(game: Game) -> None:
    game.setup.set_coins(1000)
    game.main_menu.open_store()
    store = game.store
    price = store.power_ups.item("Magnet").coin_price
    result = store.power_ups.buy("Magnet")
    assert result.coins_after == result.coins_before - price
    assert store.coins_shown() == 1000 - price
    assert game.player.coins_state() == 1000 - price
"""


# --- valid tests pass ---------------------------------------------------------------------


def test_valid_purchase_test_passes_and_reports_pages() -> None:
    result, report = run(VALID_PURCHASE)
    assert result.passed, result.reasons
    assert report.test_name == "test_buying_magnet_charges_its_price"
    assert report.spec_ids == ("STORE-5",)
    assert set(report.pages_used) == {"setup", "main_menu", "store", "player"}
    assert "StoreSection.buy" in report.calls
    assert report.assertions == 3
    assert report.visible_assertions == 3  # the last one compares against a price read on screen


@pytest.mark.parametrize(
    "body",
    [
        # soft-lock check through pytest.raises
        """\
def test_missions_popup_closes(game: Game) -> None:
    game.main_menu.open_missions()
    game.missions.close()
    assert game.screen_shown() is Screen.MAIN_MENU
""",
        # loops, enumerate, comprehensions, builtins
        """\
def test_every_item_shows_a_price(game: Game) -> None:
    game.main_menu.open_store()
    for index, item in enumerate(game.store.power_ups.items()):
        assert item.coin_price > 0, f"row {index} has no price"
    names = [i.name for i in game.store.characters.items() if i.owned]
    assert "Trash Cat" in names
    assert len(game.store.sections_shown()) == 4
""",
        # persistence across a restart
        """\
def test_coins_survive_a_restart(game: Game) -> None:
    game.setup.set_coins(2000)
    game.main_menu.open_store()
    game.store.power_ups.buy("Magnet")
    game.store.close()
    game.restart()
    game.main_menu.open_store()
    assert game.store.coins_shown() == 1250
""",
        # pytest.raises around a screen action, keyword argument
        """\
def test_unknown_item_is_not_listed(game: Game) -> None:
    game.main_menu.open_store()
    with pytest.raises(PGTimeout):
        game.store.accessories.item(name="Crown", character="Trash Cat")
""",
    ],
)
def test_valid_tests_pass(body: str) -> None:
    result, _ = run(body)
    assert result.passed, result.reasons


def test_restart_counts_as_the_app_page() -> None:
    _, report = run(
        """\
def test_restart(game: Game) -> None:
    game.restart()
    assert game.main_menu.is_shown()
"""
    )
    assert set(report.pages_used) == {"app", "main_menu"}


# --- adversarial samples ------------------------------------------------------------------

TEST = "def test_x(game: Game) -> None:\n"


@pytest.mark.parametrize(
    ("label", "body", "kwargs", "expected"),
    [
        ("syntax error", TEST + "    assert (\n", {}, "syntax_error"),
        (
            "import os",
            TEST + "    assert game.main_menu.is_shown()\n",
            {"head": "import os\n" + HEAD},
            "banned_import",
        ),
        (
            "from subprocess",
            TEST + "    assert game.main_menu.is_shown()\n",
            {"head": "from subprocess import run\n" + HEAD},
            "banned_import",
        ),
        (
            "private sdk module",
            TEST + "    assert game.main_menu.is_shown()\n",
            {"head": "from pg_sdk._driver import connect\n" + HEAD},
            "banned_import",
        ),
        (
            "relative import",
            TEST + "    assert game.main_menu.is_shown()\n",
            {"head": "from . import helpers\n" + HEAD},
            "banned_import",
        ),
        (
            "unknown export",
            TEST + "    assert game.main_menu.is_shown()\n",
            {"head": "from pg_sdk import Cheats\n" + HEAD},
            "unknown_sdk_member",
        ),
        (
            "star import",
            TEST + "    assert game.main_menu.is_shown()\n",
            {"head": "from pg_sdk import *\n" + HEAD},
            "banned_import",
        ),
        (
            "time.sleep",
            TEST + "    time.sleep(5)\n    assert game.main_menu.is_shown()\n",
            {"head": "import time\n" + HEAD},
            "banned_import",
        ),
        ("eval", TEST + '    eval("1")\n    assert game.main_menu.is_shown()\n', {}, "banned_call"),
        (
            "exec",
            TEST + '    exec("x = 1")\n    assert game.main_menu.is_shown()\n',
            {},
            "banned_call",
        ),
        (
            "open",
            TEST + '    open("save.bin")\n    assert game.main_menu.is_shown()\n',
            {},
            "banned_call",
        ),
        (
            "__import__",
            TEST + '    __import__("os")\n    assert game.main_menu.is_shown()\n',
            {},
            "banned_private",
        ),
        (
            "getattr",
            TEST + '    getattr(game, "store")\n    assert game.main_menu.is_shown()\n',
            {},
            "banned_call",
        ),
        (
            "private attribute",
            TEST + "    game._ui.driver.tap('/x')\n    assert game.main_menu.is_shown()\n",
            {},
            "banned_private",
        ),
        ("dunder", TEST + "    assert game.__class__ is not None\n", {}, "banned_private"),
        (
            "hallucinated method",
            TEST + '    game.store.purchase("Magnet")\n    assert game.store.is_shown()\n',
            {},
            "unknown_sdk_member",
        ),
        (
            "hallucinated page",
            TEST + "    game.inventory.open()\n    assert game.main_menu.is_shown()\n",
            {},
            "unknown_sdk_member",
        ),
        (
            "hallucinated via variable",
            TEST
            + "    store = game.store\n    store.power_ups.refund('Magnet')\n    assert store.is_shown()\n",
            {},
            "unknown_sdk_member",
        ),
        (
            "hallucinated record field",
            TEST + '    item = game.store.power_ups.item("Magnet")\n    assert item.price == 750\n',
            {},
            "unknown_sdk_member",
        ),
        (
            "missing argument",
            TEST + "    game.store.power_ups.buy()\n    assert game.store.is_shown()\n",
            {},
            "bad_arguments",
        ),
        (
            "too many arguments",
            TEST
            + '    game.store.power_ups.buy("Magnet", "Trash Cat", 3)\n    assert game.store.is_shown()\n',
            {},
            "bad_arguments",
        ),
        (
            "unknown keyword",
            TEST
            + '    game.store.power_ups.buy("Magnet", qty=2)\n    assert game.store.is_shown()\n',
            {},
            "bad_arguments",
        ),
        (
            "star args",
            TEST
            + '    args = ["Magnet"]\n    game.store.power_ups.buy(*args)\n    assert game.store.is_shown()\n',
            {},
            "bad_arguments",
        ),
        (
            "assert True",
            TEST + "    game.main_menu.open_store()\n    assert True\n",
            {},
            "tautology_literal",
        ),
        (
            "assert f-string",
            TEST + '    game.main_menu.open_store()\n    assert f"{game.store.coins_shown()}"\n',
            {},
            "tautology_literal",
        ),
        (
            "assert tuple",
            TEST + '    assert (game.store.coins_shown() == 5, "msg")\n',
            {},
            "tautology_literal",
        ),
        (
            "self comparison",
            TEST + "    coins = game.store.coins_shown()\n    assert coins == coins\n",
            {},
            "tautology_self",
        ),
        (
            "never None",
            TEST + "    assert game.store.coins_shown() is not None\n",
            {},
            "tautology_never_none",
        ),
        (
            "no assertion",
            TEST + '    game.main_menu.open_store()\n    game.store.power_ups.buy("Magnet")\n',
            {},
            "no_assertion",
        ),
        (
            "only model state",
            TEST + "    game.setup.set_coins(100)\n    assert game.player.coins_state() == 100\n",
            {},
            "no_visible_assertion",
        ),
        (
            "while True",
            TEST + "    while True:\n        break\n    assert game.main_menu.is_shown()\n",
            {},
            "banned_while_true",
        ),
        (
            "try/except",
            TEST
            + "    try:\n        assert game.store.coins_shown() == 1\n    except AssertionError:\n        pass\n",
            {},
            "banned_try",
        ),
        (
            "two tests",
            TEST
            + "    assert game.main_menu.is_shown()\n\n\ndef test_y(game: Game) -> None:\n    assert game.main_menu.is_shown()\n",
            {},
            "one_test_function",
        ),
        (
            "module code",
            TEST + "    assert game.main_menu.is_shown()\n\n\nCOINS = 5\n",
            {},
            "banned_module_code",
        ),
        (
            "nested def",
            TEST
            + "    def helper() -> int:\n        return 1\n    assert game.main_menu.is_shown()\n",
            {},
            "banned_definition",
        ),
        (
            "global",
            TEST + "    global COINS\n    assert game.main_menu.is_shown()\n",
            {},
            "banned_scope",
        ),
        (
            "rebinding game",
            TEST + "    game = None\n    assert game.main_menu.is_shown()\n",
            {},
            "rebind_game",
        ),
        (
            "missing marker",
            TEST + "    assert game.main_menu.is_shown()\n",
            {"marker": ""},
            "missing_spec_marker",
        ),
        (
            "unknown spec",
            TEST + "    assert game.main_menu.is_shown()\n",
            {"marker": '@pytest.mark.spec("STORE-99")\n'},
            "unknown_spec",
        ),
        (
            "skip marker",
            TEST + "    assert game.main_menu.is_shown()\n",
            {"marker": '@pytest.mark.skip\n@pytest.mark.spec("STORE-1")\n'},
            "banned_decorator",
        ),
        (
            "pytest.skip",
            TEST + '    pytest.skip("flaky")\n    assert game.main_menu.is_shown()\n',
            {},
            "banned_pytest",
        ),
        (
            "extra fixture",
            "def test_x(game: Game, tmp_path) -> None:\n    assert game.main_menu.is_shown()\n",
            {},
            "fixture_signature",
        ),
        (
            "unknown enum member",
            TEST + "    assert game.screen_shown() is Screen.SHOP\n",
            {},
            "unknown_sdk_member",
        ),
        (
            "unknown helper name",
            TEST + "    wait_for_store(game)\n    assert game.store.is_shown()\n",
            {},
            "unknown_name",
        ),
        (
            "too many actions",
            TEST
            + "    game.main_menu.open_store()\n    game.store.close()\n    assert game.main_menu.is_shown()\n",
            {"limits": Limits(max_actions=2)},
            "too_many_actions",
        ),
        (
            "too long",
            TEST + "    assert game.main_menu.is_shown()\n",
            {"limits": Limits(max_lines=3)},
            "too_long",
        ),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) < 30 else "",
)
def test_adversarial_samples_are_rejected(
    label: str, body: str, kwargs: dict[str, object], expected: str
) -> None:
    found = codes(body, **kwargs)
    assert expected in found, f"{label}: expected {expected}, got {sorted(found)}"


def test_rejection_names_the_hallucinated_member() -> None:
    result, _ = run(TEST + '    game.store.purchase("Magnet")\n    assert game.store.is_shown()\n')
    assert any("`Store` has no member `purchase`" in r.message for r in result.reasons)


def test_parse_type() -> None:
    t = parse_type("list[StoreItem]")
    assert (t.name, t.args[0].name) == ("list", "StoreItem")
    optional = parse_type("str | None")
    assert (optional.name, optional.optional) == ("str", True)
    mapping = parse_type("dict[str, int]")
    assert [a.name for a in mapping.args] == ["str", "int"]


def test_spec_ids_from_markdown_skips_retired() -> None:
    text = "- STORE-1: open\n- STORE-2: (retired) gone\nnot a spec\n"
    assert spec_ids_from_markdown([text]) == {"STORE-1"}


def test_repository_specs_are_found() -> None:
    assert {"STORE-1", "PERSIST-8", "RUN-14", "SET-7", "MISSION-8", "CHAR-10"} <= SPECS
