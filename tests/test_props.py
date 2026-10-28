import copy
import json
from pathlib import Path

import pytest

from backend.app.content import CATALOG, mutable_copy, validate_content
from backend.app.engine.props import instantiate_props, world_surfaces
from backend.app.engine.physics import move_with_collisions, has_line_of_sight, swept_circle_aabb_entry
from backend.app.engine.sound_occlusion import calculate_occlusion


def fixture_props(catalog=CATALOG):
    data = json.loads((Path(__file__).parent / "fixtures" / "stage10-props.json").read_text())
    return instantiate_props(data["placements"], catalog=catalog)


def test_data_only_spawn_visual_swap_and_no_default_placements(game_room, monkeypatch):
    data = mutable_copy(CATALOG.definitions)
    data["props"]["console"]["visual_id"] = "test_detail"
    edited = validate_content(data)
    props, puzzles = fixture_props(edited)
    original, _ = fixture_props()
    assert props[-1]["visual_id"] == "test_detail"
    assert props[-1]["geometry"] == original[-1]["geometry"]
    assert {p["archetype"] for p in props} == {"chair", "table", "console"}
    assert all(not s["props"] for s in CATALOG.definitions["segments"].values())
    assert game_room.start_mission(game_room.add_player("p")["id"], False)
    assert not game_room.map_data.get("props")
    assert not game_room.get_snapshot("p")["map"]["props"]
    from backend.app.engine import map_builder
    from backend.app.engine.wfc import run_wfc
    data["segments"]["drop_hangar"]["props"] = [{"id": "extra_chair", "archetype": "chair", "x": 10, "y": 4}]
    edited = validate_content(data)
    monkeypatch.setattr(map_builder, "SEGMENT_TEMPLATES", edited.definitions["segments"])
    generated = map_builder.build_wfc_map("outpost", 101, layout=run_wfc("outpost", 101))
    assert generated["props"][0]["archetype"] == "chair"
    assert generated["props"][0]["id"].endswith("extra_chair")


def test_blocking_nonblocking_independent_geometry_and_orientation():
    props, _ = fixture_props()
    surfaces = world_surfaces({"props": props})
    assert move_with_collisions(7, 10, 2, 0, 1, .42, surfaces)[0] == pytest.approx(9)
    assert move_with_collisions(8, 12, 4, 0, 1, .42, surfaces)[0] < 9
    assert has_line_of_sight(8, 12, 12, 12, surfaces)  # table blocks shots/movement, not sight
    assert not has_line_of_sight(10, 10, 14, 10, surfaces)
    assert any(swept_circle_aabb_entry(8, 12, 12, 12, .1, s) is not None for s in surfaces if s["blocks_projectile"])
    assert calculate_occlusion(8, 12, 12, 12, surfaces, world_id="props")[2] == 1
    placements = [{"id": "rotated", "archetype": "table", "x": 10, "y": 10, "orientation": 90}]
    rotated, _ = instantiate_props(placements)
    rect = rotated[0]["geometry"]["movement"]
    assert rect["w"] == pytest.approx(.8) and rect["h"] == pytest.approx(1.8)
    assert props[1]["geometry"]["movement"] != props[1]["geometry"]["projectile"]


def test_authoritative_movement_projectile_interaction_and_pure_snapshot(game_room):
    p = game_room.add_player("p")
    game_room.select_class("p", "tech")
    game_room.start_mission("p", False)
    game_room.phase = "active"
    props, puzzles = fixture_props()
    game_room.map_data.update(walls=[], enemies=[], loot=[], crates=[], props=props, puzzles=puzzles)
    p.update(x=8.0, y=12.0)
    game_room.update_input("p", {"dx": 1.0, "dy": 0.0})
    for _ in range(30):
        game_room.step(1/30)
    assert p["x"] < 9
    game_room.projectiles = [{"id": "shot", "x": 8.0, "y": 12.0, "vx": 40, "vy": 0,
                              "radius": .1, "ttl": 1, "damage": 10, "owner_id": "p", "faction": "squad"}]
    game_room.step(.1)
    assert not game_room.projectiles
    p.update(x=12.0, y=11.0)
    target = puzzles[0]
    before = copy.deepcopy(game_room.map_data)
    opened = game_room.handle_interact("p", target["id"])
    assert opened["type"] == "open_puzzle"
    assert game_room.map_data == before
    snapshot = game_room.get_snapshot("p")
    assert snapshot["map"]["props"] == props
    assert game_room.map_data == before
    assert "<svg" not in json.dumps(snapshot) and "mesh" not in json.dumps(snapshot)
    result = game_room.handle_puzzle_action("p", target["id"], {"action": "tech_bypass"})
    assert result["ok"] and target["solved"]
    p.update(x=12.0, y=8.0)
    target["solved"] = False
    assert game_room.handle_interact("p", target["id"])["ok"] is False


@pytest.mark.parametrize("section,key,field,value", [
    ("visuals", "chair", "material_id", "missing"),
    ("visuals", "chair", "sprite", "missing"),
    ("visuals", "chair", "width", True),
    ("visuals", "chair", "height", float("nan")),
    ("visuals", "chair", "layer", 100),
    ("materials", "steel", "texture", "missing"),
    ("assets", "test_floor", "path", "../secret.svg"),
    ("assets", "test_floor", "path", "visual/missing.svg"),
    ("assets", "test_floor", "path", "https://example.com/a.svg"),
    ("assets", "test_floor", "path", "visual/test-floor.svg?x"),
])
def test_strict_presentation_and_asset_references(section, key, field, value):
    data = mutable_copy(CATALOG.definitions)
    data["presentation"][section][key][field] = value
    with pytest.raises(ValueError, match="presentation"):
        validate_content(data)


@pytest.mark.parametrize("change", ["ref", "orientation", "bool_orientation", "float_orientation", "bounds", "unknown"])
def test_strict_consumed_prop_fields(change):
    data = mutable_copy(CATALOG.definitions)
    placement = {"id": "fixture", "archetype": "console", "x": 10, "y": 4}
    data["segments"]["drop_hangar"]["props"] = [placement]
    if change == "ref": placement["archetype"] = "missing"
    if change == "orientation": placement["orientation"] = 45
    if change == "bool_orientation": placement["orientation"] = False
    if change == "float_orientation": placement["orientation"] = 90.0
    if change == "bounds": placement["x"] = 16
    if change == "unknown": data["props"]["console"]["geometry"]["script"] = "eval"
    with pytest.raises(ValueError): validate_content(data)


@pytest.mark.parametrize("channel", ["movement", "projectile", "sight", "acoustic"])
def test_each_channel_can_be_authored_without_other_blocking(channel):
    data = mutable_copy(CATALOG.definitions)
    data["props"]["chair"]["geometry"] = {channel: {"x": -.5, "y": -.5, "w": 1.0, "h": 1.0}}
    edited = validate_content(data)
    props, _ = instantiate_props([{"id": "panel", "archetype": "chair", "x": 5, "y": 5}], catalog=edited)
    surfaces = world_surfaces({"props": props})
    assert len(surfaces) == 1
    assert (move_with_collisions(3, 5, 4, 0, 1, .42, surfaces)[0] < 5) == (channel == "movement")
    assert has_line_of_sight(3, 5, 7, 5, surfaces) == (channel != "sight")
    assert calculate_occlusion(3, 5, 7, 5, surfaces, world_id=channel)[2] == (1 if channel == "acoustic" else 0)
    assert surfaces[0]["blocks_projectile"] == (channel == "projectile")


def test_generation_validates_prop_terminal_access_and_rejects_unknown_asset_symlink(tmp_path):
    from backend.app.engine.map_builder import build_mission_map, validate_generated_map
    generated = build_mission_map("outpost", 101)
    props, puzzles = instantiate_props([{"id": "unreachable", "archetype": "console", "x": 1000, "y": 1000}])
    generated.update(props=props, puzzles=generated["puzzles"] + puzzles)
    with pytest.raises(ValueError, match="Unreachable interaction"):
        validate_generated_map(generated)
    from backend.app.content import ASSET_ROOT
    import shutil
    assets = tmp_path / "assets"
    shutil.copytree(ASSET_ROOT, assets)
    (assets / "visual" / "test-floor.svg").unlink()
    (assets / "visual" / "test-floor.svg").symlink_to(ASSET_ROOT / "visual" / "test-floor.svg")
    with pytest.raises(ValueError, match="presentation.assets.test_floor"):
        validate_content(mutable_copy(CATALOG.definitions), asset_root=assets)
