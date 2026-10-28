"""Stage 08 event, listener, lifecycle and explicit acoustic geometry acceptance."""

import copy
from unittest.mock import patch

import pytest

from backend.app.content import CATALOG, RULES, mutable_copy, validate_content
from backend.app.engine.audio_events import AUDIO_CONFIGS, create_audio_event
from backend.app.engine.sound_occlusion import calculate_occlusion, clear_occlusion_cache, _occlusion_cache


def arena(room):
    a, b = room.add_player("a"), room.add_player("b")
    assert room.start_mission("a", False)
    room.phase = "active"
    room.map_data.update(walls=[], enemies=[], puzzles=[], crates=[], loot=[],
                         extraction_zone={"x": 200, "y": 200, "w": 2, "h": 2, "active": False})
    a.update(x=0, y=0)
    b.update(x=6, y=0)
    return a, b


def alien(room, x=10, y=0):
    cfg = CATALOG.definitions["enemies"]["stalker"]
    enemy = {"id": "e", "type": "stalker", "name": cfg["name"], "color": cfg["color"],
             "hp": cfg["max_hp"], "max_hp": cfg["max_hp"], "radius": cfg["radius"],
             "state": "patrol", "x": x, "y": y, "angle": 0, "attack_timer": 0,
             "waypoints": [(x, y)], "waypoint_index": 0}
    room.map_data["enemies"].append(enemy)
    return enemy


def test_command_alarm_survives_broadcasts_and_enemy_hears_once(game_room):
    a, b = arena(game_room)
    b.update(x=0, y=20)
    target = alien(game_room)
    puzzle = {"id": "alarm", "type": "signal_decrypt", "label": "Security", "x": 0, "y": 0,
              "radius": 3, "solved": False, "data": {"progress": 0, "target_sequence": ["A7"], "options": ["A7", "B5"]}}
    game_room.map_data["puzzles"] = [puzzle]
    assert game_room.handle_puzzle_action("a", "alarm", {"action": "input_symbol", "symbol": "B5"})["ok"]
    event = game_room.pending_sound_events[0]
    assert event["kind"] == "alarm" and event["radius"] == RULES["interaction"]["alarm_radius"]
    assert "sound_type" not in event  # No alarm recording exists in the real catalog.
    before = copy.deepcopy(game_room.pending_sound_events)
    for _ in range(3):
        game_room.get_snapshot("a")
        game_room.get_snapshot("b")
    assert game_room.pending_sound_events == before
    game_room.step(1 / 30)
    assert target["state"] == "alerted" and target["investigate_target"] == (0, 0)
    first_timer = target["alert_timer"]
    assert game_room.pending_sound_events == []
    game_room.step(1 / 30)
    assert target["alert_timer"] == pytest.approx(first_timer - 1 / 30)


def test_two_listener_ids_pure_history_and_distinct_mix(game_room):
    arena(game_room)
    game_room.map_data["walls"] = [{"id": "arbitrary", "x": 3, "y": -1, "w": 0.1, "h": 2,
                                    "active": True, "is_door": False, "material": "solid"}]
    game_room._emit_sound(0, 0, 14, "reload", sound_type="weapon_reload_shotgun", source_id="a")
    original = copy.deepcopy((game_room.sound_events, game_room.pending_sound_events, game_room.recent_sounds))
    a = game_room.get_snapshot("a")["audio_events"][0]
    b = game_room.get_snapshot("b")["audio_events"][0]
    assert (a["event_id"], a["sequence"], a["timestamp"], a["world_id"]) == (b["event_id"], b["sequence"], b["timestamp"], b["world_id"])
    assert a["occl_gain"] == 1 and b["occl_gain"] < 1
    assert game_room.get_snapshot("a")["audio_events"][0] == a
    assert (game_room.sound_events, game_room.pending_sound_events, game_room.recent_sounds) == original
    assert a["radius_max"] != game_room.pending_sound_events[0]["radius"]


@pytest.mark.parametrize("phase", ["victory", "defeat", "active"])
def test_expiry_terminal_frames_and_join_floor_no_replay(game_room, phase):
    arena(game_room)
    game_room._emit_sound(0, 0, 1, sound_type="weapon_fire_pistol")
    event = copy.deepcopy(game_room.sound_events[0])
    assert game_room.get_snapshot("a")["audio_events"]
    game_room.add_player("late")
    game_room.players["late"].update(x=0, y=0)
    assert game_room.get_snapshot("late")["audio_events"] == []
    game_room.phase = phase
    game_room.step(RULES["audio"]["history_duration"])
    assert game_room.get_snapshot("a")["audio_events"] == []
    assert game_room.sound_events == [] and game_room.recent_sounds == []
    game_room.sound_events.append(event)  # Pure expiry also protects unpruned history.
    assert game_room.get_snapshot("a")["audio_events"] == []


def test_world_reset_monotonic_identity_and_previous_world_filter(game_room):
    arena(game_room)
    game_room._emit_sound(0, 0, 2, sound_type="weapon_fire_pistol")
    old = copy.deepcopy(game_room.sound_events[0])
    game_room.reset_to_lobby()
    lobby = game_room.get_snapshot("a")
    assert lobby["music"]["state"] == "lobby" and lobby["world_id"] is None
    assert lobby["audio_events"] == [] and not game_room.pending_sound_events
    game_room.step(0.2)
    assert game_room.start_mission("a", False)
    game_room._emit_sound(0, 0, 1, sound_type="weapon_fire_pistol")
    new = game_room.sound_events[0]
    assert new["sequence"] > old["sequence"] and new["timestamp"] > old["timestamp"]
    assert new["world_id"] != old["world_id"] and new["event_id"] != old["event_id"]
    game_room.sound_events.insert(0, old)
    assert all(e["world_id"] == new["world_id"] for e in game_room.get_snapshot()["audio_events"])
    assert game_room.get_snapshot()["music"]["start_t"] == new["timestamp"]


def test_presentation_bounded_without_dropping_pending_hearing(game_room):
    arena(game_room)
    for _ in range(RULES["audio"]["history_limit"] + 20):
        game_room._emit_sound(0, 0, 1, "alarm")
    assert len(game_room.sound_events) == len(game_room.recent_sounds) == RULES["audio"]["history_limit"]
    assert len(game_room.pending_sound_events) == RULES["audio"]["history_limit"] + 20
    seen = []
    with patch("backend.app.engine.game_loop.update_enemies", side_effect=lambda *args, **kwargs: seen.extend(args[3])):
        game_room.step(1 / 30)
        game_room.step(1 / 30)
    assert len(seen) == RULES["audio"]["history_limit"] + 20
    assert len({e["event_id"] for e in seen}) == len(seen)


def test_actual_engagement_music_attack_death_and_door_catalog_paths(game_room):
    a, _ = arena(game_room)
    # Keep extraction inactive while isolating engagement music; zero-puzzle missions now complete.
    game_room.map_data["puzzles"] = [{"id": "pending", "label": "Pending", "solved": False}]
    e = alien(game_room, x=1)
    game_room.step(1 / 30)
    assert e["state"] == "engaged" and game_room.get_snapshot("a")["music"]["state"] == "combat"
    assert any(s.get("sound_type") == "alien_attack" for s in game_room.sound_events)
    game_room.damage(e, 10000, a, kind="projectile")
    game_room.damage(e, 10000, a, kind="area")
    assert sum(s.get("sound_type") == "alien_death" for s in game_room.sound_events) == 1
    assert game_room.get_snapshot("a")["music"]["state"] == "explore"
    ramp_id = game_room.map_data["spaceship"]["door_wall_id"]
    game_room.map_data["walls"] = [{"id": ramp_id, "x": 3, "y": 0, "w": 1, "h": 1, "active": True, "is_door": True, "material": "door"}]
    game_room.open_spaceship_ramp()
    game_room.open_spaceship_ramp()
    assert sum(s.get("sound_type") == "door_operate" for s in game_room.sound_events) == 1
    assert game_room.map_data["geometry_revision"] == 1
    assert all(s["sound_type"] in AUDIO_CONFIGS for s in game_room.sound_events if s.get("sound_type"))
    assert not any(s.get("kind") in ("door", "alien_attack", "alien_death") for s in game_room.pending_sound_events)


def test_factory_keeps_authoritative_identity_and_simulation_time():
    event = create_audio_event("door_operate", 2, 3, "door", event_id="world:10", sequence=10, timestamp=0, world_id="world")
    assert event["timestamp"] == 0 and event["event_id"] == "world:10" and event["sequence"] == 10
    with pytest.raises(KeyError):
        create_audio_event("alarm", 0, 0, event_id="x", sequence=1, timestamp=0, world_id="world")


@pytest.mark.parametrize("changes,blocks", [({}, 1), ({"active": False}, 0), ({"is_door": True, "state": "open"}, 0), ({"material": "transparent"}, 0)])
def test_explicit_surface_active_door_material_and_origin_hit(changes, blocks):
    clear_occlusion_cache()
    wall = {"id": "not_a_name_guess", "x": 0, "y": -1, "w": 1, "h": 2, **changes}
    assert calculate_occlusion(0, 0, 4, 0, [wall], world_id="w")[2] == blocks


def test_no_door_name_guess_and_exact_geometry_cache_invalidation():
    clear_occlusion_cache()
    wall = {"id": "door_bulkhead", "x": 2, "y": -1, "w": 0.02, "h": 2, "active": True, "state": "open"}
    assert calculate_occlusion(0, 0, 4, 0, [wall], world_id="one")[2] == 1
    assert calculate_occlusion(0, 0, 1.99, 0, [wall], world_id="one")[2] == 0
    assert calculate_occlusion(0, 0, 2.01, 0, [wall], world_id="one")[2] == 1
    wall["x"] = 10
    assert calculate_occlusion(0, 0, 4, 0, [wall], world_id="one")[2] == 0
    wall.update(x=2, active=False)
    assert calculate_occlusion(0, 0, 4, 0, [wall], world_id="one")[2] == 0
    wall["active"] = True
    calculate_occlusion(0, 0, 4, 0, [wall], world_id="two", geometry_revision=1)
    assert any(key[:2] == ("two", 1) for key in _occlusion_cache)
    for index in range(RULES["audio"]["occlusion_cache_limit"] + 10):
        calculate_occlusion(index, 0, 4, 0, [wall], world_id="two")
    assert len(_occlusion_cache) == RULES["audio"]["occlusion_cache_limit"]


def test_actual_retained_spaceship_ramp_is_explicit_and_open_not_occluded(game_room):
    game_room.add_player("a")
    assert game_room.start_mission("a", False)
    ramp = next(w for w in game_room.map_data["walls"] if w["id"] == game_room.map_data["spaceship"]["door_wall_id"])
    assert ramp["is_door"] and ramp["material"] == "door"
    x, y = ramp["x"], ramp["y"] + ramp["h"] / 2
    assert calculate_occlusion(x - 1, y, x + 1, y, [ramp])[2] == 1
    game_room.open_spaceship_ramp()
    assert calculate_occlusion(x - 1, y, x + 1, y, [ramp])[2] == 0


def test_authored_ambience_fixture_is_consumed_without_adding_production_emitters(game_room):
    from backend.app.engine import map_builder
    raw = mutable_copy(CATALOG.definitions)
    raw["segments"]["drop_hangar"]["ambience_emitters"] = [{"id": "vent", "sound_type": "ambient_vent", "x": 3, "y": 4, "active": True}]
    catalog = validate_content(raw)
    with patch.object(map_builder, "SEGMENT_TEMPLATES", catalog.definitions["segments"]):
        built = map_builder.build_mission_map("outpost", 101)
    emitter = built["ambience_emitters"][0]
    start = next(s for s in built["segments"] if s["role"] == "start")
    assert (emitter["x"], emitter["y"]) == (start["x"] + 3, start["y"] + 4)
    arena(game_room)
    emitter.update(x=0, y=0)
    game_room.map_data["ambience_emitters"] = [emitter]
    snapshot = game_room.get_snapshot("a")
    assert snapshot["ambience_emitters"][0]["sound_type"] == "ambient_vent"
    assert snapshot["ambience_emitters"][0]["world_id"] == snapshot["world_id"]
    assert not game_room.pending_sound_events
    emitter["active"] = False
    assert game_room.get_snapshot("a")["ambience_emitters"] == []
    assert all(not s["ambience_emitters"] for s in CATALOG.definitions["segments"].values())
    with pytest.raises(TypeError):
        catalog.definitions["segments"]["drop_hangar"]["ambience_emitters"][0]["active"] = False


@pytest.mark.parametrize("change", ["sound", "loop", "bounds", "duplicate", "strict", "tier", "material"])
def test_audio_content_invalid_refs_bounds_rules_and_types_reject(change):
    raw = mutable_copy(CATALOG.definitions)
    emitter = {"id": "vent", "sound_type": "ambient_vent", "x": 1, "y": 1, "active": True}
    raw["segments"]["drop_hangar"]["ambience_emitters"] = [emitter]
    if change == "sound": emitter["sound_type"] = "invented_alarm"
    if change == "loop": emitter["sound_type"] = "alien_attack"
    if change == "bounds": emitter["x"] = 999
    if change == "duplicate": raw["segments"]["drop_hangar"]["ambience_emitters"].append(dict(emitter))
    if change == "strict": emitter["active"] = "true"
    if change == "tier": raw["rules"]["audio"]["occlusion_tiers"][1][1] = 2
    if change == "material": raw["rules"]["audio"]["material_penalties"].pop("solid")
    with pytest.raises(ValueError): validate_content(raw)


def test_feedback_snapshot_coalescing_keeps_live_ids_without_consuming_hearing(game_room):
    import asyncio
    import json
    from backend.app.main import broadcast_snapshots
    from backend.app.transport import Connection

    arena(game_room)
    game_room._emit_sound(0, 0, 14, "alarm", sound_type="weapon_fire_pistol")
    identity = game_room.sound_events[0]["event_id"]

    async def verify():
        sent = asyncio.Event()
        class Socket:
            def __init__(self): self.messages = []
            async def send_text(self, text):
                self.messages.append(json.loads(text))
                if self.messages[-1]["type"] == "state": sent.set()
            async def close(self): pass
        connection = Connection(Socket(), lambda: None)
        try:
            connection.enqueue({"type": "puzzle_update", "ok": True})
            for _ in range(20): await broadcast_snapshots(game_room, {"a": connection})
            await asyncio.wait_for(sent.wait(), 1)
            assert [m["type"] for m in connection.websocket.messages] == ["puzzle_update", "state"]
            assert connection.websocket.messages[-1]["audio_events"][0]["event_id"] == identity
            assert game_room.pending_sound_events[0]["event_id"] == identity
        finally:
            await connection.close()
    asyncio.run(verify())


def test_solved_bulkhead_and_ramp_emit_once_without_rebalancing_hearing(game_room):
    a, _ = arena(game_room)
    wall = {"id": "random", "x": 3, "y": 0, "w": 1, "h": 1, "active": True, "is_door": True, "material": "door"}
    puzzle = {"id": "pz", "x": 0, "y": 0, "solved": False, "door_id": "random", "label": "Console"}
    game_room.map_data.update(walls=[wall], puzzles=[puzzle])
    game_room._complete_puzzle(puzzle, a["name"])
    game_room._complete_puzzle(puzzle, a["name"])
    assert not wall["active"]
    assert game_room.map_data["geometry_revision"] == 1
    assert len(game_room.sound_events) == 1 and game_room.sound_events[0]["sound_type"] == "door_operate"
    assert not game_room.pending_sound_events


def test_last_round_auto_reload_uses_actual_asset_without_extra_ai_noise(game_room):
    from backend.app.engine.inventory import sync_weapon_state, get_active_weapon_config
    a, _ = arena(game_room)
    a["ammo"] = 1
    sync_weapon_state(a)
    weapon = get_active_weapon_config(a, {})
    game_room.update_input("a", {"firing": True})
    game_room.step(1 / 30)
    reloads = [e for e in game_room.sound_events if e["kind"] == "reload"]
    assert len(reloads) == 1 and reloads[0]["sound_type"] == weapon["reload_sound"]
    assert reloads[0]["source_id"] == "a" and reloads[0]["radius"] == 0
    assert a["reload_timer"] == weapon["reload_time"]
