"""Recognise Android system dialogs in a `uiautomator dump`, so device helpers can dismiss them.

Only one dialog is handled: Android 16's warning that a debuggable app is not 16 KB-aligned
(see docs/adr/0008-android-compat-dialog.md). It appears after every `pm clear` and blocks
Unity from starting until it is dismissed. We tap its **OK** button and never "Don't show
again", so no device setting changes.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict

COMPAT_TITLE = "Android app compatibility"
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class TapPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: int
    y: int


def _center(bounds: str) -> TapPoint | None:
    match = _BOUNDS.fullmatch(bounds)
    if match is None:
        return None
    x1, y1, x2, y2 = (int(value) for value in match.groups())
    return TapPoint(x=(x1 + x2) // 2, y=(y1 + y2) // 2)


def compat_dialog_ok(dump_xml: str) -> TapPoint | None:
    """Where to tap OK on the 16 KB app-compatibility warning, or None if it is not showing.

    Every condition must hold: a system (`android`) alert titled "Android app compatibility",
    a message that mentions 16 KB, and an `android:id/button2` labelled OK.
    """
    try:
        root = ET.fromstring(dump_xml)  # noqa: S314 - our own device's uiautomator dump
    except ET.ParseError:
        return None
    system_nodes = [node for node in root.iter("node") if node.get("package") == "android"]

    def has(resource_id: str, predicate: str) -> bool:
        return any(
            node.get("resource-id") == resource_id and predicate in node.get("text", "")
            for node in system_nodes
        )

    if not (has("android:id/alertTitle", COMPAT_TITLE) and has("android:id/message", "16 KB")):
        return None
    for node in system_nodes:
        if node.get("resource-id") == "android:id/button2" and node.get("text") == "OK":
            return _center(node.get("bounds", ""))
    return None
