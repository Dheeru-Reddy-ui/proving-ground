"""How the pytest plugin names a test's failure (feeds G2/G3 via pg_core.classify)."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from pg_sdk.errors import PGGameError, PGInfraError, PGTimeout
from pg_sdk.pytest_plugin import _failure_kind


def raises(exc: BaseException) -> Callable[[], None]:
    def body() -> None:
        raise exc

    return body


def kind_of(body: Callable[[], None], when: str = "call") -> str | None:
    call: pytest.CallInfo[None] = pytest.CallInfo.from_call(body, when=when)  # type: ignore[arg-type]
    failed = call.excinfo is not None
    report: Any = SimpleNamespace(passed=not failed, skipped=False, when=when)
    return _failure_kind(report, call)


def did_not_raise() -> None:
    with pytest.raises(PGTimeout):
        pass


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (raises(AssertionError("assert 0 == 750")), "assertion"),
        (did_not_raise, "assertion"),
        (lambda: pytest.fail("expected the popup to stay"), "assertion"),
        (raises(PGTimeout("the store", 10, "main_menu")), "pg_timeout"),
        (raises(PGGameError("NullReferenceException")), "game_error"),
        (raises(PGInfraError("driver connection failed")), "infra"),
        (raises(TypeError("buy() missing 1 required argument")), "test_error"),
        (lambda: None, None),
    ],
)
def test_failure_kinds(body: Callable[[], None], expected: str | None) -> None:
    assert kind_of(body) == expected


def test_any_setup_failure_is_infra() -> None:
    assert kind_of(raises(PGTimeout("the main menu", 60, "start")), when="setup") == "infra"
