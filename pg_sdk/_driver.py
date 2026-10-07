"""Typed adapter around the untyped AltTester Python driver (import name `alttester`).

Only this module touches `alttester`, so the rest of the code base stays fully typed and the
driver can be faked in unit tests. Generated tests never import it.
"""

from __future__ import annotations

from typing import Any

from pg_core.doctor import AppProbe, AppProbeError

DEFAULT_APP_NAME = "__default__"


class AltTesterSession:
    """One live driver connection to the instrumented app through AltTester Desktop."""

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    def current_scene(self) -> str:
        return str(self._driver.get_current_scene())

    def loaded_scenes(self) -> list[str]:
        return [str(scene) for scene in self._driver.get_all_loaded_scenes()]

    def close(self) -> None:
        self._driver.stop()


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
