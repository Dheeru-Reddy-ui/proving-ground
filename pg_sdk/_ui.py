"""Low-level UI access shared by the pages: element lookup, visibility, taps, bounded waits.

`Driver` is the narrow interface pages need; `pg_sdk._driver.AltTesterSession` implements it on
the phone and unit tests use a fake. Nothing here is part of the public SDK.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pg_sdk._locators import Locators
from pg_sdk.errors import PGTimeout

ASSEMBLY = "Assembly-CSharp"
UI_ASSEMBLY = "UnityEngine.UI"


@dataclass(frozen=True)
class Element:
    """An active object found by path, with its screen position."""

    id: int
    name: str
    x: float
    y: float


class Driver(Protocol):
    def find(self, path: str) -> Element | None: ...

    def find_all(self, path: str) -> list[Element]: ...

    def text(self, path: str) -> str | None: ...

    def tap(self, path: str) -> bool: ...

    def component_property(self, path: str, component: str, prop: str, assembly: str) -> Any: ...

    def set_component_property(
        self, path: str, component: str, prop: str, assembly: str, value: Any
    ) -> None: ...

    def static_property(self, type_name: str, member: str, assembly: str) -> Any: ...

    def set_static_property(
        self, type_name: str, member: str, assembly: str, value: Any
    ) -> None: ...

    def call_static_method(
        self, type_name: str, method: str, assembly: str, parameters: list[str] | None = None
    ) -> Any: ...

    def current_scene(self) -> str: ...

    def loaded_scenes(self) -> list[str]: ...

    def screen_size(self) -> tuple[int, int]: ...

    def swipe(
        self, start: tuple[float, float], end: tuple[float, float], duration_s: float
    ) -> None: ...

    def screenshot(self, path: Path) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Timeouts:
    action_s: float = 10.0  # a screen or element appearing after a tap
    settle_s: float = 3.0  # a purchase or claim showing its effect
    run_s: float = 150.0  # a run lasting until all lives are lost
    poll_s: float = 0.25


@dataclass
class Ui:
    driver: Driver
    locators: Locators
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    timeouts: Timeouts = field(default_factory=Timeouts)
    actions: list[str] = field(default_factory=list)
    _size: tuple[int, int] | None = None

    # --- paths ----------------------------------------------------------------------------

    def path(self, key: str) -> str:
        return self.locators[key]

    def child(self, element: Element, key: str) -> str:
        """Path of a locator (relative path) below a specific element, anchored on its id."""
        return f"//*[@id={element.id}]/{self.locators[key]}"

    # --- reads ----------------------------------------------------------------------------

    def size(self) -> tuple[int, int]:
        if self._size is None:
            self._size = self.driver.screen_size()
        return self._size

    def on_screen(self, element: Element | None) -> bool:
        """Active and inside the screen. Hidden panels stay active but sit off-screen."""
        if element is None:
            return False
        width, height = self.size()
        return 0 <= element.x <= width and 0 <= element.y <= height

    def visible(self, path: str) -> bool:
        return self.on_screen(self.driver.find(path))

    def text_at(self, path: str) -> str | None:
        return self.driver.text(path)

    # --- actions --------------------------------------------------------------------------

    def log(self, action: str) -> None:
        self.actions.append(action)

    def tap(self, path: str, what: str) -> None:
        """Tap the element at `path` once it is on screen, enabled or not (a disabled button
        simply ignores the tap, which is what purchase-refusal checks rely on)."""
        self.wait_until(f"{what} to be shown", lambda: self.visible(path))
        if not self.driver.tap(path):
            self.wait_until(f"{what} to be shown", lambda: self.driver.tap(path))
        self.log(f"tap {what}")

    def interactable(self, path: str) -> bool:
        """A Button's `interactable` flag; True for elements without a Button component."""
        try:
            value = self.driver.component_property(
                path, "UnityEngine.UI.Button", "interactable", UI_ASSEMBLY
            )
        except LookupError:
            return True
        return value is True

    def press(self, path: str, what: str) -> None:
        """Tap a button once it is on screen and enabled (navigation and confirmations)."""
        self.wait_until(f"{what} to be shown", lambda: self.visible(path))
        self.wait_until(f"{what} to be enabled", lambda: self.interactable(path))
        self.tap(path, what)

    # --- waits ----------------------------------------------------------------------------

    def wait_until(
        self, what: str, condition: Callable[[], bool], timeout_s: float | None = None
    ) -> None:
        limit = self.timeouts.action_s if timeout_s is None else timeout_s
        deadline = self.clock() + limit
        while not condition():
            if self.clock() >= deadline:
                raise PGTimeout(what, limit, self.describe_screen())
            self.sleep(self.timeouts.poll_s)

    def wait_for_change(
        self, read: Callable[[], object], before: object, timeout_s: float | None = None
    ) -> bool:
        """Poll `read` until it differs from `before`; False if it never did within the limit."""
        limit = self.timeouts.settle_s if timeout_s is None else timeout_s
        deadline = self.clock() + limit
        while read() == before:
            if self.clock() >= deadline:
                return False
            self.sleep(self.timeouts.poll_s)
        return True

    def describe_screen(self) -> str:
        """Short context for timeouts: the scene and the screen in front."""
        from pg_sdk._screens import screen_shown  # _screens imports this module

        try:
            return f"scene {self.driver.current_scene()}, {screen_shown(self).value}"
        except Exception as exc:  # context only; never mask the timeout itself
            return f"unavailable ({type(exc).__name__})"


_INT = re.compile(r"-?\d+")


def parse_int(text: str | None, what: str) -> int:
    """First integer in a UI text ("750", "x1", "x 3", "120m", "-1")."""
    match = _INT.search(text or "")
    if match is None:
        raise ValueError(f"{what}: no number in {text!r}")
    return int(match.group())


def parse_fraction(text: str | None) -> tuple[int, int] | None:
    """`"12 / 2000"` -> (12, 2000); None when the text is not a fraction."""
    numbers = _INT.findall(text or "")
    if "/" not in (text or "") or len(numbers) != 2:
        return None
    return int(numbers[0]), int(numbers[1])


def is_red(color: Any) -> bool:
    """The store marks unaffordable prices with Color.red; normal prices are black."""
    if not isinstance(color, dict):
        return False
    return float(color.get("r", 0)) > 0.9 and float(color.get("g", 1)) < 0.1
