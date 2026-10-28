import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.commands import parse_command
from backend.app.content import CATALOG, mutable_copy, validate_content
from backend.app.engine.game_loop import GameRoom
from backend.app.engine.inventory import inventory_items
from backend.app.engine.persistence import SaveError
from backend.app.engine.worlds import instantiate_world
from backend.app.main import create_app, dispatch_command


@pytest.fixture
def hub(game_room):
    game_room.hub_world = "flagship_stub"
    game_room.reset_to_lobby()
    game_room.add_player("leader")
    game_room.add_player("peer")
    return game_room


def service(room, kind):
    target = next(t for t in room.map_data["interactions"] if t["type"] == kind)
    room.players["leader"].update(x=target["x"], y=target["y"] + 1)
    return target["id"]


def depart(room):
    target = service(room, "mission_selection")
    assert room.prepare_mission("leader", target)
    room.toggle_ready("leader", True)
    room.toggle_ready("peer", True)
    assert room.start_mission("leader", target)
    room.map_data.update(enemies=[], puzzles=[], objective_definitions=[], objectives=[], walls=[],
                         props=[], crates=[], loot=[], extraction_zone={"x": 10, "y": 10, "w": 4, "h": 4, "active": True})
    room.phase = "active"
    for p in room.players.values():
        p.update(x=4, y=4)


def totals(room):
    counts = dict(room.shared_stash.stacks)
    for record in room.shared_stash.holdings.values():
        tid = record["template_id"]
        counts[tid] = counts.get(tid, 0) + 1
    return counts


def test_default_lobby_remains_worldless(game_room):
    game_room.initialize()
    assert game_room.map_data is None
    assert game_room.get_snapshot()["world_kind"] is None
    assert game_room.hub_world is None


def test_explicit_production_flag_initializes_authored_world(game_room):
    async def idle(*args):
        await asyncio.Event().wait()
    configured = GameRoom(game_room.level_store, game_room.shared_stash, hub_world="flagship_stub")
    with TestClient(create_app(configured, simulation=idle)) as client:
        with client.websocket_connect("/ws") as socket:
            welcome = socket.receive_json()
            assert welcome["state"]["phase"] == "lobby"
            assert welcome["state"]["world_kind"] == "hub"
            assert welcome["state"]["map"]["world_name"] == "Flagship Hub (Placeholder)"
        assert client.post("/__test__/fixture").status_code == 404
    with pytest.raises(ValueError, match="Unknown hub"):
        GameRoom(hub_world="missing")


def test_world_identity_not_kind_or_phase(hub):
    before = hub.map_data["world_id"]
    snap = hub.get_snapshot()
    assert snap["phase"] == "lobby" and snap["map"]
    assert "spaceship" not in snap["map"] and "extraction_zone" not in snap["map"]
    assert "mission_id" not in snap["map"]
    assert snap["world_capabilities"]["beneficial_use"] == "disabled"
    assert snap["players"][0]["capabilities"]["can_move"]
    hub.phase = "active"
    hub.step(10)
    assert hub.phase == "active" and hub.mission_time == 0
    assert hub.map_data["world_id"] == before
    hub.reset_to_lobby()
    assert hub.map_data["world_id"] != before


def test_shared_movement_collision_prop_and_input_timeout(hub):
    p = hub.players["leader"]
    p.update(x=7, y=8)
    assert hub.update_input("leader", {"dx": 0, "dy": -1, "firing": False, "running": False})
    for _ in range(10):
        hub.update_input("leader", {"dx": 0, "dy": -1})
        hub.step(.1)
    assert p["y"] >= 7.35 + p["radius"] - .001
    p.update(x=1, y=3)
    hub.update_input("leader", {"dx": -1, "dy": 0})
    for _ in range(12):
        hub.step(.1)
    assert p["x"] >= .3 + p["radius"] - .001
    assert p["input"]["dx"] == 0 and p["vx"] == 0
    assert hub.sound_events and all(s["world_id"] == hub.map_data["world_id"] for s in hub.sound_events)


@pytest.mark.parametrize("action", ["fire", "reload", "skill", "heal", "ammo", "turret", "sonar", "damage"])
def test_hub_commands_cannot_mutate_combat_or_consume_items(hub, action):
    p = hub.players["leader"]
    target = service(hub, "stash")
    templates = {"heal": "medkit", "ammo": "ammo_pack", "turret": "portable_turret", "sonar": "sonar_beacon"}
    if action in templates:
        tid = templates[action]
        hub.shared_stash.add_template(tid)
        assert hub.stash_to_player("leader", tid, target_id=target)["ok"]
        item = next(it for it in inventory_items(p) if not it.get("starter") and it["template_id"] == tid)
    before = copy.deepcopy(p)
    ownership = totals(hub)
    if action == "fire":
        assert not hub.update_input("leader", {"dx": 1, "firing": True})
    elif action == "reload":
        assert not hub.trigger_reload("leader")
    elif action == "skill":
        assert not hub.trigger_skill("leader")
    elif action == "damage":
        assert not hub.damage(p, 1000)
    else:
        assert not hub.handle_inventory_action("leader", {"action": "use_item", "item_id": item["id"]})["ok"]
    assert p == before and totals(hub) == ownership
    assert not hub.projectiles and not hub.deployables


def test_hub_ticks_disable_hostile_ai_damage_cooldowns_and_failure(hub):
    depart(hub)
    # Use an actual mission enemy, rather than an incomplete mock actor.
    enemy = copy.deepcopy(hub.level_store.instantiate_map("outpost_101")["enemies"][0])
    hub.reset_to_lobby()
    p = hub.players["leader"]
    enemy.update(x=p["x"], y=p["y"], state="engaged", attack_cooldown=1)
    hub.map_data["enemies"].append(enemy)
    p.update(fire_cooldown=1, skill_cooldown=2, skill_timer=3, reload_timer=4)
    before = copy.deepcopy(enemy)
    hp = p["hp"]
    hub.step(.25)
    assert enemy == before and p["hp"] == hp
    assert (p["fire_cooldown"], p["skill_cooldown"], p["skill_timer"], p["reload_timer"]) == (1, 2, 3, 4)
    for player in hub.players.values():
        player.update(hp=0, downed=True)
    hub.step(100)
    assert hub.phase == "lobby" and hub.mission_time == 0
    assert not hub.map_data["crates"] and not hub.map_data.get("objectives")


def test_console_target_reach_los_and_leader_policy(hub):
    target = service(hub, "stash")
    assert hub.handle_interact("leader", target)["service"]["type"] == "stash"
    assert hub.handle_interact("leader")["target_id"] == target
    before = totals(hub)
    assert not hub.stash_to_player("leader", "medkit")["ok"]
    hub.players["leader"].update(x=18, y=14)
    assert not hub.stash_to_player("leader", "medkit", target_id=target)["ok"]
    target = service(hub, "mission_selection")
    assert not hub.prepare_mission("peer", target)
    assert not hub.start_mission("leader", target)
    hub.map_data["walls"].append({"id": "blocking", "x": 10, "y": 8, "w": 3, "h": .1, "blocks_sight": True})
    assert not hub.prepare_mission("leader", target)
    assert totals(hub) == before


def test_shared_preparation_and_explicit_departure(hub):
    for record in hub.level_store.levels.values():
        record.update(completed=False, tested=False)
    target = service(hub, "mission_selection")
    assert not hub.select_mission("leader", "reactor_303", True, target)
    assert hub.select_mission("leader", "outpost_101", True, target)
    assert hub.prepare_mission("leader", target)
    assert hub.toggle_ready("leader", True)
    assert hub.toggle_ready("peer", True)
    world = hub.map_data["world_id"]
    hub.step(100)
    assert hub.phase == "prep" and hub.map_data["world_id"] == world
    assert hub.start_mission("leader", target)
    assert hub.phase == "dropship" and hub.map_data["world_kind"] == "mission"
    assert hub.world_capabilities["combat"] and hub.world_capabilities["beneficial_use"] == "normal"
    assert hub.map_data["world_id"] != world


@pytest.mark.parametrize("outcome", ["victory", "defeat", "abort"])
def test_depart_return_conserves_claims_and_restarts_presentation(hub, outcome):
    target = service(hub, "stash")
    hub.shared_stash.add_template("rail_carbine")
    assert hub.stash_to_player("leader", "rail_carbine", target_id=target)["ok"]
    item = next(it for it in inventory_items(hub.players["leader"]) if not it.get("starter"))
    baseline = totals(hub)
    hub_world = hub.map_data["world_id"]
    depart(hub)
    mission_world = hub.map_data["world_id"]
    hub._emit_sound(4, 4, 10, sound_type="weapon_fire_shotgun")
    if outcome == "victory":
        for p in hub.players.values():
            p.update(x=11, y=11)
        hub.step(.1)
        assert hub.phase == "victory"
    elif outcome == "defeat":
        for p in hub.players.values():
            hub.damage(p, p["max_hp"] + 1)
        hub.step(.1)
        assert hub.phase == "defeat"
        assert item["id"] in hub.shared_stash.holdings
    hub.reset_to_lobby()
    assert totals(hub) == baseline
    assert hub.map_data["world_id"] not in (hub_world, mission_world)
    assert not hub.sound_events and not hub.pending_sound_events and not hub.recent_sounds
    assert not hub.projectiles and not hub.deployables and not hub.interaction_requests
    assert hub.get_snapshot()["music"]["state"] == "lobby"
    for p in hub.players.values():
        assert not p["ready"] and not p["downed"] and p["hp"] == p["max_hp"]
        assert p["input"]["dx"] == p["input"]["dy"] == 0 and not p["input"]["firing"]
    hub.reset_to_lobby()
    assert totals(hub) == baseline


def test_depart_settles_dropped_hub_claim_once(hub):
    target = service(hub, "stash")
    hub.shared_stash.add_template("medkit")
    assert hub.stash_to_player("leader", "medkit", target_id=target)["ok"]
    item = next(it for it in inventory_items(hub.players["leader"]) if not it.get("starter"))
    count = totals(hub)
    assert hub.handle_inventory_action("leader", {"action": "drop_item", "item_id": item["id"]})["ok"]
    depart(hub)
    assert totals(hub) == count and item["id"] not in hub.shared_stash.holdings


def test_failed_return_and_disconnect_retry_preserve_ownership(hub, monkeypatch):
    target = service(hub, "stash")
    hub.shared_stash.add_template("medkit")
    assert hub.stash_to_player("leader", "medkit", target_id=target)["ok"]
    baseline = totals(hub)
    save = hub.shared_stash.commit
    def fail(*args):
        raise SaveError("injected")
    monkeypatch.setattr(hub.shared_stash, "commit", fail)
    hub.remove_player("leader")
    assert "leader" in hub.pending_disconnects and totals(hub) == baseline
    monkeypatch.setattr(hub.shared_stash, "commit", save)
    hub.step(.1)
    assert "leader" not in hub.players and hub.leader_id == "peer" and totals(hub) == baseline
    hub.add_player("new-session")
    assert totals(hub) == baseline and hub.players["new-session"]["x"] in (8, 9, 10, 12)


@pytest.mark.parametrize("field,value", [("kind", "social"), ("rules_id", "missing"), ("width", float("nan")), ("account", True)])
def test_strict_world_content_rejects_bad_fields(field, value):
    content = mutable_copy(CATALOG.definitions)
    content["worlds"]["flagship_stub"][field] = value
    with pytest.raises(ValueError):
        validate_content(content)


def test_data_only_authored_world_variation_and_rules_selected_not_kind(hub):
    content = mutable_copy(CATALOG.definitions)
    content["worlds"]["variant"] = copy.deepcopy(content["worlds"]["flagship_stub"])
    content["worlds"]["variant"]["props"].append({"id": "extra", "archetype": "chair", "x": 14, "y": 9})
    edited = validate_content(content)
    assert len(instantiate_world("variant", edited)["props"]) == 5
    hub.map_data["world_kind"] = "mission"
    hub.phase = "active"
    assert not hub.trigger_skill("leader") and not hub.trigger_reload("leader")
    hub.step(.1)
    assert hub.mission_time == 0


def test_failed_world_return_keeps_old_world_and_exact_claims(hub, monkeypatch):
    target = service(hub, "stash")
    hub.shared_stash.add_template("medkit")
    assert hub.stash_to_player("leader", "medkit", target_id=target)["ok"]
    depart(hub)
    for p in hub.players.values():
        hub.damage(p, 1000)
    hub.step(.1)
    previous = hub.map_data
    baseline = totals(hub)
    original = hub.shared_stash.commit
    def fail(*args):
        raise SaveError("return failure")
    monkeypatch.setattr(hub.shared_stash, "commit", fail)
    with pytest.raises(SaveError):
        hub.reset_to_lobby()
    assert hub.map_data is previous and hub.phase == "defeat" and totals(hub) == baseline
    monkeypatch.setattr(hub.shared_stash, "commit", original)
    hub.reset_to_lobby()
    assert hub.map_data is not previous and totals(hub) == baseline


def test_mission_latejoin_disconnect_and_return_refund_once(hub):
    target = service(hub, "stash")
    hub.shared_stash.add_template("rail_carbine")
    assert hub.stash_to_player("leader", "rail_carbine", target_id=target)["ok"]
    baseline = totals(hub)
    depart(hub)
    hub.add_player("late")
    assert hub.players["late"]["x"] in {x for x, _ in hub.map_data["player_spawn_points"]}
    hub.remove_player("leader")
    assert hub.leader_id == "peer" and hub.map_data["crates"] and totals(hub) == baseline
    hub.add_player("reconnected")
    assert totals(hub) == baseline
    hub.reset_to_lobby()
    assert totals(hub) == baseline and not hub.map_data["crates"]
    for identity in list(hub.players):
        hub.remove_player(identity)
    assert totals(hub) == baseline and hub.leader_id is None


def test_authored_spawn_and_bounds_rejected_without_save_rewrites(hub):
    content = mutable_copy(CATALOG.definitions)
    content["worlds"]["flagship_stub"]["spawns"][0] = {"x": 7, "y": 7}
    catalog = validate_content(content)
    with pytest.raises(ValueError, match="blocked player spawn"):
        instantiate_world("flagship_stub", catalog)
    content["worlds"]["flagship_stub"]["tiles"][0]["w"] = 21
    with pytest.raises(ValueError, match="outside world"):
        validate_content(content)


def test_mission_rules_individually_select_movement_failure_objectives_and_clock(hub):
    content = mutable_copy(CATALOG.definitions)
    content["world_rules"]["mission"].update(combat=False, ai=False, objectives=False, extraction=False, failure=False, mission_clock=False, cooldowns=False)
    hub.catalog = validate_content(content)
    depart(hub)
    hub.map_data.pop("spaceship", None)
    hub.map_data.pop("extraction_zone", None)
    assert hub.update_input("leader", {"dx": 1, "dy": 0, "firing": False})
    before = hub.players["leader"]["x"]
    hub.step(.1)
    assert hub.players["leader"]["x"] > before and hub.phase == "active" and hub.mission_time == 0
    assert not hub.trigger_reload("leader") and not hub.trigger_skill("leader")
    hub.map_data["capabilities"]["movement"] = False
    before = hub.players["leader"]["x"]
    hub.step(.1)
    assert hub.players["leader"]["x"] == before


def test_service_correlation_stale_world_and_self_owned_readiness(hub):
    target = service(hub, "stash")
    command = {"type": "stash_to_player", "template_id": "medkit", "target_id": target,
               "request_id": "request", "session_id": "leader", "world_id": hub.map_data["world_id"]}
    command = parse_command(json.dumps(command))
    reply = dispatch_command(hub, "leader", command)
    assert reply["ok"] and reply["world_id"] == command["world_id"]
    with pytest.raises(ValueError, match="Duplicate"):
        dispatch_command(hub, "leader", command)
    hub.reset_to_lobby()
    with pytest.raises(ValueError, match="Stale"):
        dispatch_command(hub, "leader", {**command, "request_id": "later"})
    with pytest.raises(ValueError, match="self-owned"):
        dispatch_command(hub, "peer", {"type": "toggle_ready", "target_player_id": "leader"})
