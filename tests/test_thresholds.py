"""Data-driven threshold generation and authoritative latching-door contracts."""

import copy
import hashlib

import pytest

from backend.app.content import CATALOG, mutable_copy, validate_content
from backend.app.engine import doors, map_builder, objectives, wfc
from backend.app.engine.game_loop import GameRoom
from backend.app.engine.map_builder import build_mission_map, validate_generated_map
from backend.app.engine.physics import has_line_of_sight, move_with_collisions


def install_catalog(monkeypatch, data):
    catalog = validate_content(data)
    monkeypatch.setattr(map_builder, "CATALOG", catalog)
    for module in (map_builder, wfc):
        monkeypatch.setattr(module, "MISSIONS", catalog.definitions["missions"])
        monkeypatch.setattr(module, "SEGMENT_TEMPLATES", catalog.definitions["segments"])
    return catalog


@pytest.mark.parametrize("policy_id,mode", [("open_passage", "open"), ("automatic_door", "proximity"), ("power_lock", "puzzle"), ("signal_lock", "puzzle")])
@pytest.mark.parametrize("mission_id", sorted(CATALOG.definitions["missions"]))
def test_single_policy_weights_generate_solvable_maps(monkeypatch, policy_id, mode, mission_id):
    data = mutable_copy(CATALOG.definitions)
    for mission in data["missions"].values():
        mission["threshold_weights"] = {identity: int(identity == policy_id) for identity in data["thresholds"]}
    catalog = install_catalog(monkeypatch, data)
    for seed in (0, 101):
        level = build_mission_map(mission_id, seed)
        validate_generated_map(level)
        assert all(c["mode"] == mode and c["threshold_id"] == policy_id for c in level["connectors"])
        connector_puzzles = [p for p in level["puzzles"] if not p.get("prop_id")]
        assert len(connector_puzzles) == len(level["connectors"]) if mode == "puzzle" else not connector_puzzles
        assert {w["puzzle_id"] for w in level["wires"]} == {p["id"] for p in connector_puzzles}
        for connector in level["connectors"]:
            if mode == "open":
                assert "door_id" not in connector and "puzzle_id" not in connector
                assert not any(w["id"] == connector["id"].replace("conn_", "door_") for w in level["walls"])
            else:
                wall = next(w for w in level["walls"] if w["id"] == connector["door_id"])
                assert all(wall[f] is True for f in ("active", "is_door", "blocks_movement", "blocks_projectile", "blocks_sight"))
                if mode == "proximity":
                    assert "puzzle_id" not in connector and wall["trigger_radius"] == 2.4
                else:
                    puzzle = next(p for p in connector_puzzles if p["id"] == connector["puzzle_id"])
                    assert puzzle["definition_id"] == catalog.definitions["thresholds"][policy_id]["puzzle_id"]


def test_mixed_defaults_and_weight_key_order_are_seed_deterministic(monkeypatch):
    one = build_mission_map("outpost", 101)
    assert {c["mode"] for c in one["connectors"]} == {"open", "proximity", "puzzle"}
    assert one == build_mission_map("outpost", 101)
    data = mutable_copy(CATALOG.definitions)
    for mission in data["missions"].values():
        mission["threshold_weights"] = dict(reversed(list(mission["threshold_weights"].items())))
    install_catalog(monkeypatch, data)
    assert one == build_mission_map("outpost", 101)
    assert one["generator_version"] == "wfc-3" and one["content_version"] == "foundation-3"


@pytest.mark.parametrize("damage", ["unknown_mode", "extra_field", "open_radius", "missing_radius", "zero_radius", "small_radius", "nan_radius", "infinite_radius", "string_radius", "door_type", "missing_puzzle", "bad_puzzle", "bad_sound", "wrong_sound_category", "loop_sound", "negative_weight", "nan_weight", "infinite_weight", "unknown_weight", "zero_weights", "empty_weights", "world_puzzle", "world_not_door"])
def test_threshold_catalog_rejects_malformed_policy(damage):
    data = mutable_copy(CATALOG.definitions)
    policy = data["thresholds"]["automatic_door"]
    weights = data["missions"]["outpost"]["threshold_weights"]
    if damage == "unknown_mode":
        policy["mode"] = "timer"
    elif damage == "extra_field":
        policy["close_after"] = 1
    elif damage == "open_radius":
        data["thresholds"]["open_passage"]["trigger_radius"] = 2
    elif damage == "missing_radius":
        policy.pop("trigger_radius")
    elif damage in ("zero_radius", "small_radius", "nan_radius", "infinite_radius", "string_radius"):
        policy["trigger_radius"] = {"zero_radius": 0, "small_radius": .1, "nan_radius": float("nan"), "infinite_radius": float("inf"), "string_radius": "2"}[damage]
    elif damage == "door_type":
        policy["door_type"] = "arbitrary_mesh"
    elif damage == "missing_puzzle":
        data["thresholds"]["power_lock"].pop("puzzle_id")
    elif damage == "bad_puzzle":
        data["thresholds"]["power_lock"]["puzzle_id"] = "unknown"
    elif damage in ("bad_sound", "wrong_sound_category"):
        policy["sound_type"] = "unknown" if damage == "bad_sound" else "weapon_fire_pistol"
    elif damage == "loop_sound":
        data["sounds"]["door_operate"]["loop"] = True
    elif damage in ("negative_weight", "nan_weight", "infinite_weight", "unknown_weight"):
        weights["unknown" if damage == "unknown_weight" else "open_passage"] = {"negative_weight": -1, "nan_weight": float("nan"), "infinite_weight": float("inf"), "unknown_weight": 1}[damage]
    elif damage in ("zero_weights", "empty_weights"):
        data["missions"]["outpost"]["threshold_weights"] = {"open_passage": 0} if damage == "zero_weights" else {}
    else:
        wall = data["worlds"]["flagship_stub"]["walls"][0]
        wall["threshold_id"] = "power_lock" if damage == "world_puzzle" else "automatic_door"
    with pytest.raises(ValueError):
        validate_content(data)


@pytest.fixture(scope="module")
def mixed_map():
    return build_mission_map("outpost", 101)


@pytest.mark.parametrize("damage", ["missing_mode", "unknown_policy", "wrong_mode", "open_door", "proximity_puzzle", "wrong_door", "wrong_radius", "nan_radius", "wrong_sound", "wrong_type", "inactive_door", "transparent_door", "passable_door", "wrong_puzzle", "duplicate_connector", "duplicate_port", "duplicate_puzzle", "duplicate_wire", "rogue_puzzle", "rogue_wire", "rogue_door", "missing_jamb"])
def test_generation_rejects_inconsistent_threshold_instances(mixed_map, damage):
    level = copy.deepcopy(mixed_map)
    passage = next(c for c in level["connectors"] if c["mode"] == "open")
    automatic = next(c for c in level["connectors"] if c["mode"] == "proximity")
    locked = next(c for c in level["connectors"] if c["mode"] == "puzzle")
    wall = next(w for w in level["walls"] if w["id"] == automatic["door_id"])
    if damage == "missing_mode":
        automatic.pop("mode")
    elif damage == "unknown_policy":
        automatic["threshold_id"] = "missing"
    elif damage == "wrong_mode":
        automatic["mode"] = "puzzle"
    elif damage == "open_door":
        passage["door_id"] = wall["id"]
    elif damage == "proximity_puzzle":
        automatic["puzzle_id"] = locked["puzzle_id"]
    elif damage == "wrong_door":
        automatic["door_id"] = locked["door_id"]
    elif damage in ("wrong_radius", "nan_radius", "wrong_sound", "wrong_type", "inactive_door", "transparent_door", "passable_door"):
        field, value = {"wrong_radius": ("trigger_radius", 1), "nan_radius": ("trigger_radius", float("nan")), "wrong_sound": ("sound_type", "weapon_fire_pistol"), "wrong_type": ("type", "wall"), "inactive_door": ("active", False), "transparent_door": ("blocks_sight", False), "passable_door": ("blocks_movement", False)}[damage]
        wall[field] = value
    elif damage == "wrong_puzzle":
        next(p for p in level["puzzles"] if p["id"] == locked["puzzle_id"])["definition_id"] = "missing"
    elif damage in ("duplicate_connector", "duplicate_port"):
        duplicate = copy.deepcopy(automatic)
        if damage == "duplicate_port":
            duplicate["id"] = "duplicate"
        level["connectors"].append(duplicate)
    elif damage in ("duplicate_puzzle", "rogue_puzzle"):
        duplicate = copy.deepcopy(level["puzzles"][0])
        if damage == "rogue_puzzle":
            duplicate.update(id="rogue", door_id=wall["id"])
        level["puzzles"].append(duplicate)
    elif damage in ("duplicate_wire", "rogue_wire"):
        duplicate = copy.deepcopy(level["wires"][0])
        if damage == "rogue_wire":
            duplicate["puzzle_id"] = "rogue"
        level["wires"].append(duplicate)
    elif damage == "rogue_door":
        duplicate = copy.deepcopy(wall)
        duplicate.update(id="rogue", x=0, y=0)
        duplicate.pop("threshold_id")
        duplicate.pop("mode")
        level["walls"].append(duplicate)
    else:
        prefix = passage["id"].replace("conn_", "flank_") + "_"
        level["walls"] = [w for w in level["walls"] if not w["id"].startswith(prefix)]
    with pytest.raises(ValueError):
        validate_generated_map(level)


def automatic_room(game_room, *, horizontal=False, radius=2.4):
    player = game_room.add_player("p")
    wall = {"id": "automatic", "threshold_id": "automatic_door", "x": 5, "y": 1, "w": .8, "h": 12, "active": True, "blocks_sight": True}
    doors.configure_threshold(wall, CATALOG)
    wall["trigger_radius"] = radius
    if horizontal:
        wall.update(x=1, y=5, w=12, h=.8)
    player.update(x=2, y=2, footstep_timer=10)
    game_room.map_data = {"world_id": "threshold_test", "world_kind": "mission", "walls": [wall], "puzzles": [], "enemies": [], "crates": [], "loot": [], "geometry_revision": 0,
                          "capabilities": {**mutable_copy(CATALOG.definitions["world_rules"]["hub"]), "objectives": False}}
    game_room.phase = "active"
    return player, wall


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("side", [-1, 1])
def test_proximity_radius_is_actual_rectangle_distance_and_events_once(game_room, horizontal, side):
    player, wall = automatic_room(game_room, horizontal=horizontal, radius=1.25)
    normal = wall["y"] if horizontal else wall["x"]
    extent = wall["h"] if horizontal else wall["w"]
    normal += extent if side == 1 else 0
    def position(distance):
        player.update(x=2 if horizontal else normal + side * distance, y=normal + side * distance if horizontal else 2)
    position(1.2501)
    game_room.step(0)
    assert wall["active"] and game_room.map_data["geometry_revision"] == 0
    assert not game_room.sound_events
    assert not has_line_of_sight(2, 2, 8, 2, [wall]) if not horizontal else not has_line_of_sight(2, 2, 2, 8, [wall])
    position(1.25)
    game_room.step(0)
    assert all(wall[key] is False for key in ("active", "blocks_movement", "blocks_projectile", "blocks_sight"))
    assert wall["state"] == "open" and game_room.map_data["geometry_revision"] == 1
    event = game_room.sound_events[0]
    assert event["sound_type"] == "door_operate" and event["source_id"] == wall["id"]
    assert event["world_id"] == "threshold_test" and event["event_id"]
    position(10)
    for _ in range(5):
        game_room.step(.05)
    assert not wall["active"] and game_room.map_data["geometry_revision"] == 1
    assert len(game_room.sound_events) == 1 and game_room.sound_events[0]["event_id"] == event["event_id"]


@pytest.mark.parametrize("status", ["downed", "zero_hp", "pending_disconnect", "movement_disabled"])
def test_ineligible_players_cannot_trigger_automatic_door(game_room, status):
    player, wall = automatic_room(game_room)
    player.update(x=4, y=2)
    if status == "downed":
        player["downed"] = True
    elif status == "zero_hp":
        player["hp"] = 0
    elif status == "pending_disconnect":
        game_room.pending_disconnects.add(player["id"])
    else:
        game_room.map_data["capabilities"]["movement"] = False
    doors.update_proximity_doors(game_room)
    assert wall["active"] and not game_room.sound_events and game_room.map_data["geometry_revision"] == 0


@pytest.mark.parametrize("horizontal", [False, True])
def test_door_opens_during_movement_substeps_without_stuck_tick(game_room, horizontal):
    player, wall = automatic_room(game_room, horizontal=horizontal, radius=.8)
    player["y" if horizontal else "x"] = 4
    # Start outside the trigger and cross the entire slab in one authoritative step.
    before = (player["x"], player["y"])
    vx, vy = (0, 9) if horizontal else (9, 0)
    blocked = move_with_collisions(*before, vx, vy, .5, player["radius"], [wall])
    assert blocked[1 if horizontal else 0] < 5
    game_room.update_input("p", {"dx": 0 if horizontal else 1, "dy": 1 if horizontal else 0, "running": True,
                                 "aim_x": 4 if horizontal else 10, "aim_y": 10 if horizontal else 2})
    game_room.step(.5)
    assert player["y" if horizontal else "x"] > 5.8
    assert not wall["active"] and game_room.map_data["geometry_revision"] == 1
    assert len([s for s in game_room.sound_events if s["sound_type"] == "door_operate"]) == 1


def test_authored_hub_proximity_threshold_uses_noncombat_movement(game_room):
    data = mutable_copy(CATALOG.definitions)
    data["worlds"]["flagship_stub"]["walls"].append({"x": 4, "y": 1, "w": .8, "h": 12, "active": True, "blocks_sight": True, "is_door": True, "material": "door", "threshold_id": "automatic_door"})
    catalog = validate_content(data)
    room = GameRoom(game_room.level_store, game_room.shared_stash, catalog=catalog, hub_world="flagship_stub")
    room.reset_to_lobby()
    player = room.add_player("p")
    player.update(x=3, y=2)
    door = room.map_data["walls"][-1]
    room.update_input("p", {"dx": 1, "dy": 0, "running": True, "aim_x": 10, "aim_y": 2})
    room.step(.5)
    assert player["x"] > 4.8 and not door["active"]
    assert room.phase == "lobby" and room.mission_time == 0 and not room.projectiles


@pytest.mark.parametrize("policy_id", ["open_passage", "automatic_door", "power_lock", "mixed"])
def test_extraction_dependencies_only_include_actual_puzzles(game_room, monkeypatch, policy_id):
    data = mutable_copy(CATALOG.definitions)
    if policy_id != "mixed":
        data["missions"]["outpost"]["threshold_weights"] = {policy_id: 1}
    catalog = install_catalog(monkeypatch, data)
    level = build_mission_map("outpost", 101)
    level["world_id"] = "objective_test"
    game_room.map_data = level
    game_room.catalog = catalog
    objectives.map_objectives(level, catalog=catalog)
    required = {r["target_id"] for r in level["objective_definitions"] if r["condition"] == "puzzle_solved" and r["required"]}
    assert required == {p["id"] for p in level["puzzles"]}
    assert not level["extraction_zone"]["active"]
    objectives.update(game_room)
    assert level["extraction_zone"]["active"] == (not required)
    for puzzle in level["puzzles"]:
        puzzle["solved"] = True
    objectives.update(game_room)
    assert level["extraction_zone"]["active"]
    assert all(not w["active"] for w in level["walls"] if w.get("mode") == "puzzle")
    assert all(c["locked"] for c in level["crates"] if not c["puzzle"]["solved"])
    revision, events = level.get("geometry_revision", 0), len(game_room.sound_events)
    objectives.update(game_room)
    assert level.get("geometry_revision", 0) == revision and len(game_room.sound_events) == events


def test_old_version_snapshot_keeps_progress_layout_bytes_and_puzzle_mechanics(game_room, monkeypatch):
    record = game_room.level_store.levels["outpost_101"]
    record.update(generator_version="wfc-2", content_version="foundation-2", tested=True, completed=True)
    game_room.level_store.save()
    before = copy.deepcopy(game_room.level_store.levels)
    digest = hashlib.sha256(game_room.level_store.data_file.read_bytes()).hexdigest()
    def forbidden(*args, **kwargs):
        pytest.fail("old layout unexpectedly validated, regenerated, or saved")
    monkeypatch.setattr("backend.app.engine.level_store.validate_generated_map", forbidden)
    monkeypatch.setattr("backend.app.engine.level_store.build_mission_map", forbidden)
    monkeypatch.setattr(game_room.level_store, "save", forbidden)
    game_room.add_player("p")
    for _ in range(2):
        assert game_room.start_mission("p")
        game_room.phase = "active"
        puzzle = game_room.map_data["puzzles"][0]
        wall = next(w for w in game_room.map_data["walls"] if w["id"] == puzzle["door_id"])
        game_room.players["p"].update(x=puzzle["x"], y=puzzle["y"])
        doors.update_proximity_doors(game_room)
        assert wall["active"]
        game_room._complete_puzzle(puzzle, "p")
        assert puzzle["solved"] and not wall["active"]
        game_room.reset_to_lobby()
    assert game_room.level_store.levels == before
    assert hashlib.sha256(game_room.level_store.data_file.read_bytes()).hexdigest() == digest
