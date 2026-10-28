import copy
import json
import random
from pathlib import Path

import pytest

from backend.app.commands import parse_command
from backend.app.content import CATALOG, mutable_copy, validate_content, validate_objectives
from backend.app.engine import objectives, puzzles
from backend.app.engine.ai import update_enemies
from backend.app.main import dispatch_command


def fixture_catalog():
    data = mutable_copy(CATALOG.definitions)
    additions = json.loads((Path(__file__).parent / "fixtures" / "stage11-encounter.json").read_text())
    for section, values in additions.items():
        data[section].update(values)
    return validate_content(data)


@pytest.fixture
def encounter_room(game_room):
    room = game_room
    room.catalog = fixture_catalog()
    room.add_player("leader")
    room.add_player("peer")
    room.start_mission("leader", False)
    room.phase = "active"
    for p in room.players.values():
        p.update(x=10.0, y=10.0)
    puzzle = {"id": "qte", "type": "fixture_timed", "label": "Timed fixture", "x": 12, "y": 10,
              "radius": 2.8, "solved": False, "door_id": None, "data": puzzles.generate("fixture_timed", catalog=room.catalog)}
    puzzles.runtime_puzzle(puzzle, catalog=room.catalog)
    room.map_data.update(walls=[], puzzles=[puzzle], crates=[], enemies=[], loot=[], props=[])
    return room


def command(room, action, request="one", player="leader"):
    return dispatch_command(room, player, parse_command(json.dumps({"type": "puzzle_action", "puzzle_id": "qte", "action": action,
                            "request_id": request, "session_id": player, "world_id": room.map_data["world_id"]})))


def test_authoritative_attempt_valid_input_no_client_success_or_time(encounter_room):
    room = encounter_room
    room.simulation_time = 123
    result = command(room, {"action": "start_attempt"})
    session = result["puzzle"]["session"]
    assert session["started_at"] == 123 and session["deadline"] == 125
    assert session["player_id"] == "leader" and session["id"]
    for field in ("success", "elapsed", "deadline"):
        with pytest.raises(ValueError):
            parse_command(json.dumps({"type": "puzzle_action", "puzzle_id": "qte", "action": {"action": "timed_input", "symbol": "ACK", "attempt_id": session["id"], field: 0}}))
    room.step(1.999)
    result = command(room, {"action": "timed_input", "symbol": "ACK", "attempt_id": session["id"]}, "two")
    assert result["ok"] and result["solved"]
    replay = command(room, {"action": "timed_input", "symbol": "ACK", "attempt_id": session["id"]}, "three")
    assert not replay["ok"] and "replayed" in replay["message"]
    with pytest.raises(ValueError, match="Duplicate"):
        command(room, {"action": "start_attempt"})


@pytest.mark.parametrize("clock", [2, 2.001, 10])
def test_timeout_exact_server_boundary_and_tick_without_input(encounter_room, clock):
    room = encounter_room
    session = command(room, {"action": "start_attempt"})["puzzle"]["session"]
    room.step(clock)
    assert session["status"] == "expired"
    result = command(room, {"action": "timed_input", "symbol": "ACK", "attempt_id": session["id"]}, "two")
    assert not result["ok"] and "expired" in result["message"]
    assert not room.map_data["puzzles"][0]["solved"]


@pytest.mark.parametrize("action,player,error", [
    ({"action": "timed_input", "symbol": "ACK", "attempt_id": "forged"}, "leader", "session"),
    ({"action": "timed_input", "symbol": "ACK", "attempt_id": "actual"}, "peer", "player"),
    ({"action": "tech_bypass"}, "leader", "capability"),
    ({"action": "rotate", "node_index": 0}, "leader", "Incompatible"),
])
def test_wrong_attempt_owner_capability_and_incompatible_actions(encounter_room, action, player, error):
    session = command(encounter_room, {"action": "start_attempt"})["puzzle"]["session"]
    if action.get("attempt_id") == "actual":
        action["attempt_id"] = session["id"]
    before = copy.deepcopy(encounter_room.map_data)
    result = command(encounter_room, action, "two", player)
    assert not result["ok"] and error in result["message"]
    assert encounter_room.map_data == before


@pytest.mark.parametrize("denial", ["dead", "downed", "range", "los", "session", "world"])
def test_attempt_commands_keep_server_guards(encounter_room, denial):
    room = encounter_room
    p = room.players["leader"]
    if denial == "dead":
        p["hp"] = 0
    elif denial == "downed":
        p["downed"] = True
    elif denial == "range":
        p["x"] = 0
    elif denial == "los":
        room.map_data["walls"] = [{"id": "block", "x": 11, "y": 0, "w": .1, "h": 30, "active": True, "blocks_sight": True}]
    before = copy.deepcopy(room.map_data)
    if denial in ("session", "world"):
        data = {"type": "puzzle_action", "puzzle_id": "qte", "action": {"action": "start_attempt"},
                "request_id": "r", "session_id": "leader", "world_id": room.map_data["world_id"]}
        data["session_id" if denial == "session" else "world_id"] = "wrong"
        with pytest.raises(ValueError, match="Stale"):
            dispatch_command(room, "leader", data)
    else:
        assert not command(room, {"action": "start_attempt"})["ok"]
    assert room.map_data == before


def test_one_attempt_wrong_input_consumes_and_cannot_restart(encounter_room):
    room = encounter_room
    session = command(room, {"action": "start_attempt"})["puzzle"]["session"]
    assert command(room, {"action": "timed_input", "symbol": "WRONG", "attempt_id": session["id"]}, "two")["ok"]
    assert session["status"] == "consumed"
    assert not command(room, {"action": "start_attempt"}, "three")["ok"]
    assert not command(room, {"action": "timed_input", "symbol": "ACK", "attempt_id": session["id"]}, "four")["ok"]


def test_existing_mechanics_data_only_variants_and_coop(encounter_room):
    room = encounter_room
    for identity in ("fixture_power", "fixture_signal"):
        puzzle = {"id": "variant", "type": identity, "label": "variant", "solved": False, "x": 12, "y": 10,
                  "radius": 2.8, "door_id": None, "data": puzzles.generate(identity, rng=random.Random(1), catalog=room.catalog)}
        room.map_data["puzzles"] = [puzzle]
        room.map_data.pop("objectives", None)
        room.map_data.pop("objective_definitions", None)
        if identity == "fixture_power":
            assert len(puzzle["data"]["nodes"]) == 2
            for i in range(2):
                while puzzle["data"]["nodes"][i] != 0:
                    assert room.handle_puzzle_action("peer", "variant", {"action": "rotate", "node_index": i})["ok"]
        else:
            assert len(puzzle["data"]["target_sequence"]) == 2
            for symbol in puzzle["data"]["target_sequence"]:
                assert room.handle_puzzle_action("leader", "variant", {"action": "input_symbol", "symbol": symbol})["ok"]
        assert puzzle["solved"]
        assert puzzle["definition"]["handler"] != identity and puzzle["session"]["status"] == "solved"


def test_unknown_handlers_refs_and_strict_consumed_fields():
    for section, identity, field, value in [
        ("puzzles", "fixture_timed", "handler", "eval"),
        ("puzzles", "fixture_timed", "script", "import os"),
        ("attacks", "fixture_acid", "handler", "arbitrary_import"),
        ("attacks", "fixture_acid", "projectile_speed", 0),
        ("enemies", "fixture_boss", "attacks", ["unknown"]),
        ("enemies", "fixture_boss", "encounter_id", "unknown"),
        ("encounters", "fixture_phases", "phases", [{"id": "bad", "health_at_most": .5, "attacks": ["fixture_claw"]}]),
    ]:
        data = mutable_copy(fixture_catalog().definitions)
        data[section][identity][field] = value
        with pytest.raises(ValueError):
            validate_content(data)
    assert "fixture_timed" not in CATALOG.definitions["puzzles"]
    assert "fixture_boss" not in CATALOG.definitions["enemies"]
    assert not CATALOG.definitions["encounters"]


@pytest.mark.parametrize("records,error", [
    ([{"id": "a", "label": "a", "condition": "complete", "dependencies": ["b"]}, {"id": "b", "label": "b", "condition": "complete", "dependencies": ["a"]}], "cycle"),
    ([{"id": "a", "label": "a", "condition": "complete", "dependencies": ["missing"]}], "unknown"),
    ([{"id": "a", "label": "a", "condition": "puzzle_solved", "target_id": "missing"}], "unknown"),
    ([{"id": "a", "label": "a", "condition": "complete", "effects": [{"handler": "eval"}]}], "handler"),
    ([{"id": "a", "label": "a", "condition": "complete", "effects": [{"handler": "open_door", "target_id": "missing"}]}], "unknown"),
])
def test_objective_graph_rejection(records, error):
    with pytest.raises(ValueError, match=error):
        validate_objectives(records, puzzles=set(), enemies=set(), walls=set(), crates=set())


def test_zero_puzzle_objective_mapping_activates_extraction(encounter_room):
    room = encounter_room
    room.map_data["puzzles"] = []
    room.step(1 / 30)
    assert room.map_data["extraction_zone"]["active"]
    assert room.map_data["objectives"][-1]["completed"]


def test_mixed_dependencies_effects_once_and_boss_death_credit(encounter_room):
    room = encounter_room
    boss = {"id": "boss", "type": "fixture_boss", "faction": "swarm", "x": 20, "y": 10, "angle": 0,
            "hp": 100, "max_hp": 100, "radius": 1.1, "state": "patrol", "name": "fixture", "color": "#B03A2E", "attack_timer": 0}
    room.map_data["enemies"] = [boss]
    room.map_data["walls"] = [{"id": "door", "x": 25, "y": 10, "w": .5, "h": 2, "active": True, "blocks_sight": True}]
    room.map_data["crates"] = [{"id": "crate", "locked": True}]
    room.map_data["objective_definitions"] = [
        {"id": "input", "label": "Input", "condition": "puzzle_solved", "target_id": "qte"},
        {"id": "boss", "label": "Defeat boss", "condition": "enemy_dead", "target_id": "boss", "dependencies": ["input"], "effects": [{"handler": "open_door", "target_id": "door"}, {"handler": "unlock_crate", "target_id": "crate"}]},
        {"id": "extract", "label": "Extract", "condition": "complete", "dependencies": ["boss"], "effects": [{"handler": "activate_extraction"}]},
    ]
    room.damage(boss, 100, room.players["leader"], kind="projectile")
    room.damage(boss, 100, room.players["leader"], kind="melee")
    room.step(1 / 30)
    assert boss["encounter_phase"] == "dead"
    assert room.players["leader"]["kills"] == 1
    assert not any(o["completed"] for o in room.map_data["objectives"])
    session = command(room, {"action": "start_attempt"})["puzzle"]["session"]
    assert command(room, {"action": "timed_input", "symbol": "ACK", "attempt_id": session["id"]}, "two")["solved"]
    assert all(o["completed"] for o in room.map_data["objectives"])
    assert not room.map_data["walls"][0]["active"] and not room.map_data["crates"][0]["locked"]
    assert room.map_data["extraction_zone"]["active"]
    before = copy.deepcopy((room.map_data, room.events_log, room.event_sequence, room.interaction_revision))
    objectives.update(room)
    assert (room.map_data, room.events_log, room.event_sequence, room.interaction_revision) == before
    assert sum(s["kind"] == "alien_death" for s in room.sound_events) == 1


def test_boss_profiles_melee_ranged_cooldown_thresholds_and_normal_ai(encounter_room):
    room = encounter_room
    p = room.players["leader"]
    boss = {"id": "boss", "type": "fixture_boss", "faction": "swarm", "x": 9, "y": 10, "angle": 0, "hp": 100, "max_hp": 100,
            "radius": 1.1, "state": "patrol", "attack_timer": 0, "waypoints": [(9, 10), (8, 10)]}
    projectiles = []
    def update(dt=.01):
        update_enemies([boss], {"leader": p}, [], [], projectiles, dt, catalog=room.catalog, damage=room.damage, emit_sound=room._emit_sound)
    hp = p["hp"]
    update()
    assert boss["state"] == "engaged" and boss["encounter_phase"] == "guard"
    assert p["hp"] == hp - 8 and boss["attack_timer"] == .5  # existing Bulwark mitigation
    update(.1)
    assert p["hp"] == hp - 8
    p["x"] = 14
    update(.5)
    assert projectiles[-1]["damage"] == 7 and projectiles[-1]["vx"] == 9
    boss["hp"] = 50
    update(.01)
    assert boss["encounter_phase"] == "enraged" and boss["attack_profiles"] == ["fixture_heavy_claw", "fixture_acid"]
    assert boss["attack_timer"] > 0  # phase transition does not reset cooldown
    boss["hp"] = 80
    update(.01)
    assert boss["encounter_phase"] == "enraged"  # no phase regression on healing
    p["x"] = 10
    update(1)
    assert p["hp"] == hp - 8 - 16 and boss["attack_timer"] == .25
    boss["hp"] = 0
    update()
    assert boss["encounter_phase"] == "dead"
    assert CATALOG.definitions["attacks"]["stalker_claw"]["damage"] == 12
    assert CATALOG.definitions["attacks"]["spitter_acid"]["cooldown"] == 1.5
    assert CATALOG.definitions["attacks"]["brute_claw"]["range"] == 1.2


def test_legacy_runtime_mapping_keeps_layouts_and_real_store_bytes(game_room):
    before = copy.deepcopy(game_room.level_store.levels)
    bytes_before = game_room.level_store.data_file.read_bytes()
    one = game_room.level_store.instantiate_map("outpost_101", False)
    two = game_room.level_store.instantiate_map("outpost_101", False)
    original = before["outpost_101"]["map_template"]
    for p, old in zip(one["puzzles"], original["puzzles"]):
        assert p["data"] == old["data"]
        assert {k: v for k, v in p.items() if k not in ("definition", "definition_id", "session")} == old
    assert one["puzzles"][0]["session"]["id"] != two["puzzles"][0]["session"]["id"]
    assert game_room.level_store.levels == before
    assert game_room.level_store.data_file.read_bytes() == bytes_before


def test_authored_mission_objectives_consumed_and_cycles_rejected(encounter_room):
    data = mutable_copy(encounter_room.catalog.definitions)
    data["missions"]["outpost"]["objectives"] = [{"id": "authored", "label": "Authored input", "condition": "puzzle_solved", "target_id": "qte"}]
    encounter_room.catalog = validate_content(data)
    objectives.update(encounter_room)
    assert any(o["id"] == "authored" and not o["completed"] for o in encounter_room.map_data["objectives"])
    assert "authored" in encounter_room.map_data["objective_definitions"][-1]["dependencies"]
    data["missions"]["outpost"]["objectives"][0]["dependencies"] = ["authored"]
    with pytest.raises(ValueError, match="cycle"):
        validate_content(data)


@pytest.mark.parametrize("puzzle_type,action", [("power_reroute", {"action": "input_symbol", "symbol": "A7"}),
                                               ("signal_decrypt", {"action": "rotate", "node_index": 0})])
def test_existing_incompatible_actions_and_wrong_runtime_session_never_mutate(encounter_room, puzzle_type, action):
    room = encounter_room
    puzzle = room.map_data["puzzles"][0]
    puzzle.update(type=puzzle_type, data=puzzles.generate(puzzle_type), solved=False)
    puzzle.pop("definition")
    puzzles.runtime_puzzle(puzzle)
    before = copy.deepcopy(puzzle["data"])
    assert not room.handle_puzzle_action("leader", "qte", action)["ok"]
    correct = {"action": "rotate", "node_index": 0} if puzzle_type == "power_reroute" else {"action": "input_symbol", "symbol": puzzle["data"]["target_sequence"][0]}
    assert not room.handle_puzzle_action("leader", "qte", {**correct, "puzzle_session_id": "old-session"})["ok"]
    assert puzzle["data"] == before


def test_invalid_objective_preflight_cannot_solve_puzzle_or_apply_effect(encounter_room):
    room = encounter_room
    room.map_data["objective_definitions"] = [{"id": "bad", "label": "bad", "condition": "complete", "effects": [{"handler": "open_door", "target_id": "missing"}]}]
    before = copy.deepcopy(room.map_data["puzzles"][0])
    result = command(room, {"action": "start_attempt"})
    assert not result["ok"] and "unknown" in result["message"]
    assert room.map_data["puzzles"][0] == before


def test_normal_patrol_hearing_chase_and_default_ranged_profile_preserved(encounter_room):
    room = encounter_room
    enemy = {"id": "normal", "type": "spitter", "x": 0, "y": 0, "hp": 35, "max_hp": 35, "radius": .6,
             "angle": 0, "state": "patrol", "attack_timer": 0, "waypoints": [(2, 0)], "waypoint_index": 0}
    update_enemies([enemy], {}, [], [], [], .5)
    assert enemy["x"] == 1.25 and enemy["state"] == "patrol"
    update_enemies([enemy], {}, [], [{"x": 5, "y": 0, "radius": 14}], [], .1)
    assert enemy["state"] == "alerted" and enemy["investigate_target"] == (5, 0)
    p = room.players["leader"]
    p.update(x=enemy["x"] + 9, y=0)
    before = enemy["x"]
    shots = []
    update_enemies([enemy], {"leader": p}, [], [], shots, .1)
    assert enemy["state"] == "engaged" and enemy["x"] == pytest.approx(before + .48) and not shots
    p["x"] = enemy["x"] + 8
    update_enemies([enemy], {"leader": p}, [], [], shots, .1)
    assert shots[0]["damage"] == 9 and shots[0]["vx"] == 9 and enemy["attack_timer"] == 1.5


def test_launch_unknown_objective_reference_rejected_before_world_publication(game_room):
    game_room.add_player("p")
    data = mutable_copy(CATALOG.definitions)
    data["missions"]["outpost"]["objectives"] = [{"id": "bad", "label": "bad", "condition": "enemy_dead", "target_id": "missing"}]
    game_room.catalog = validate_content(data)
    before = copy.deepcopy(game_room.players)
    with pytest.raises(ValueError, match="unknown objective target"):
        game_room.start_mission("p", False)
    assert game_room.map_data is None and game_room.phase == "lobby" and game_room.players == before


def test_saved_definition_variant_retains_authored_parameters_when_catalog_changes(game_room):
    record = game_room.level_store.levels["outpost_101"]
    puzzle = record["map_template"]["puzzles"][0]
    puzzle["definition"] = {"handler": "power_reroute", "node_count": 2, "allow_tech_bypass": False}
    puzzle["data"] = {"type": "power_reroute", "nodes": [1, 2], "target": [0, 0]}
    game_room.level_store.save()
    before = game_room.level_store.data_file.read_bytes()
    runtime = game_room.level_store.instantiate_map("outpost_101", False)["puzzles"][0]
    assert runtime["definition"] == puzzle["definition"] and runtime["data"] == puzzle["data"]
    assert runtime["session"]["id"] and "session" not in puzzle
    assert game_room.level_store.data_file.read_bytes() == before


def test_boss_ranged_shot_uses_actual_shared_projectile_damage(encounter_room):
    room = encounter_room
    room.map_data["puzzles"] = []
    room.players["peer"].update(x=30, y=30)
    boss = {"id": "boss", "type": "fixture_boss", "faction": "swarm", "name": "fixture", "color": "#B03A2E",
            "x": 15, "y": 10, "angle": 3.141592653589793, "hp": 100, "max_hp": 100, "radius": 1.1,
            "state": "patrol", "attack_timer": 0, "waypoints": [(15, 10)]}
    room.map_data["enemies"] = [boss]
    hp = room.players["leader"]["hp"]
    room.step(1 / 30)
    assert room.projectiles and room.projectiles[0]["damage"] == 7
    for _ in range(16):
        room.step(1 / 30)
    assert room.players["leader"]["hp"] == hp - 7
    assert room.players["leader"]["last_damage"] == {"owner_id": "boss", "kind": "projectile"}
