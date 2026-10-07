"""Typed adapter around the untyped AltTester Python driver (import name `alttester`).

Only this module touches `alttester`, so the rest of the code base stays fully typed and the
driver can be faked in unit tests. Generated tests never import it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from pg_core.doctor import AppProbe, AppProbeError
from pg_sdk._ui import Element
from pg_sdk.errors import PGInfraError

DEFAULT_APP_NAME = "__default__"


class LogLine(BaseModel):
    """One log notification from the game (Debug.Log*, exceptions)."""

    model_config = ConfigDict(frozen=True)

    message: str
    stack_trace: str
    level: int | str | None


class AltTesterSession:
    """One live driver connection to the instrumented app through AltTester Desktop."""

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    def current_scene(self) -> str:
        return str(self._driver.get_current_scene())

    def loaded_scenes(self) -> list[str]:
        return [str(scene) for scene in self._driver.get_all_loaded_scenes()]

    def wait_for_scene(self, name: str, timeout_s: float) -> None:
        self._driver.wait_for_current_scene_to_be(name, timeout=timeout_s, interval=0.5)

    def dump_elements(
        self, *, with_components: bool
    ) -> tuple[list[dict[str, Any]], dict[int, list[str] | None] | None]:
        """Every object in the loaded scenes, active or not, plus each one's component names.

        Scenes create and destroy objects while they load, so an object listed first can be
        gone when its components are read. Its entry is then None instead of failing the dump.
        """
        from alttester import exceptions as alt

        objects = self._driver.get_all_elements(enabled=False)
        raw = [dict(obj.to_json()) for obj in objects]
        if not with_components:
            return raw, None
        components: dict[int, list[str] | None] = {}
        for obj in objects:
            try:
                names = [str(c.get("componentName")) for c in obj.get_all_components()]
            except alt.NotFoundException:
                names = None
            components[int(obj.id)] = names
        return raw, components

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._driver.get_png_screenshot(str(path))

    def add_log_listener(self, callback: Callable[[LogLine], None]) -> None:
        """Forward the game's log notifications to `callback`, once each.

        `overwrite=False` on purpose: with the driver's default (True), AltTester-Driver 2.3.2
        registers the callback twice and delivers every notification twice (docs/VERSIONS.md).
        """
        from alttester.commands.Notifications.notification_type import NotificationType

        def relay(result: Any) -> None:
            callback(
                LogLine(
                    message=str(result.message),
                    stack_trace=str(result.stack_trace or ""),
                    level=result.type,
                )
            )

        self._driver.add_notification_listener(NotificationType.LOG, relay, overwrite=False)

    def remove_log_listener(self) -> None:
        from alttester.commands.Notifications.notification_type import NotificationType

        self._driver.remove_notification_listener(NotificationType.LOG)

    def call_static_method(
        self, type_name: str, method: str, assembly: str, parameters: list[str] | None = None
    ) -> Any:
        with _infra_errors():
            return self._driver.call_static_method(
                type_name, method, assembly, parameters=parameters
            )

    def get_static_property(
        self, component: str, path: str, assembly: str, max_depth: int = 2
    ) -> Any:
        with _infra_errors():
            return self._driver.get_static_property(component, path, assembly, max_depth=max_depth)

    def screen_size(self) -> tuple[int, int]:
        width, height = self._driver.get_application_screensize()
        return int(width), int(height)

    def position(self, path: str) -> tuple[float, float] | None:
        """Screen position of the active object at `path`, or None if there is none."""
        from alttester import By
        from alttester import exceptions as alt

        try:
            obj = self._driver.find_object(By.PATH, path)
        except alt.NotFoundException:
            return None
        return float(obj.x), float(obj.y)

    def tap(self, path: str) -> bool:
        """Tap the active object at `path`; False when there is none."""
        obj = self._find_object(path)
        if obj is None:
            return False
        with _infra_errors():
            obj.tap()
        return True

    def time_scale(self) -> float:
        return float(self._driver.get_time_scale())

    def set_time_scale(self, scale: float) -> None:
        self._driver.set_time_scale(scale)

    def close(self) -> None:
        self._driver.stop()

    # --- pg_sdk._ui.Driver ----------------------------------------------------------------

    def find(self, path: str) -> Element | None:
        obj = self._find_object(path)
        return None if obj is None else _element(obj)

    def find_all(self, path: str) -> list[Element]:
        from alttester import By

        with _infra_errors():
            return [_element(obj) for obj in self._driver.find_objects(By.PATH, path)]

    def text(self, path: str) -> str | None:
        from alttester import exceptions as alt

        obj = self._find_object(path)
        if obj is None:
            return None
        with _infra_errors():
            try:
                return str(obj.get_text())
            except alt.NotFoundException:
                return None

    def component_property(self, path: str, component: str, prop: str, assembly: str) -> Any:
        from alttester import exceptions as alt

        obj = self._find_object(path)
        if obj is None:
            raise LookupError(f"no active object at {path}")
        with _infra_errors():
            try:
                return obj.get_component_property(component, prop, assembly)
            except alt.NotFoundException as exc:
                raise LookupError(f"{component}.{prop} at {path}: {exc}") from exc

    def set_component_property(
        self, path: str, component: str, prop: str, assembly: str, value: Any
    ) -> None:
        obj = self._find_object(path)
        if obj is None:
            raise LookupError(f"no active object at {path}")
        with _infra_errors():
            obj.set_component_property(component, prop, assembly, value)

    def static_property(self, type_name: str, member: str, assembly: str) -> Any:
        with _infra_errors():
            return self._driver.get_static_property(type_name, member, assembly, max_depth=2)

    def set_static_property(self, type_name: str, member: str, assembly: str, value: Any) -> None:
        with _infra_errors():
            self._driver.set_static_property(type_name, member, assembly, value)

    def swipe(
        self, start: tuple[float, float], end: tuple[float, float], duration_s: float
    ) -> None:
        with _infra_errors():
            self._driver.swipe(start, end, duration=duration_s, wait=True)

    def _find_object(self, path: str) -> Any:
        from alttester import By
        from alttester import exceptions as alt

        with _infra_errors():
            try:
                return self._driver.find_object(By.PATH, path)
            except alt.NotFoundException:
                return None


def _element(obj: Any) -> Element:
    return Element(id=int(obj.id), name=str(obj.name), x=float(obj.x), y=float(obj.y))


@contextmanager
def _infra_errors() -> Iterator[None]:
    """Report a broken device chain as PGInfraError, so it is never mistaken for a test result."""
    from alttester import exceptions as alt
    from websocket import WebSocketException

    try:
        yield
    except (
        alt.ConnectionError,
        alt.CommandResponseTimeoutException,
        WebSocketException,
        ConnectionError,
    ) as exc:
        raise PGInfraError(f"driver connection failed: {type(exc).__name__}: {exc}") from exc


def connect(
    host: str, port: int, timeout_s: float, app_name: str = DEFAULT_APP_NAME
) -> AltTesterSession:
    # Imported lazily: importing alttester is slow, and only device commands need it.
    from alttester import AltDriver

    return AltTesterSession(AltDriver(host=host, port=port, app_name=app_name, timeout=timeout_s))


def probe_app(host: str, port: int, timeout_s: float) -> AppProbe:
    """Connect, read the current and loaded scenes, and always release the driver slot."""
    from alttester import exceptions as alt

    try:
        session = connect(host, port, timeout_s)
    except alt.NoAppConnected as exc:
        return AppProbe(error=AppProbeError.NO_APP, message=str(exc))
    except (
        alt.MaxNoOfConnectionsDriversExceededException,
        alt.MultipleDriversTryingToConnectException,
        alt.MultipleDriverError,
    ) as exc:
        return AppProbe(error=AppProbeError.SLOT_BUSY, message=str(exc))
    except alt.ConnectionTimeoutError as exc:
        return AppProbe(error=AppProbeError.TIMEOUT, message=str(exc))
    except alt.ConnectionError as exc:
        return AppProbe(error=AppProbeError.CONNECTION, message=str(exc))

    try:
        return AppProbe(scene=session.current_scene(), loaded_scenes=tuple(session.loaded_scenes()))
    except alt.CommandResponseTimeoutException as exc:
        return AppProbe(error=AppProbeError.TIMEOUT, message=str(exc))
    except alt.AltException as exc:
        return AppProbe(error=AppProbeError.CONNECTION, message=str(exc))
    finally:
        session.close()
