"""The settings popup: volume sliders and deleting saved data."""

from __future__ import annotations

from pg_sdk._screens import screen_shown
from pg_sdk._ui import UI_ASSEMBLY, Ui
from pg_sdk.types import Screen, Volumes

_SLIDER = "UnityEngine.UI.Slider"


class Settings:
    """The settings popup, opened from the main menu."""

    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    def is_shown(self) -> bool:
        """True when the settings popup is open."""
        return screen_shown(self._ui) is Screen.SETTINGS

    def volumes_shown(self) -> Volumes:
        """Positions of the master, music and sound-effects sliders (0.0 to 1.0).
        Precondition: settings open."""
        self._require()
        return Volumes(
            master=self._slider("settings.master"),
            music=self._slider("settings.music"),
            sound_effects=self._slider("settings.sound_effects"),
        )

    def set_master_volume(self, level: float) -> None:
        """Move the master slider to `level` (0.0 to 1.0); the game applies it at once.
        Precondition: settings open."""
        self._set("settings.master", level)

    def set_music_volume(self, level: float) -> None:
        """Move the music slider to `level` (0.0 to 1.0). Precondition: settings open."""
        self._set("settings.music", level)

    def set_sound_effects_volume(self, level: float) -> None:
        """Move the sound-effects slider to `level` (0.0 to 1.0). Precondition: settings open."""
        self._set("settings.sound_effects", level)

    def close(self) -> None:
        """Close the popup (the game saves settings here). Precondition: settings open."""
        self._require()
        self._ui.press(self._ui.path("settings.close"), "settings close button")
        self._ui.wait_until("the settings popup to close", lambda: not self.is_shown())

    def delete_data(self) -> None:
        """Press Delete data; returns once the confirmation question is shown.
        Precondition: settings open."""
        self._require()
        self._ui.press(self._ui.path("settings.delete_data"), "delete data button")
        self._ui.wait_until("the delete confirmation", self.confirmation_shown)

    def confirmation_shown(self) -> bool:
        """True while the delete-data confirmation question is shown."""
        return self._ui.visible(self._ui.path("settings.confirm_yes"))

    def confirm_delete(self) -> None:
        """Answer YES to the delete confirmation. Precondition: confirmation shown."""
        self._ui.press(self._ui.path("settings.confirm_yes"), "delete confirmation YES")
        self._ui.wait_until("the confirmation to close", lambda: not self.confirmation_shown())

    def cancel_delete(self) -> None:
        """Answer NO to the delete confirmation. Precondition: confirmation shown."""
        self._ui.press(self._ui.path("settings.confirm_no"), "delete confirmation NO")
        self._ui.wait_until("the confirmation to close", lambda: not self.confirmation_shown())

    # --- helpers --------------------------------------------------------------------------

    def _require(self) -> None:
        self._ui.wait_until("the settings popup", self.is_shown)

    def _slider(self, key: str) -> float:
        value = self._ui.driver.component_property(
            self._ui.path(key), _SLIDER, "value", UI_ASSEMBLY
        )
        return round(float(value), 4)

    def _set(self, key: str, level: float) -> None:
        if not 0.0 <= level <= 1.0:
            raise ValueError(f"volume level must be between 0.0 and 1.0, got {level}")
        self._require()
        path = self._ui.path(key)
        # Setting Slider.value fires the slider's onValueChanged, like dragging it does.
        self._ui.driver.set_component_property(path, _SLIDER, "value", UI_ASSEMBLY, level)
        self._ui.log(f"set {key} to {level}")
        self._ui.wait_until(f"{key} to read {level}", lambda: abs(self._slider(key) - level) < 1e-3)
