"""Scene dumps: every object an introspection pass saw in a scene, with its hierarchy path."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict


class SceneElement(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    path: str
    enabled: bool
    transform_id: int
    parent_transform_id: int
    screen_x: float
    screen_y: float
    mobile_y: float
    camera_id: int | None
    components: tuple[str, ...] | None  # None when components were not collected


class SceneDump(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene: str
    label: str | None
    build: str
    captured_at: str  # passed in by the caller: pg_core never reads the clock
    element_count: int
    elements: tuple[SceneElement, ...]


def element_paths(raw: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    """Rebuild `/Root/Child/Leaf` paths from the transformId/transformParentId links.

    A path starts at the highest ancestor present in the dump. Cycles cannot hang the walk.
    """
    by_transform = {int(element["transformId"]): element for element in raw}
    paths: dict[int, str] = {}
    for element in raw:
        names: list[str] = []
        seen: set[int] = set()
        current: Mapping[str, Any] | None = element
        while current is not None and int(current["transformId"]) not in seen:
            seen.add(int(current["transformId"]))
            names.append(str(current["name"]))
            current = by_transform.get(int(current["transformParentId"]))
        paths[int(element["id"])] = "/" + "/".join(reversed(names))
    return paths


def build_scene_dump(
    *,
    scene: str,
    label: str | None,
    build: str,
    captured_at: str,
    raw: Sequence[Mapping[str, Any]],
    components: Mapping[int, Sequence[str]] | None,
) -> SceneDump:
    paths = element_paths(raw)
    elements = []
    for item in raw:
        element_id = int(item["id"])
        camera = item.get("idCamera")
        elements.append(
            SceneElement(
                id=element_id,
                name=str(item["name"]),
                path=paths[element_id],
                enabled=bool(item["enabled"]),
                transform_id=int(item["transformId"]),
                parent_transform_id=int(item["transformParentId"]),
                screen_x=float(item["x"]),
                screen_y=float(item["y"]),
                mobile_y=float(item["mobileY"]),
                camera_id=int(camera) if camera is not None else None,
                components=(
                    tuple(components.get(element_id, ())) if components is not None else None
                ),
            )
        )
    ordered = tuple(sorted(elements, key=lambda e: (e.path, e.id)))
    return SceneDump(
        scene=scene,
        label=label,
        build=build,
        captured_at=captured_at,
        element_count=len(ordered),
        elements=ordered,
    )
