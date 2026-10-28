"""Stage 06 generation gates and non-destructive legacy compatibility."""

import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest

from backend.app.content import CATALOG, CONTENT_PATH, validate_content
from backend.app.config.missions import MISSIONS
from backend.app.engine import map_builder, wfc
from backend.app.engine.level_store import LevelStore
from backend.app.engine.map_builder import astar_wire_path, build_mission_map, validate_generated_map, verify_level_solvability
from backend.app.engine.physics import collision_path_clear, swept_circle_intersects_aabb


@pytest.fixture(scope="module")
def generated():
    return build_mission_map("outpost", 101)


@pytest.mark.parametrize("mission_id", sorted(MISSIONS))
def test_representative_seed_sweep_all_missions(mission_id):
    mission = MISSIONS[mission_id]
    for seed in (0, 1, 2, 17, 42, 101, 202, 303, 777, 98765):
        level = build_mission_map(mission_id, seed)
        validate_generated_map(level)
        assert mission["min_segments"] <= len(level["segments"]) <= mission["max_segments"]
        assert set(mission["required_templates"]).issubset(s["id"] for s in level["segments"])
        assert level["requested_seed"] == seed
        assert level["resolved_seed"] == level["seed"] == seed + 17 * (level["generation_attempts"] - 1)
        assert level["content_version"] == CATALOG.definitions["content_version"]
        assert level["generator_version"] == wfc.GENERATOR_VERSION
        assert len(level["generation_diagnostics"]) == level["generation_attempts"] - 1
        assert all(d["reason"] and type(d["seed"]) is int for d in level["generation_diagnostics"])
        assert level["segments"][level["start_segment_index"]]["role"] == "start"
        assert level["segments"][level["end_segment_index"]]["role"] == "end"


def test_fresh_processes_are_structurally_identical_across_hash_seeds(tmp_path):
    script = r'''
import hashlib, json
from backend.app.engine.map_builder import build_mission_map
from backend.app.config.missions import MISSIONS
for mission in sorted(MISSIONS):
    for seed in (0, 101, 777):
        level = build_mission_map(mission, seed)
        print(mission, seed, hashlib.sha256(json.dumps(level, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
'''
    outputs = []
    for hash_seed in ("0", "1", "2", "137", "random"):
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                env={**os.environ, "PYTHONHASHSEED": hash_seed, "SWARM_SAVE_DIR": str(tmp_path)},
                                check=True, timeout=90)
        outputs.append(result.stdout)
    assert all(output == outputs[0] for output in outputs)
    assert len(outputs[0].splitlines()) == 9
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("damage", ["wallless", "missing_perimeter", "thin_barrier", "blocked_spawn", "blocked_enemy", "blocked_puzzle", "blocked_crate", "blocked_extraction", "invalid_port", "invalid_role", "missing_required", "too_few", "too_many", "disconnected", "wire_crosses_cover"])
def test_rejects_bad_geometry_and_final_constraints(generated, damage):
    level = copy.deepcopy(generated)
    start = level["segments"][level["start_segment_index"]]
    if damage == "wallless":
        level["walls"] = []
    elif damage == "missing_perimeter":
        level["walls"] = [w for w in level["walls"] if w["id"] != next(w["id"] for w in level["walls"] if w["id"].startswith("wall_") and "_int_" not in w["id"])]
    elif damage == "thin_barrier":
        # An enclosed pocket between integer samples: endpoint-only fill tunnels in.
        ext = level["extraction_zone"]
        x, y = ext["x"], ext["y"] + 0.45
        level["walls"].extend([
            {"id": "thin_top", "x": x, "y": y, "w": ext["w"], "h": 0.05, "active": True},
            {"id": "thin_bottom", "x": x, "y": y + 5, "w": ext["w"], "h": 0.05, "active": True},
            {"id": "thin_left", "x": x, "y": y, "w": 0.05, "h": 5, "active": True},
            {"id": "thin_right", "x": x + ext["w"], "y": y, "w": 0.05, "h": 5, "active": True},
        ])
        level["extraction_zone"] = {"x": x + 0.6, "y": y + 0.6, "w": ext["w"] - 1.2, "h": 3.8}
    elif damage in ("blocked_spawn", "blocked_enemy", "blocked_puzzle", "blocked_crate", "blocked_extraction"):
        if damage == "blocked_spawn":
            x, y = level["player_spawn_points"][-1]
        elif damage == "blocked_enemy":
            x, y = level["enemies"][0]["x"], level["enemies"][0]["y"]
        elif damage == "blocked_puzzle":
            x, y = level["puzzles"][0]["x"], level["puzzles"][0]["y"]
        elif damage == "blocked_crate":
            x, y = level["crates"][0]["x"], level["crates"][0]["y"]
        else:
            ext = level["extraction_zone"]
            x, y = ext["x"] + ext["w"] / 2, ext["y"] + ext["h"] / 2
        level["walls"].append({"id": "blocked", "x": x - 3, "y": y - 3, "w": 6, "h": 6, "active": True, "blocks_sight": False})
    elif damage == "invalid_port":
        level["connectors"][0]["gx"] += 1
    elif damage == "invalid_role":
        start["role"] = "room"
    elif damage == "missing_required":
        for s in level["segments"]:
            if s["id"] == "command_hub":
                s["id"] = "security_corridor"
    elif damage == "too_few":
        level["segments"] = level["segments"][:2]
    elif damage == "too_many":
        level["segments"].append(copy.deepcopy(start))
    elif damage == "disconnected":
        level["connectors"] = []
    else:
        a, b = level["wires"][0]["path"][:2]
        level["walls"].append({"id": "wire_cover", "x": (a[0] + b[0]) / 2 - 0.025, "y": (a[1] + b[1]) / 2 - 0.025, "w": 0.05, "h": 0.05, "active": True, "blocks_sight": False})
    assert not verify_level_solvability(level)
    with pytest.raises(ValueError, match="Unreachable extraction footprint" if damage == "thin_barrier" else None):
        validate_generated_map(level)


def test_swept_footprint_and_wire_routing_reject_thin_barriers():
    thin = {"id": "thin", "x": 4.45, "y": 0, "w": 0.05, "h": 10, "active": True}
    assert swept_circle_intersects_aabb(4, 5, 5, 5, 0, thin)
    assert not collision_path_clear((4, 5), (5, 5), CATALOG.definitions["rules"]["player"]["radius"], [thin])
    segments = [{"x": 0, "y": 0, "w": 10, "h": 10}]
    with pytest.raises(ValueError, match="No collision-free wire route"):
        astar_wire_path((2, 5), (8, 5), segments, [thin], set())
    cover = {"id": "cover", "x": 4, "y": 3, "w": 2, "h": 4, "active": True, "blocks_sight": False}
    path = astar_wire_path((2.1, 5.1), (8.1, 5.1), segments, [cover], set())
    assert path[0] == (2.1, 5.1) and path[-1] == (8.1, 5.1)
    assert all(collision_path_clear(a, b, 0.22, [cover]) for a, b in zip(path, path[1:]))


def test_player_radius_catalog_controls_actual_validation(generated, monkeypatch):
    rules = {key: dict(value) for key, value in map_builder.RULES.items()}
    rules["player"]["radius"] = 2.1
    monkeypatch.setattr(map_builder, "RULES", rules)
    with pytest.raises(ValueError, match="Blocked or misplaced connector"):
        validate_generated_map(generated)
    # Radius, not node spacing or sight flags, decides clearance in a narrow passage.
    walls = [{"x": 0, "y": 0, "w": 10, "h": 4.5},
             {"x": 0, "y": 5.5, "w": 10, "h": 4.5, "blocks_sight": False}]
    assert collision_path_clear((1, 5), (9, 5), CATALOG.definitions["rules"]["player"]["radius"], walls)
    assert not collision_path_clear((1, 5), (9, 5), 0.6, walls)


def test_geometry_validation_is_distinct_from_testing_progress(game_room):
    record = game_room.level_store.generate_level("outpost", 101)
    assert record["geometry_validated"] and not record["tested"] and not record["completed"]
    assert record["status"] == "uncompleted"
    assert game_room.level_store.instantiate_map(record["level_id"])["enemies"]
    original = copy.deepcopy(record["map_template"])
    game_room.level_store.mark_completed(record["level_id"])
    assert record["map_template"] == original
    assert record["tested"] and record["completed"]


def test_wfc_alone_has_stable_authoritative_counts():
    for mission in MISSIONS:
        a, b = wfc.run_wfc(mission, 101), wfc.run_wfc(mission, 101)
        assert a == b
        assert MISSIONS[mission]["min_segments"] <= len(a["segments"]) <= MISSIONS[mission]["max_segments"]


def test_retry_exhaustion_is_bounded_diagnostic_and_cannot_persist(game_room, monkeypatch):
    before = copy.deepcopy(game_room.level_store.levels)
    saved = game_room.level_store.data_file.read_bytes()
    attempts = []
    def always_reject(mission, seed, diagnostics):
        attempts.append(seed)
        diagnostics.append({"seed": seed, "reason": "fixture impossible required room"})
    monkeypatch.setattr(map_builder, "_try_seed", always_reject)
    with pytest.raises(wfc.GenerationError, match="fixture impossible required room") as caught:
        game_room.level_store.generate_level("outpost", seed=100)
    assert len(attempts) == wfc.MAX_ATTEMPTS
    assert len(caught.value.diagnostics) == wfc.MAX_ATTEMPTS
    assert game_room.level_store.levels == before
    assert game_room.level_store.data_file.read_bytes() == saved


def test_invalid_builder_output_is_rejected_before_store_or_launch(game_room, generated, monkeypatch):
    invalid = copy.deepcopy(generated)
    invalid["walls"] = []
    monkeypatch.setattr("backend.app.engine.level_store.build_mission_map", lambda *a, **kw: invalid)
    before = copy.deepcopy(game_room.level_store.levels)
    with pytest.raises(ValueError, match="collision enclosure"):
        game_room.level_store.generate_level("outpost", 123)
    assert game_room.level_store.levels == before
    record = game_room.level_store.levels["outpost_101"]
    record["generator_version"] = wfc.GENERATOR_VERSION
    record["content_version"] = CATALOG.definitions["content_version"]
    record["map_template"] = invalid
    with pytest.raises(ValueError, match="collision enclosure"):
        game_room.level_store.instantiate_map("outpost_101")


@pytest.mark.parametrize("count", [0, 1])
def test_legacy_sparse_snapshot_repeated_launches_never_regenerate_or_write(game_room, monkeypatch, count):
    store = game_room.level_store
    record = store.levels["outpost_101"]
    record["tested"] = True
    record["completed"] = True
    record["map_template"]["loot"] = record["map_template"]["loot"][:count]
    record["map_template"]["crates"] = []
    store.save()
    before = copy.deepcopy(store.levels)
    digest = hashlib.sha256(store.data_file.read_bytes()).hexdigest()
    def forbidden(*a, **kw):
        pytest.fail("snapshot launch attempted regeneration or persistence")
    monkeypatch.setattr(store, "save", forbidden)
    monkeypatch.setattr("backend.app.engine.level_store.build_mission_map", forbidden)
    assert verify_level_solvability(record["map_template"])  # Sparse loot does not invalidate geometry.
    game_room.add_player("p")
    runtime_ids = set()
    for _ in range(3):
        assert game_room.start_mission("p")
        snapshot = game_room.map_data
        assert [{k: v for k, v in wall.items() if k not in ("is_door", "material")} for wall in snapshot["walls"]] == [{k: v for k, v in wall.items() if k not in ("is_door", "material")} for wall in record["map_template"]["walls"]]
        for item in snapshot["loot"]:
            assert item["id"] not in runtime_ids
            runtime_ids.add(item["id"])
        game_room.reset_to_lobby()
    assert store.levels == before
    assert hashlib.sha256(store.data_file.read_bytes()).hexdigest() == digest


def test_generated_zero_loot_uses_catalog_and_keeps_snapshot(tmp_path, monkeypatch):
    data = json.loads(CONTENT_PATH.read_text())
    for segment in data["segments"].values():
        segment["loot_count"] = 0
        segment["loot_spawns"] = []
        segment.pop("crate_spawn", None)
    catalog = validate_content(data)
    monkeypatch.setattr(map_builder, "SEGMENT_TEMPLATES", catalog.definitions["segments"])
    store = LevelStore(tmp_path / "levels.json")
    record = store.generate_level("outpost", 101)
    assert record["map_template"]["loot"] == record["map_template"]["crates"] == []
    before = store.data_file.read_bytes()
    for _ in range(2):
        assert store.instantiate_map(record["level_id"])["loot"] == []
    assert store.data_file.read_bytes() == before


def test_content_upgrade_does_not_revalidate_or_regenerate_saved_layout(game_room, generated, monkeypatch):
    record = game_room.level_store.levels["outpost_101"]
    record.update(generator_version=wfc.GENERATOR_VERSION, content_version="older-content")
    record["map_template"] = copy.deepcopy(generated)
    record["map_template"]["content_version"] = "older-content"
    before = copy.deepcopy(record)
    def forbidden(*a, **kw):
        pytest.fail("content upgrade implicitly validated/repaired a retained snapshot")
    monkeypatch.setattr("backend.app.engine.level_store.validate_generated_map", forbidden)
    monkeypatch.setattr(game_room.level_store, "save", forbidden)
    game_room.level_store.instantiate_map("outpost_101")
    assert record == before


def test_endpoint_roles_and_crate_spawns_not_names(monkeypatch):
    data = json.loads(CONTENT_PATH.read_text())
    renames = {"drop_hangar": "insertion_room", "extraction_pad": "departure_room"}
    for old, new in renames.items():
        segment = data["segments"].pop(old)
        segment["id"], segment["name"] = new, "Unrelated authored label"
        data["segments"][new] = segment
    for mission in data["missions"].values():
        mission["allowed_templates"] = [renames.get(t, t) for t in mission["allowed_templates"]]
    data["segments"]["insertion_room"]["crate_spawn"]["x"] = 9.0
    data["segments"]["insertion_room"]["crate_spawn"]["locked"] = True
    catalog = validate_content(data)
    for module in (map_builder, wfc):
        monkeypatch.setattr(module, "SEGMENT_TEMPLATES", catalog.definitions["segments"])
        monkeypatch.setattr(module, "MISSIONS", catalog.definitions["missions"])
    level = build_mission_map("outpost", 101)
    start = level["segments"][level["start_segment_index"]]
    crate = next(c for c in level["crates"] if c["segment_index"] == start["index"])
    assert start["id"] == "insertion_room"
    assert level["segments"][level["end_segment_index"]]["id"] == "departure_room"
    assert crate["x"] == start["x"] + 9 and crate["locked"]


@pytest.mark.parametrize("damage", ["missing_role", "required_unknown", "duplicate_allowed", "bad_crate"])
def test_authorable_generation_schema_rejects_invalid_constraints(damage):
    data = json.loads(CONTENT_PATH.read_text())
    if damage == "missing_role":
        data["segments"]["drop_hangar"]["role"] = "room"
    elif damage == "required_unknown":
        data["missions"]["outpost"]["required_templates"] = ["missing"]
    elif damage == "duplicate_allowed":
        data["missions"]["outpost"]["allowed_templates"].append("drop_hangar")
    else:
        data["segments"]["drop_hangar"]["crate_spawn"]["x"] = 100.0
    with pytest.raises(ValueError):
        validate_content(data)
