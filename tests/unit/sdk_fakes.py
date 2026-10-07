"""An in-memory stand-in for the phone, implementing pg_sdk._ui.Driver for unit tests."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pg_sdk._locators import Locators
from pg_sdk._ui import Element, Timeouts, Ui

ON = (500.0, 500.0)
OFF = (500.0, -622.0)  # hidden panels sit below the screen


@dataclass
class Node:
    path: str
    id: int
    text: str | None = None
    pos: tuple[float, float] = ON
    props: dict[tuple[str, str], Any] = field(default_factory=dict)
    on_tap: Callable[[], None] | None = None


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeDriver:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.statics: dict[tuple[str, str], Any] = {}
        self.static_writes: list[tuple[str, str, Any]] = []
        self.static_calls: list[tuple[str, str]] = []
        self.set_static_hook: Callable[[str, str, Any], None] | None = None
        self.taps: list[str] = []
        self.scene = "Main"
        self._next_id = -100

    # --- building -------------------------------------------------------------------------

    def add(self, path: str, text: str | None = None, **kwargs: Any) -> Node:
        self._next_id -= 2
        node = Node(path=path, id=self._next_id, text=text, **kwargs)
        self.nodes[path] = node
        return node

    def remove(self, prefix: str) -> None:
        for path in [p for p in self.nodes if p == prefix or p.startswith(prefix + "/")]:
            del self.nodes[path]

    def show(self, path: str, shown: bool = True) -> None:
        self.nodes[path].pos = ON if shown else OFF

    # --- Driver ---------------------------------------------------------------------------

    def _match(self, path: str) -> list[Node]:
        anchored = re.fullmatch(r"//\*\[@id=(-?\d+)\](?:/(.*))?", path)
        if anchored:
            base = next((n.path for n in self.nodes.values() if n.id == int(anchored[1])), None)
            if base is None:
                return []
            path = base + ("/" + anchored[2] if anchored[2] else "")
        pattern = (
            "^"
            + "/".join("[^/]+" if part == "*" else re.escape(part) for part in path.split("/"))
            + "$"
        )
        # "#n" only disambiguates same-named siblings in this fake; the game has no such suffix.
        # A path anchored on an id names one exact object, so it keeps the suffix.
        name = (lambda p: p) if anchored else _plain
        return [n for p, n in self.nodes.items() if re.match(pattern, name(p))]

    def find(self, path: str) -> Element | None:
        found = self._match(path)
        return None if not found else _element(found[0])

    def find_all(self, path: str) -> list[Element]:
        return [_element(n) for n in self._match(path)]

    def text(self, path: str) -> str | None:
        found = self._match(path)
        return found[0].text if found else None

    def tap(self, path: str) -> bool:
        found = self._match(path)
        if not found:
            return False
        self.taps.append(found[0].path)
        if found[0].on_tap is not None and found[0].props.get(
            ("UnityEngine.UI.Button", "interactable"), True
        ):
            found[0].on_tap()
        return True

    def component_property(self, path: str, component: str, prop: str, assembly: str) -> Any:
        found = self._match(path)
        if not found or (component, prop) not in found[0].props:
            raise LookupError(f"{component}.{prop} at {path}")
        return found[0].props[(component, prop)]

    def set_component_property(
        self, path: str, component: str, prop: str, assembly: str, value: Any
    ) -> None:
        self._match(path)[0].props[(component, prop)] = value

    def static_property(self, type_name: str, member: str, assembly: str) -> Any:
        value = self.statics[(type_name, member)]
        return value() if callable(value) else value

    def set_static_property(self, type_name: str, member: str, assembly: str, value: Any) -> None:
        self.static_writes.append((type_name, member, value))
        if self.set_static_hook is not None:
            self.set_static_hook(type_name, member, value)

    def call_static_method(
        self, type_name: str, method: str, assembly: str, parameters: list[str] | None = None
    ) -> Any:
        self.static_calls.append((type_name, method))
        return None

    def current_scene(self) -> str:
        return self.scene

    def loaded_scenes(self) -> list[str]:
        return [self.scene]

    def screen_size(self) -> tuple[int, int]:
        return (1440, 3120)

    def swipe(
        self, start: tuple[float, float], end: tuple[float, float], duration_s: float
    ) -> None:
        pass

    def screenshot(self, path: Path) -> None:
        pass

    def close(self) -> None:
        pass


def _plain(path: str) -> str:
    return re.sub(r"#\d+", "", path)


def _element(node: Node) -> Element:
    name = _plain(node.path).rsplit("/", 1)[-1]
    return Element(id=node.id, name=name, x=node.pos[0], y=node.pos[1])


def make_ui(driver: FakeDriver, clock: FakeClock | None = None) -> Ui:
    clock = clock or FakeClock()
    return Ui(
        driver=driver,
        locators=Locators.load("e63240052d1b"),
        clock=clock,
        sleep=clock.sleep,
        timeouts=Timeouts(action_s=2.0, settle_s=1.0, run_s=5.0, poll_s=0.25),
    )


def loc(key: str) -> str:
    return Locators.load("e63240052d1b")[key]
