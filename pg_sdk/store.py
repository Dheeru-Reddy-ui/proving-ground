"""The store: balances, its four sections, item rows and buying."""

from __future__ import annotations

from typing import Literal, cast

from pg_sdk._screens import screen_shown
from pg_sdk._ui import UI_ASSEMBLY, Element, Ui, is_red, parse_int
from pg_sdk.types import PurchaseResult, Screen, StoreItem

SectionKey = Literal["power_ups", "characters", "accessories", "themes"]


class _RowGone(Exception):
    """A row was rebuilt while being read; the read is retried."""


class StoreSection:
    """One section (tab) of the store: power-ups, characters, accessories or themes."""

    def __init__(self, ui: Ui, key: SectionKey) -> None:
        self._ui = ui
        self._key = key

    def open(self) -> None:
        """Show this section. Precondition: the store is open. Returns once its rows are listed."""
        _require_store(self._ui)
        if not self._is_open():
            self._ui.press(self._ui.path(f"store.tab.{self._key}"), f"{self._label} tab")
        self._ui.wait_until(f"{self._label} rows", lambda: self._is_open() and bool(self._rows()))

    def items(self) -> list[StoreItem]:
        """Every row of this section in display order, as shown. Opens the section first."""
        self.open()
        snapshot: list[list[StoreItem]] = [[]]

        def read() -> bool:
            try:
                snapshot[0] = [self._read_row(row, group) for row, group in self._rows()]
            except _RowGone:
                return False
            return bool(snapshot[0])

        self._ui.wait_until(f"a stable list of {self._label}", read)
        return snapshot[0]

    def item(self, name: str, character: str | None = None) -> StoreItem:
        """The row named `name` (in the accessories section also give the `character` it
        belongs to). Opens the section first; raises PGTimeout if no such row is listed."""
        self.open()
        found: list[StoreItem | None] = [None]

        def lookup() -> bool:
            found[0] = self._current(name, character)
            return found[0] is not None

        self._ui.wait_until(f"store item {_label_of(name, character)}", lookup)
        return cast(StoreItem, found[0])  # set by the successful wait above

    def buy(self, name: str, character: str | None = None) -> PurchaseResult:
        """Press Buy on the row `name` and report what the store showed before and after.

        The button is pressed even when it is disabled: a refused purchase then shows no
        change, after a few seconds of waiting for one. Opens the section first.
        """
        before = self.item(name, character)
        coins_before = _coins(self._ui)
        premium_before = _premium(self._ui)
        row: list[Element | None] = [None]

        def locate() -> bool:
            row[0] = self._find(name, character)
            return row[0] is not None

        self._ui.wait_until(f"store item {_label_of(name, character)}", locate)
        self._ui.tap(
            self._ui.child(cast(Element, row[0]), "row.buy_button"),
            f"Buy on {_label_of(name, character)}",
        )

        def state() -> tuple[int, int, StoreItem | None]:
            return _coins(self._ui), _premium(self._ui), self._current(name, character)

        if self._ui.wait_for_change(state, (coins_before, premium_before, before)):
            # Some sections rebuild their rows after a purchase; wait until two reads agree.
            last = [state()]

            def settled() -> bool:
                now = state()
                agreed = now == last[0] and now[2] is not None
                last[0] = now
                return agreed

            self._ui.wait_until("the store to settle after the purchase", settled)
        coins_after, premium_after, after = state()
        return PurchaseResult(
            item_before=before,
            item_after=after if after is not None else self.item(name, character),
            coins_before=coins_before,
            coins_after=coins_after,
            premium_before=premium_before,
            premium_after=premium_after,
        )

    # --- helpers --------------------------------------------------------------------------

    @property
    def _label(self) -> str:
        return self._key.replace("_", "-")

    def _list_path(self) -> str:
        return self._ui.path(f"store.list.{self._key}")

    def _is_open(self) -> bool:
        return self._ui.visible(self._list_path())

    def _rows(self) -> list[tuple[Element, str | None]]:
        """Item rows in display order, each with the header (character) it sits under."""
        ui = self._ui
        rows: list[tuple[Element, str | None]] = []
        header: str | None = None
        for entry in ui.driver.find_all(f"{self._list_path()}/{ui.path('store.list_entries')}"):
            if entry.name.startswith(ui.path("store.header_name_prefix")):
                header = ui.text_at(ui.child(entry, "store.header.name"))
            elif entry.name.startswith(ui.path("store.row_name_prefix")):
                rows.append((entry, header if self._key == "accessories" else None))
        return rows

    def _find(self, name: str, character: str | None) -> Element | None:
        for row, group in self._rows():
            if self._ui.text_at(self._ui.child(row, "row.name")) == name and (
                character is None or group == character
            ):
                return row
        return None

    def _current(self, name: str, character: str | None) -> StoreItem | None:
        for row, group in self._rows():
            if self._ui.text_at(self._ui.child(row, "row.name")) != name:
                continue
            if character is not None and group != character:
                continue
            try:
                return self._read_row(row, group)
            except _RowGone:
                return None
        return None

    def _read_row(self, row: Element, group: str | None) -> StoreItem:
        ui = self._ui
        premium_path = ui.child(row, "row.premium_price")
        premium_text = ui.text_at(premium_path)
        coin_path = ui.child(row, "row.coin_price")
        count_text = ui.text_at(ui.child(row, "row.count")) if self._key == "power_ups" else None
        return StoreItem(
            name=_need(ui.text_at(ui.child(row, "row.name"))),
            character=group,
            coin_price=parse_int(_need(ui.text_at(coin_path)), "coin price"),
            premium_price=parse_int(premium_text, "premium price") if premium_text else None,
            count=parse_int(count_text, "count") if count_text else None,
            owned=_need(ui.text_at(ui.child(row, "row.buy_label"))).strip().lower() == "owned",
            buy_enabled=ui.interactable(ui.child(row, "row.buy_button")),
            coin_price_highlighted=is_red(_color(ui, coin_path)),
            premium_price_highlighted=bool(premium_text) and is_red(_color(ui, premium_path)),
        )


class Store:
    """The store, opened from the main menu or the game-over screen."""

    def __init__(self, ui: Ui) -> None:
        self._ui = ui
        self.power_ups = StoreSection(ui, "power_ups")
        self.characters = StoreSection(ui, "characters")
        self.accessories = StoreSection(ui, "accessories")
        self.themes = StoreSection(ui, "themes")

    def is_shown(self) -> bool:
        """True when the store is the screen in front."""
        return screen_shown(self._ui) is Screen.STORE

    def coins_shown(self) -> int:
        """The coin balance displayed in the store. Precondition: store open."""
        _require_store(self._ui)
        return _coins(self._ui)

    def premium_shown(self) -> int:
        """The premium balance displayed in the store. Precondition: store open."""
        _require_store(self._ui)
        return _premium(self._ui)

    def sections_shown(self) -> list[str]:
        """Labels of the section tabs, in display order. Precondition: store open."""
        _require_store(self._ui)
        tabs = self._ui.driver.find_all(self._ui.path("store.tab_labels"))
        return [self._ui.text_at(f"//*[@id={tab.id}]") or "" for tab in tabs]

    def close(self) -> None:
        """Close the store; the screen it was opened from comes back. Precondition: store open."""
        _require_store(self._ui)
        self._ui.press(self._ui.path("store.close"), "store close button")
        self._ui.wait_until(
            "the store to close", lambda: screen_shown(self._ui) is not Screen.STORE
        )


def _require_store(ui: Ui) -> None:
    """Wait for the store to be in front with a section's rows built. The balance counters
    show placeholder text until the store's first frames have run, and by the time rows are
    listed they hold the real balances."""
    ui.wait_until("the store", lambda: store_ready(ui))


def store_ready(ui: Ui) -> bool:
    """True once the store is in front and one of its sections lists rows."""
    return screen_shown(ui) is Screen.STORE and _rows_listed(ui)


def _rows_listed(ui: Ui) -> bool:
    entries = ui.path("store.list_entries")
    return any(
        ui.driver.find_all(f"{ui.path(f'store.list.{key}')}/{entries}")
        for key in ("power_ups", "characters", "accessories", "themes")
    )


def _coins(ui: Ui) -> int:
    return parse_int(ui.text_at(ui.path("store.coins")), "store coins")


def _premium(ui: Ui) -> int:
    return parse_int(ui.text_at(ui.path("store.premium")), "store premium")


def _color(ui: Ui, path: str) -> object:
    try:
        return ui.driver.component_property(path, "UnityEngine.UI.Text", "color", UI_ASSEMBLY)
    except LookupError:
        return None


def _need(text: str | None) -> str:
    if text is None:
        raise _RowGone()
    return text


def _label_of(name: str, character: str | None) -> str:
    return f"{name!r}" + (f" for {character}" if character else "")
