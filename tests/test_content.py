import copy
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from backend.app.content import CATALOG, CONTENT_PATH, load_catalog, mutable_copy, public_payload, validate_content
from backend.app.config.classes import CHARACTER_CLASSES
from backend.app.config.items import ITEM_TEMPLATES, make_item
from backend.app.engine.inventory import item_effect, get_active_weapon_config


def authored():
    return json.loads(CONTENT_PATH.read_text())


@pytest.mark.parametrize("path,value,error", [
    (("weapons", "breaching_shotgun", "damage"), 0, "weapons.breaching_shotgun.damage"),
    (("weapons", "breaching_shotgun", "range"), float("nan"), "weapons.breaching_shotgun.range"),
    (("weapons", "breaching_shotgun", "fire_rate"), float("inf"), "weapons.breaching_shotgun.fire_rate"),
    (("weapons", "breaching_shotgun", "fire_sound"), "missing", "weapons.breaching_shotgun.fire_sound"),
    (("weapons", "breaching_shotgun", "fire_sound"), "door_operate", "weapons.breaching_shotgun.fire_sound"),
    (("classes", "vanguard", "id"), "other", "classes.vanguard.id"),
    (("classes", "vanguard", "carry_capacity"), True, "classes.vanguard.carry_capacity"),
    (("classes", "vanguard", "loadout_id"), "missing", "classes.vanguard.loadout_id"),
    (("classes", "vanguard", "passive", "id"), "arbitrary_import", "classes.vanguard.passive.id"),
    (("classes", "vanguard", "sprint_speed"), 1.0, "classes.vanguard.sprint_speed"),
    (("items", "medkit", "category"), "script", "items.medkit.category"),
    (("items", "medkit", "effect_id"), "missing", "items.medkit.effect_id"),
    (("items", "medkit", "effect_id"), "shockwave_shield", "items.medkit.effect_id"),
    (("items", "medkit", "weapon_id"), "compact_smg", "items.medkit"),
    (("effects", "nano_field", "behavior"), "import_module", "effects.nano_field"),
    (("effects", "nano_field", "radius"), -1, "effects.nano_field"),
    (("effects", "nano_field", "script"), "eval", "effects.nano_field"),
    (("loadouts", "tech", "primary_weapon"), "medkit", "loadouts.tech.primary_weapon"),
    (("segments", "drop_hangar", "width"), 15.0, "segments.drop_hangar.width"),
    (("segments", "drop_hangar", "interior_walls", 0, "w"), 100.0, "segments.drop_hangar.interior_walls.0"),
    (("segments", "drop_hangar", "puzzle_slots", 0, "x"), 17.0, "segments.drop_hangar.puzzle_slots.0"),
    (("segments", "drop_hangar", "puzzle_slots", 0, "type"), "unknown", "segments.drop_hangar.puzzle_slots.0.type"),
    (("segments", "drop_hangar", "connector_ports", 0, "cell_x"), 1, "segments.drop_hangar.connector_ports.0"),
    (("segments", "security_corridor", "connector_ports", 0, "dir"), "E", "segments.security_corridor.connector_ports.0.dir"),
    (("segments", "drop_hangar", "enemy_spawns", 0, "type"), "missing", "segments.drop_hangar.enemy_spawns.0.type"),
    (("segments", "drop_hangar", "enemy_spawns", 0, "patrol_dx"), 40.0, "segments.drop_hangar.enemy_spawns.0"),
    (("segments", "drop_hangar", "loot_spawns", 0, "type"), "missing", "segments.drop_hangar.loot_spawns.0.type"),
    (("rules", "geometry", "connector_width"), 5.0, "rules.geometry.connector_width"),
    (("missions", "outpost", "prerequisites"), ["missing"], "missions.outpost.prerequisites"),
    (("missions", "outpost", "prerequisites"), ["reactor"], "prerequisite cycle"),
    (("missions", "outpost", "max_segments"), 20, "missions.outpost.max_segments"),
    (("loot_tables", "supply_crates", 0, 0), "missing", "loot_tables.supply_crates.0"),
    (("sounds", "weapon_fire_shotgun", "asset"), "audio/missing.mp3", "sounds.weapon_fire_shotgun.asset: missing asset"),
    (("sounds", "weapon_fire_shotgun", "asset"), "../music.mp3", "sounds.weapon_fire_shotgun.asset: asset must"),
    (("sounds", "weapon_fire_shotgun", "asset"), "/tmp/music.mp3", "sounds.weapon_fire_shotgun.asset: asset must"),
    (("sounds", "weapon_fire_shotgun", "asset"), "https://example.com/music.mp3", "sounds.weapon_fire_shotgun.asset: asset must"),
    (("sounds", "weapon_fire_shotgun", "radius_min"), 40.0, "sounds.weapon_fire_shotgun.radius_max"),
    (("music", "tracks", "explore"), "audio/missing.mp3", "music.tracks.explore: missing asset"),
])
def test_invalid_content_has_useful_paths(path, value, error):
    data = authored()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError) as caught:
        validate_content(data)
    assert error in str(caught.value)


def test_immutable_definitions_and_runtime_independence():
    with pytest.raises(TypeError):
        CATALOG.definitions["classes"]["vanguard"]["carry_capacity"] = 1
    with pytest.raises(TypeError):
        CHARACTER_CLASSES["vanguard"]["weapon"]["damage"] = 1
    with pytest.raises(TypeError):
        ITEM_TEMPLATES["medkit"]["value"]["heal"] = 1
    before = mutable_copy(CATALOG.definitions)
    one, two = make_item("auto_turret"), make_item("auto_turret")
    one["effect"]["range"] = 123
    assert two["effect"]["range"] == 10.5
    view = CATALOG.client_view()
    view["classes"]["vanguard"]["weapon"]["damage"] = 123
    assert mutable_copy(CATALOG.definitions) == before


def test_effective_balance_and_legacy_expanded_values(game_room):
    effects = CATALOG.definitions["effects"]
    assert effects["shockwave_shield"]["radius"] == 4
    assert effects["auto_turret"]["range"] == 10.5
    assert effects["auto_turret"]["damage"] == 9
    assert effects["auto_turret"]["fire_interval"] == 0.28
    assert effects["nano_field"]["radius"] == 3.8
    assert effects["nano_field"]["heal_interval"] == 0.5
    assert effects["nano_field"]["heal"] == 5
    assert effects["nano_field"]["low_health_heal"] == 8
    assert effects["portable_turret"]["duration"] == 10
    assert effects["sonar_beacon"]["duration"] == 4
    before = copy.deepcopy(game_room.level_store.levels)
    level = game_room.level_store.instantiate_map("outpost_101", force_test_mode=False)
    source = before["outpost_101"]["map_template"]
    assert [{k: v for k, v in wall.items() if k not in ("is_door", "material")} for wall in level["walls"]] == source["walls"]
    assert [{k: v for k, v in p.items() if k not in ("definition", "definition_id", "session")} for p in level["puzzles"]] == source["puzzles"]
    assert game_room.level_store.levels == before
    item = make_item("medkit")
    item["value"]["heal"] = 71
    assert item_effect(item)["heal"] == 71
    p = game_room.add_player("p")
    weapon = p["inventory"]["equipped"]["primary_weapon"]
    weapon["weapon"]["damage"] = 37
    assert get_active_weapon_config(p, CHARACTER_CLASSES["vanguard"]["weapon"])["damage"] == 37


def test_public_payload_hides_only_private_power_answers(game_room, game_app):
    player = game_room.add_player("p")
    assert game_room.start_mission("p", False)
    before = copy.deepcopy(game_room.map_data)
    snapshot = game_room.get_snapshot("p")
    for puzzle in snapshot["map"]["puzzles"]:
        if puzzle["type"] == "power_reroute":
            assert "target" not in puzzle["data"]
        else:
            assert puzzle["data"]["target_sequence"]
    for crate in snapshot["map"]["crates"]:
        if crate.get("puzzle", {}).get("type") == "power_reroute":
            assert "target" not in crate["puzzle"]["data"]
    assert game_room.map_data == before
    for puzzle in before["puzzles"]:
        if puzzle["type"] == "power_reroute":
            assert "target" not in public_payload({"puzzle": puzzle})["puzzle"]["data"]
    with TestClient(game_app) as client:
        view = client.get("/api/config").json()
        assert "segments" not in view and "effects" not in view
        assert view["classes"]["vanguard"]["carry_capacity"] == player["inventory"]["max_weight"]


def test_catalog_edits_reach_real_runtime_http_and_js_consumers(tmp_path, game_room):
    data = authored()
    data["weapons"]["breaching_shotgun"]["damage"] = 31
    data["weapons"]["breaching_shotgun"]["mag_size"] = 9
    data["weapons"]["breaching_shotgun"]["fire_sound"] = "weapon_fire_pistol"
    data["weapons"]["breaching_shotgun"]["can_fire_while_running"] = True
    data["classes"]["vanguard"]["carry_capacity"] = 29
    data["effects"]["shockwave_shield"]["radius"] = 6.0
    data["effects"]["tactical_light"]["sight_boost"] = 7.0
    data["sounds"]["weapon_fire_pistol"]["asset"] = "audio/shotgun_fire.mp3"
    data["sounds"]["weapon_fire_pistol"]["base_gain"] = 0.42
    path = tmp_path / "content.json"
    path.write_text(json.dumps(data))
    script = r'''
import asyncio, json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app.engine.game_loop import GameRoom
from backend.app.engine.inventory import SharedStash, is_light_weapon, sight_boost
from backend.app.engine.level_store import LevelStore
from backend.app.config.items import make_item
from backend.app.main import create_app
room = GameRoom(LevelStore(Path(sys.argv[1])), SharedStash(Path(sys.argv[2])))
p = room.add_player("p")
assert p["ammo"] == 9 and p["inventory"]["max_weight"] == 29
assert room.start_mission("p", False)
room.phase = "active"
room.map_data["walls"] = []
room.map_data["enemies"] = [{"id":"e","type":"stalker","x":p["x"]+5,"y":p["y"],"hp":45,"radius":0.7,"state":"patrol"}]
assert room.trigger_skill("p")
assert room.map_data["enemies"][0]["hp"] == 30
room.map_data["enemies"] = []
room.update_input("p", {"firing": True})
room.step(1/30)
assert room.projectiles and room.projectiles[0]["damage"] == 31
audio = next(s for s in room.sound_events if s.get("sound_type"))
assert audio["sound_type"] == "weapon_fire_pistol" and audio["base_gain"] == 0.42
assert is_light_weapon(p)
p["inventory"]["storage"].append(make_item("tactical_light"))
assert sight_boost(p) == 7
async def idle(*args):
    await asyncio.Event().wait()
with TestClient(create_app(room, simulation=idle)) as client:
    view = client.get("/api/config").json()
    assert view["classes"]["vanguard"]["weapon"]["damage"] == 31
    assert view["classes"]["vanguard"]["skill"]["radius"] == 6
    assert view["classes"]["vanguard"]["carry_capacity"] == 29
    assert view["sounds"]["weapon_fire_pistol"]["asset"] == "audio/shotgun_fire.mp3"
    with client.websocket_connect("/ws") as ws:
        welcome = ws.receive_json()
        assert welcome["catalog"] == {k:v for k,v in view.items() if k not in ("levels", "development_tools")}
print(json.dumps({"catalog": view, "player": p}))
'''
    env = {**os.environ, "SWARM_CONTENT_PATH": str(path)}
    result = subprocess.run([sys.executable, "-c", script, str(game_room.level_store.data_file), str(game_room.shared_stash.path)], env=env, capture_output=True, text=True, check=True)
    js = r'''
import { canFireWhileRunning, playerSightRange, audioAssets } from './frontend/src/js/content.js';
import assert from 'node:assert/strict';
let raw = ''; for await (const chunk of process.stdin) raw += chunk;
const { catalog, player } = JSON.parse(raw);
assert.equal(catalog.classes.vanguard.carry_capacity, player.inventory.max_weight);
assert.equal(catalog.classes.vanguard.weapon.damage, 31);
assert.equal(catalog.classes.vanguard.skill.radius, 6);
assert.equal(canFireWhileRunning(player, catalog.items), true);
assert.equal(playerSightRange(player, catalog.classes, catalog.items), 19);
assert.equal(audioAssets(catalog).weapon_fire_pistol, 'audio/shotgun_fire.mp3');
'''
    subprocess.run(["node", "--input-type=module", "-e", js], input=result.stdout, capture_output=True, text=True, check=True)
    assert load_catalog(path).classes["vanguard"]["weapon"]["damage"] == 31


def test_cli_validates_requested_catalog_and_fails_without_save_writes(tmp_path):
    path = tmp_path / "content.json"
    path.write_text(json.dumps(authored()))
    command = [sys.executable, "-m", "backend.app.content", str(path)]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0 and CATALOG.definitions["content_version"] in result.stdout
    data = authored()
    data["weapons"]["breaching_shotgun"]["fire_sound"] = "missing"
    path.write_text(json.dumps(data))
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 1 and "weapons.breaching_shotgun.fire_sound" in result.stderr
    env = {**os.environ, "SWARM_CONTENT_PATH": str(path), "SWARM_SAVE_DIR": str(tmp_path / "saves")}
    result = subprocess.run([sys.executable, "-c", "import backend.app.main"], env=env, text=True, capture_output=True)
    assert result.returncode != 0 and "weapons.breaching_shotgun.fire_sound" in result.stderr
    assert set(tmp_path.iterdir()) == {path}


def test_duplicate_keys_and_symlink_asset_escape_rejected(tmp_path):
    path = tmp_path / "content.json"
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="duplicate JSON key 'schema_version'"):
        load_catalog(path)
    from backend.app.content import ASSET_ROOT
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    (asset_root / "audio").symlink_to(ASSET_ROOT / "audio", target_is_directory=True)
    with pytest.raises(ValueError, match="sounds.weapon_fire_shotgun.asset"):
        validate_content(authored(), asset_root=asset_root)


@pytest.mark.parametrize("class_id", ["tech", "medic"])
def test_edited_deployable_tuning_controls_actual_projectiles_and_healing(game_room, monkeypatch, class_id):
    data = authored()
    if class_id == "tech":
        data["effects"]["auto_turret"]["damage"] = 23
        data["effects"]["auto_turret"]["range"] = 12.0
        data["effects"]["auto_turret"]["fire_interval"] = 0.4
    else:
        data["effects"]["nano_field"]["radius"] = 6.0
        data["effects"]["nano_field"]["heal"] = 11
    edited = validate_content(data)
    monkeypatch.setattr("backend.app.engine.game_loop.CHARACTER_CLASSES", edited.classes)
    p = game_room.add_player("p")
    assert game_room.select_class("p", class_id)
    assert game_room.start_mission("p", False)
    game_room.phase = "active"
    game_room.map_data["walls"] = []
    enemies = game_room.map_data["enemies"]
    enemy = copy.deepcopy(enemies[0])
    enemy.update(x=p["x"] + 11.5, y=p["y"])
    game_room.map_data["enemies"] = [enemy] if class_id == "tech" else []
    p["hp"] = p["max_hp"] - 20
    assert game_room.trigger_skill("p")
    dep = game_room.deployables[0]
    if class_id == "medic":
        p["x"] += 5.5
    game_room.step(1/30)
    if class_id == "tech":
        assert dep["range"] == 12 and dep["fire_timer"] == 0.4
        assert game_room.projectiles[0]["damage"] == 23
        assert edited.client_view()["classes"]["tech"]["skill"]["damage"] == 23
    else:
        assert dep["radius"] == 6
        assert p["hp"] == p["max_hp"] - 9
        assert edited.client_view()["items"]["nano_field"]["effect"]["radius"] == 6


def test_every_weapon_sound_emission_is_registered_without_name_guessing(game_room):
    from backend.app.engine.audio_events import AUDIO_CONFIGS
    from backend.app.engine.inventory import sync_weapon_state
    p = game_room.add_player("p")
    assert game_room.start_mission("p", False)
    game_room.phase = "active"
    game_room.map_data["walls"] = []
    game_room.map_data["enemies"] = []
    for tid, template in ITEM_TEMPLATES.items():
        if "weapon" not in template:
            continue
        item = make_item(tid)
        item["name"] = "No weapon-type hint"
        p["inventory"]["active_weapon_slot"] = "primary_weapon"
        p["inventory"]["equipped"]["primary_weapon"] = item
        sync_weapon_state(p)
        p["ammo"] -= 1
        assert game_room.trigger_reload("p")
        assert game_room.sound_events[-1]["sound_type"] == template["weapon"]["reload_sound"]
        p["reload_timer"] = 0
        p["fire_cooldown"] = 0
        game_room.update_input("p", {"firing": True})
        game_room.step(1/30)
        assert any(s.get("sound_type") == template["weapon"]["fire_sound"] for s in game_room.sound_events)
        assert all(s["sound_type"] in AUDIO_CONFIGS for s in game_room.sound_events if "sound_type" in s)
    with pytest.raises(KeyError):
        game_room._emit_sound(p["x"], p["y"], 1, sound_type="unknown_handler")
