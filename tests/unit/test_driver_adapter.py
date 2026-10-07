"""Unit tests for the AltTester driver adapter, using the real `alttester` exception classes."""

from __future__ import annotations

from typing import Any, ClassVar

import alttester
import pytest
from alttester import exceptions as alt

from pg_core.doctor import AppProbeError
from pg_sdk import _driver


class FakeDriver:
    instances: ClassVar[list[FakeDriver]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.stopped = False
        FakeDriver.instances.append(self)

    def get_current_scene(self) -> str:
        return "Start"

    def get_all_loaded_scenes(self) -> list[str]:
        return ["Start", "Shop"]

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def fresh_instances() -> None:
    FakeDriver.instances = []


def test_probe_reads_scenes_and_releases_the_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alttester, "AltDriver", FakeDriver)
    probe = _driver.probe_app("127.0.0.1", 13000, 5)
    assert probe.scene == "Start"
    assert probe.loaded_scenes == ("Start", "Shop")
    assert probe.error is None
    (driver,) = FakeDriver.instances
    assert driver.kwargs == {
        "host": "127.0.0.1",
        "port": 13000,
        "app_name": "__default__",
        "timeout": 5,
    }
    assert driver.stopped


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (alt.NoAppConnected, AppProbeError.NO_APP),
        (alt.MaxNoOfConnectionsDriversExceededException, AppProbeError.SLOT_BUSY),
        (alt.MultipleDriversTryingToConnectException, AppProbeError.SLOT_BUSY),
        (alt.MultipleDriverError, AppProbeError.SLOT_BUSY),
        (alt.ConnectionTimeoutError, AppProbeError.TIMEOUT),
        (alt.ConnectionError, AppProbeError.CONNECTION),
    ],
)
def test_connection_errors_are_classified(
    monkeypatch: pytest.MonkeyPatch, exc: type[Exception], kind: AppProbeError
) -> None:
    def refuse(**kwargs: Any) -> None:
        raise exc("from server")

    monkeypatch.setattr(alttester, "AltDriver", refuse)
    probe = _driver.probe_app("127.0.0.1", 13000, 1)
    assert probe.error is kind
    assert probe.message == "from server"


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (alt.CommandResponseTimeoutException, AppProbeError.TIMEOUT),
        (alt.NotFoundException, AppProbeError.CONNECTION),
    ],
)
def test_command_errors_after_connecting_still_release_the_driver(
    monkeypatch: pytest.MonkeyPatch, exc: type[Exception], kind: AppProbeError
) -> None:
    class SlowDriver(FakeDriver):
        def get_current_scene(self) -> str:
            raise exc("no answer")

    monkeypatch.setattr(alttester, "AltDriver", SlowDriver)
    probe = _driver.probe_app("127.0.0.1", 13000, 1)
    assert probe.error is kind
    assert FakeDriver.instances[0].stopped
