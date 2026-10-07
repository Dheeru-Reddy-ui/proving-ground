"""Unit tests for AltTesterSession methods used by the spike, with a fake driver."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from alttester.commands.Notifications.notification_type import NotificationType

from pg_sdk._driver import AltTesterSession, LogLine


class FakeObject:
    def __init__(self, id_: int, name: str) -> None:
        self.id = id_
        self.name = name

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}

    def get_all_components(self) -> list[dict[str, str]]:
        return [{"componentName": "UnityEngine.RectTransform", "assemblyName": "UnityEngine"}]


class FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.listeners: dict[Any, Any] = {}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def get_all_elements(self, **kwargs: Any) -> list[FakeObject]:
        self._record("get_all_elements", **kwargs)
        return [FakeObject(1, "Canvas"), FakeObject(2, "Button")]

    def get_png_screenshot(self, path: str) -> None:
        self._record("get_png_screenshot", path)

    def add_notification_listener(self, kind: Any, callback: Any, overwrite: bool = True) -> None:
        self._record("add_notification_listener", kind, overwrite=overwrite)
        self.listeners[kind] = callback

    def remove_notification_listener(self, kind: Any) -> None:
        self._record("remove_notification_listener", kind)

    def call_static_method(self, *args: Any, **kwargs: Any) -> str:
        self._record("call_static_method", *args, **kwargs)
        return "null"

    def get_static_property(self, *args: Any, **kwargs: Any) -> int:
        self._record("get_static_property", *args, **kwargs)
        return 0

    def get_time_scale(self) -> float:
        return 1.0

    def set_time_scale(self, scale: float) -> None:
        self._record("set_time_scale", scale)

    def wait_for_current_scene_to_be(self, name: str, **kwargs: Any) -> None:
        self._record("wait_for_current_scene_to_be", name, **kwargs)


def test_dump_includes_inactive_objects_and_components() -> None:
    driver = FakeDriver()
    raw, components = AltTesterSession(driver).dump_elements(with_components=True)
    assert driver.calls[0] == ("get_all_elements", (), {"enabled": False})
    assert raw == [{"id": 1, "name": "Canvas"}, {"id": 2, "name": "Button"}]
    assert components == {1: ["UnityEngine.RectTransform"], 2: ["UnityEngine.RectTransform"]}


def test_dump_survives_objects_that_vanish_before_components_are_read() -> None:
    from alttester import exceptions as alt

    class Vanishing(FakeObject):
        def get_all_components(self) -> list[dict[str, str]]:
            raise alt.NotFoundException("Object not found")

    class Driver(FakeDriver):
        def get_all_elements(self, **kwargs: Any) -> list[FakeObject]:
            return [FakeObject(1, "Canvas"), Vanishing(2, "CharacterPreview")]

    raw, components = AltTesterSession(Driver()).dump_elements(with_components=True)
    assert len(raw) == 2
    assert components == {1: ["UnityEngine.RectTransform"], 2: None}


def test_dump_without_components() -> None:
    _, components = AltTesterSession(FakeDriver()).dump_elements(with_components=False)
    assert components is None


def test_screenshot_creates_folder_and_passes_a_string_path(tmp_path: Path) -> None:
    driver = FakeDriver()
    target = tmp_path / "new" / "shot.png"
    AltTesterSession(driver).screenshot(target)
    assert target.parent.is_dir()
    assert driver.calls == [("get_png_screenshot", (str(target),), {})]


def test_log_listener_registers_once_and_relays_lines() -> None:
    driver = FakeDriver()
    received: list[LogLine] = []
    session = AltTesterSession(driver)
    session.add_log_listener(received.append)
    assert driver.calls[0] == (
        "add_notification_listener",
        (NotificationType.LOG,),
        {"overwrite": False},
    )
    driver.listeners[NotificationType.LOG](
        SimpleNamespace(message="boom", stack_trace=None, type=0)
    )
    assert received == [LogLine(message="boom", stack_trace="", level=0)]
    session.remove_log_listener()
    assert driver.calls[-1] == ("remove_notification_listener", (NotificationType.LOG,), {})


def test_static_members_time_scale_and_scene_wait() -> None:
    driver = FakeDriver()
    session = AltTesterSession(driver)
    session.call_static_method("UnityEngine.Debug", "LogError", "UnityEngine.CoreModule", ["x"])
    assert session.get_static_property("PlayerData", "instance.coins", "Assembly-CSharp") == 0
    assert session.time_scale() == 1.0
    session.set_time_scale(2.0)
    session.wait_for_scene("Start", 30)
    assert [c[0] for c in driver.calls] == [
        "call_static_method",
        "get_static_property",
        "set_time_scale",
        "wait_for_current_scene_to_be",
    ]
    assert driver.calls[0][2] == {"parameters": ["x"]}
    assert driver.calls[1][2] == {"max_depth": 2}
    assert driver.calls[3][2] == {"timeout": 30, "interval": 0.5}
