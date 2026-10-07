"""Unit tests for scene dumps (pg_core.scene)."""

from typing import Any

from pg_core.scene import build_scene_dump, element_paths


def raw(id_: int, name: str, transform: int, parent: int, enabled: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "id": id_,
        "x": 10.0,
        "y": 20.0,
        "z": 0.0,
        "mobileY": 30.0,
        "type": "",
        "enabled": enabled,
        "worldX": 0.0,
        "worldY": 0.0,
        "worldZ": 0.0,
        "transformParentId": parent,
        "transformId": transform,
        "idCamera": 5,
    }


HIERARCHY = [
    raw(1, "Canvas", transform=100, parent=0),
    raw(2, "Loadout", transform=200, parent=100),
    raw(3, "StoreButton", transform=300, parent=200, enabled=False),
]


def test_paths_follow_parent_links() -> None:
    assert element_paths(HIERARCHY) == {
        1: "/Canvas",
        2: "/Canvas/Loadout",
        3: "/Canvas/Loadout/StoreButton",
    }


def test_missing_parent_starts_path_at_highest_known_ancestor() -> None:
    orphan = [raw(7, "Popup", transform=700, parent=999)]
    assert element_paths(orphan) == {7: "/Popup"}


def test_cycles_do_not_hang() -> None:
    cyclic = [raw(1, "A", transform=10, parent=20), raw(2, "B", transform=20, parent=10)]
    assert element_paths(cyclic) == {1: "/B/A", 2: "/A/B"}


def test_build_scene_dump_with_components_sorted_by_path() -> None:
    dump = build_scene_dump(
        scene="Main",
        label="loadout",
        build="abc123def456",
        captured_at="2026-10-07T12:00:00+00:00",
        raw=list(reversed(HIERARCHY)),
        components={3: ["Button", "Image"]},
    )
    assert dump.element_count == 3
    assert [e.path for e in dump.elements] == [
        "/Canvas",
        "/Canvas/Loadout",
        "/Canvas/Loadout/StoreButton",
    ]
    store = dump.elements[2]
    assert store.components == ("Button", "Image")
    assert store.enabled is False
    assert dump.elements[0].components == ()  # collected, none reported for it
    assert store.camera_id == 5
    assert store.mobile_y == 30.0


def test_objects_that_vanished_mid_dump_are_counted() -> None:
    dump = build_scene_dump(
        scene="Main",
        label=None,
        build="b",
        captured_at="t",
        raw=HIERARCHY,
        components={1: ["Canvas"], 2: None, 3: ["Button"]},
    )
    assert dump.vanished_during_dump == 1
    by_id = {e.id: e for e in dump.elements}
    assert by_id[2].components is None
    assert by_id[3].components == ("Button",)


def test_build_scene_dump_without_components() -> None:
    no_camera = raw(1, "Canvas", transform=100, parent=0)
    no_camera["idCamera"] = None
    dump = build_scene_dump(
        scene="Start",
        label=None,
        build="unknown-build",
        captured_at="t",
        raw=[no_camera],
        components=None,
    )
    assert dump.elements[0].components is None
    assert dump.elements[0].camera_id is None
