"""Unit tests for recognising Android's 16 KB compatibility warning (pg_core.android_ui)."""

from pathlib import Path

import pytest

from pg_core.android_ui import TapPoint, compat_dialog_ok

# Real `uiautomator dump` taken on the project phone while the warning was showing.
REAL_DIALOG = (Path(__file__).parent / "fixtures" / "android16_compat_dialog.xml").read_text(
    encoding="utf-8"
)

APP_ONLY = (
    '<?xml version="1.0" ?><hierarchy rotation="0">'
    '<node index="0" text="" resource-id="" class="android.view.SurfaceView" '
    'package="com.DefaultCompany.TrashCat" bounds="[0,0][1440,3120]" /></hierarchy>'
)


def dialog(title: str, message: str, ok_text: str = "OK", package: str = "android") -> str:
    return (
        '<?xml version="1.0" ?><hierarchy rotation="0">'
        f'<node text="{title}" resource-id="android:id/alertTitle" package="{package}" '
        'bounds="[127,982][1313,1070]" />'
        f'<node text="{message}" resource-id="android:id/message" package="{package}" '
        'bounds="[127,1115][1313,2820]" />'
        f'<node text="{ok_text}" resource-id="android:id/button2" package="{package}" '
        'bounds="[127,2880][529,3015]" />'
        '<node text="Don\'t show again" resource-id="android:id/button1" '
        f'package="{package}" bounds="[533,2880][1313,3015]" /></hierarchy>'
    )


def test_real_dialog_taps_ok_not_dont_show_again() -> None:
    point = compat_dialog_ok(REAL_DIALOG)
    assert point == TapPoint(x=328, y=2947)  # centre of OK [127,2880][529,3015]
    assert point.x < 533  # left of "Don't show again", which starts at x=533


def test_game_screen_without_dialog() -> None:
    assert compat_dialog_ok(APP_ONLY) is None


@pytest.mark.parametrize(
    "xml",
    [
        "",
        "<not xml",
        dialog("Some other dialog", "This app isn't 16 KB-compatible."),
        dialog("Android app compatibility", "Some other compatibility problem"),
        dialog("Android app compatibility", "This app isn't 16 KB-compatible.", ok_text="Close"),
        dialog("Android app compatibility", "This app isn't 16 KB-compatible.", package="evil.app"),
    ],
)
def test_lookalikes_are_not_tapped(xml: str) -> None:
    assert compat_dialog_ok(xml) is None


def test_malformed_bounds_are_ignored() -> None:
    xml = dialog("Android app compatibility", "This app isn't 16 KB-compatible.").replace(
        "[127,2880][529,3015]", "garbage"
    )
    assert compat_dialog_ok(xml) is None
