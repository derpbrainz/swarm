"""
Unit and integration tests for the Co-op Isometric Extraction Game.
"""

try:
    from fastapi.testclient import TestClient
    from backend.app.main import app, room
except ModuleNotFoundError:
    TestClient = None
    from backend.app.engine.game_loop import GameRoom
    app = None
    room = GameRoom()
from backend.app.engine.map_builder import build_mission_map, verify_level_solvability
from backend.app.engine.level_store import level_store
from backend.app.engine.physics import (
    circle_vs_aabb,
    has_line_of_sight,
    move_with_collisions,
)


def _reset_room():
    room.players.clear()
    room.join_order.clear()
    room.leader_id = None
    room.selected_mission_id = "outpost"
    room.selected_level_id = "outpost_101"
    room.reset_to_lobby()


def _select_level(player_id, level_id):
    if hasattr(room, "select_level"):
        if room.select_level(player_id, level_id):
            return True
    return room.select_mission(player_id, level_id)


def test_wfc_multi_size_segments_and_connectors():
    m = build_mission_map("outpost")

    assert len(m["segments"]) >= 4
    assert m["segments"][0]["id"] == "drop_hangar"
    assert m["segments"][-1]["id"] == "extraction_pad"

    grid_sizes = {(s["grid_w"], s["grid_h"]) for s in m["segments"]}
    assert len(grid_sizes) >= 2

    connectors = m.get("connectors", [])
    assert connectors

    for c in connectors:
        assert c["dir"] in ("N", "S", "E", "W")
        assert c.get("flat", True)
        assert c.get("grid_aligned", True)

    upstream_segments = {c["from_segment"] for c in connectors}
    for pz in m.get("puzzles", []):
        assert pz["segment_index"] in upstream_segments

    assert verify_level_solvability(m) is True


def test_level_persistence_and_testing_phase():
    _reset_room()

    rec = level_store.generate_level("outpost", seed=777, tested=False)
    assert rec["tested"] is False
    assert rec["level_id"] in level_store.levels

    p1 = room.add_player("p1", "Alice")
    assert _select_level("p1", rec["level_id"])
    assert room.start_mission("p1")

    room.step(3.0)
    assert room.map_data["test_mode"] is True
    assert len(room.map_data.get("enemies", [])) == 0
    assert len(room.map_data.get("loot", [])) > 0

    puzzles = room.map_data.get("puzzles", [])
    assert len(puzzles) > 0

    for pz in puzzles:
        room._complete_puzzle(pz, p1["name"])

    ext = room.map_data["extraction_zone"]
    p1["x"] = ext["x"] + 2.0
    p1["y"] = ext["y"] + 2.0
    room.step(0.1)

    assert room.phase == "victory"
    assert level_store.levels[rec["level_id"]]["tested"] is True

    _reset_room()
    p1 = room.add_player("p1", "Alice")
    assert _select_level("p1", rec["level_id"])
    assert room.start_mission("p1")

    room.step(3.0)
    assert room.map_data["test_mode"] is False
    assert len(room.map_data.get("enemies", [])) > 0
    assert len(room.map_data.get("loot", [])) > 0

    level_store.levels.pop(rec["level_id"], None)
    level_store.save()
    _reset_room()


def test_class_selection_and_level_progression_unlocks():
    _reset_room()
    for r in level_store.levels.values():
        r["completed"] = False
    level_store.save()

    p1 = room.add_player("p1", "Leader")
    # Every character class can be selected freely
    for cls_id in ("vanguard", "tech", "medic", "recon"):
        assert room.select_class("p1", cls_id) is True
        assert room.players["p1"]["class_id"] == cls_id

    # Outpost #1, Outpost #2, and Biolab #1 are unlocked initially
    assert room.select_mission("p1", "outpost_154", enforce_unlock=True) is True
    assert room.selected_level_id == "outpost_154"
    assert room.select_mission("p1", "biolab_202", enforce_unlock=True) is True
    assert room.selected_level_id == "biolab_202"

    # Harder levels (Biolab #2 and Reactor #1) require completing an easier level first
    unlocked_b2, reason_b2 = level_store.is_level_unlocked("biolab_255")
    assert unlocked_b2 is False and "Bio-Lab Containment #1" in (reason_b2 or "")
    assert room.select_mission("p1", "biolab_255", enforce_unlock=True) is False

    unlocked_r1, reason_r1 = level_store.is_level_unlocked("reactor_303")
    assert unlocked_r1 is False and "first to unlock" in (reason_r1 or "")
    assert room.select_mission("p1", "reactor_303", enforce_unlock=True) is False

    # Completing Biolab #1 unlocks both Biolab #2 and Reactor #1!
    level_store.mark_completed("biolab_202")
    assert level_store.is_level_unlocked("biolab_255")[0] is True
    assert level_store.is_level_unlocked("reactor_303")[0] is True
    assert room.select_mission("p1", "reactor_303", enforce_unlock=True) is True
    assert room.selected_level_id == "reactor_303"

    for r in level_store.levels.values():
        r["completed"] = False
    level_store.save()
    _reset_room()


def test_map_segment_stitching():
    m = build_mission_map("outpost")
    assert len(m["segments"]) >= 4
    assert m["segments"][0]["id"] == "drop_hangar"
    assert m["segments"][-1]["id"] == "extraction_pad"
    assert m["spaceship"]["ramp_open"] is False
    assert m["extraction_zone"]["active"] is False
    assert len(m["puzzles"]) >= 2
    assert len(m["enemies"]) >= 5


def test_physics_and_line_of_sight():
    walls = [{"x": 5.0, "y": 0.0, "w": 1.0, "h": 10.0, "active": True, "blocks_sight": True}]
    assert has_line_of_sight(2.0, 5.0, 8.0, 5.0, walls) is False
    assert has_line_of_sight(2.0, 12.0, 8.0, 12.0, walls) is True

    nx, ny = move_with_collisions(4.6, 5.0, 2.0, 0.0, 0.5, 0.5, walls)
    assert nx <= 4.5 + 1e-5


def test_lobby_leader_and_gameplay_flow():
    room.players.clear()
    room.join_order.clear()
    room.leader_id = None
    room.reset_to_lobby()

    p1 = room.add_player("p1", "Alice")
    p2 = room.add_player("p2", "Bob")
    assert room.leader_id == "p1"

    # Non-leader cannot select mission or launch
    assert room.select_mission("p2", "reactor") is False
    assert room.select_mission("p1", "reactor") is True
    assert room.selected_mission_id == "reactor"

    # Character selection
    assert room.select_class("p1", "recon") is True
    assert room.select_class("p2", "tech") is True

    # Start mission
    assert room.start_mission("p1") is True
    assert room.phase == "dropship"

    # Step past dropship countdown -> ramp opens and phase becomes active
    room.step(3.0)
    assert room.phase == "active"
    assert room.map_data["spaceship"]["ramp_open"] is True

    # Solve all puzzles -> unlocks bulkhead doors and activates extraction zone
    for pz in room.map_data["puzzles"]:
        room._complete_puzzle(pz, "Bob")
    assert room.map_data["extraction_zone"]["active"] is True

    # Move both players into extraction zone -> victory!
    ext = room.map_data["extraction_zone"]
    for p in room.players.values():
        p["x"] = ext["x"] + 2.0
        p["y"] = ext["y"] + 2.0
    room.step(0.1)
    assert room.phase == "victory"


def test_websocket_and_http_endpoints():
    if TestClient is None or app is None:
        return
    room.players.clear()
    room.join_order.clear()
    room.leader_id = None
    room.reset_to_lobby()

    client = TestClient(app)
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert "vanguard" in data["classes"]
    assert "outpost" in data["missions"]

    index_res = client.get("/")
    assert index_res.status_code == 200
    assert "VALKYRIE PROTOCOL" in index_res.text

    with client.websocket_connect("/ws") as ws:
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome"
        pid = welcome["player_id"]
        # Drain initial broadcast
        _ = ws.receive_json()

        # Start mission as leader and move player to first puzzle terminal
        room.start_mission(pid)
        room.step(3.0)
        pz = room.map_data["puzzles"][0]
        room.players[pid]["x"] = pz["x"]
        room.players[pid]["y"] = pz["y"]

        ws.send_json({"type": "interact"})
        msg = ws.receive_json()
        assert msg["type"] == "interact_result"
        assert msg["interact_type"] == "open_puzzle"
        assert msg["puzzle"]["id"] == pz["id"]


def test_enemy_hearing_and_sight_ai():
    _reset_room()

    p1 = room.add_player("p1", "Scout")
    room.select_class("p1", "vanguard")
    room.select_mission("p1", "outpost_101")
    room.start_mission("p1", force_test_mode=False)
    room.step(3.0)  # open ramp

    enemy = room.map_data["enemies"][0]
    assert enemy["state"] == "patrol"

    # Place player within gunfire hearing range of enemy, behind a wall, and fire
    p1["x"] = enemy["x"] - 4.0
    p1["y"] = enemy["y"]
    p1["input"]["firing"] = True
    room.step(0.1)
    assert enemy["state"] in ("alerted", "engaged")

    # Test skills and puzzles
    assert room.trigger_skill("p1") is True
    pz = room.map_data["puzzles"][0]
    p1["x"], p1["y"] = pz["x"], pz["y"]
    res = room.handle_interact("p1")
    assert res is not None and res["type"] == "open_puzzle"


def test_inventory_crates_body_chest_shared_stash_and_ready():
    from backend.app.config.items import make_item
    from backend.app.engine.inventory import shared_stash

    _reset_room()

    p1 = room.add_player("p1", "VanguardOp")
    p2 = room.add_player("p2", "ReconOp")
    room.select_class("p1", "vanguard")
    room.select_class("p2", "recon")

    # Stronger classes have greater storage capacity
    assert p1["inventory"]["max_weight"] > p2["inventory"]["max_weight"]
    assert p1["inventory"]["max_weight"] == 24
    assert p2["inventory"]["max_weight"] == 12

    # Dedicated slots exist on every class
    for slot in ("primary_weapon", "special_ability", "secondary_weapon", "quick_use"):
        assert p1["inventory"]["equipped"].get(slot) is not None
        assert p2["inventory"]["equipped"].get(slot) is not None

    # Lobby transfer between shared stash and player & minimum loadout
    initial_medkits = shared_stash.stacks.get("medkit", 0)
    shared_stash.add_template("medkit", 2)
    res_stash = room.stash_to_player("p2", "medkit")
    assert res_stash["ok"] is True
    assert any(it["template_id"] == "medkit" for it in p2["inventory"]["storage"])

    # Wipe p2's items and test minimum loadout restoration
    p2["inventory"]["equipped"]["primary_weapon"] = None
    p2["inventory"]["equipped"]["secondary_weapon"] = None
    p2["inventory"]["equipped"]["quick_use"] = None
    p2["inventory"]["storage"] = []
    assert room.restore_min_loadout("p2") is True
    assert p2["inventory"]["equipped"]["primary_weapon"] is not None
    assert p2["inventory"]["equipped"]["secondary_weapon"] is not None
    assert p2["inventory"]["equipped"]["quick_use"] is not None

    # Preparation & Ready system: leader prepares mission, all players ready up -> 5s countdown -> launch
    room.select_mission("p1", "outpost_101")
    assert room.prepare_mission("p1") is True
    assert room.phase == "prep"
    assert room.toggle_ready("p1") is True   # p1 is now ready, p2 not ready yet
    assert room.phase == "prep"
    assert room.toggle_ready("p2") is True   # all ready -> 5s prep countdown started!
    assert room.phase == "prep"
    assert room.prep_timer == 5.0
    room.step(5.0)                           # countdown completes -> auto-launches into dropship
    assert room.phase == "dropship"

    room.step(3.0)
    assert room.phase == "active"

    # Ground item instanced pickup via button press (interact)
    ground_loot = room.map_data.get("loot", [])
    assert len(ground_loot) > 0
    target_item = ground_loot[0]
    p1["x"], p1["y"] = target_item["x"], target_item["y"]
    pickup_res = room.handle_interact("p1")
    assert pickup_res is not None and pickup_res["type"] == "picked_up"
    assert target_item["collected"] is True
    # Instanced: second player cannot pick up the same item
    p2["x"], p2["y"] = target_item["x"], target_item["y"]
    assert room.pickup_ground_item("p2", target_item["id"])["ok"] is False

    # Supply crates with skill-check puzzle & chest item transfer
    crates = room.map_data.get("crates", [])
    assert len(crates) > 0
    locked_crates = [c for c in crates if c.get("locked")]
    if locked_crates:
        lc = locked_crates[0]
        p1["x"], p1["y"] = lc["x"], lc["y"]
        res_lock = room.handle_interact("p1")
        assert res_lock is not None and res_lock["type"] == "open_puzzle"
        pz = lc["puzzle"]
        if pz["type"] == "power_reroute":
            for i in range(len(pz["data"]["nodes"])):
                while pz["data"]["nodes"][i] != 0:
                    room.handle_puzzle_action("p1", pz["id"], {"action": "rotate", "node_index": i})
        else:
            for sym in pz["data"]["target_sequence"]:
                room.handle_puzzle_action("p1", pz["id"], {"action": "input_symbol", "symbol": sym})
        assert lc["locked"] is False

    open_crate = crates[0]
    open_crate["locked"] = False
    assert len(open_crate["items"]) > 0
    crate_item_id = open_crate["items"][0]["id"]
    p1["x"], p1["y"] = open_crate["x"], open_crate["y"]
    took = room.take_crate_item("p1", open_crate["id"], crate_item_id)
    assert took["ok"] is True
    assert any(it["id"] == crate_item_id for it in p1["inventory"]["storage"])

    # If a player dies (downed), their body turns into an item chest containing their inventory
    p2["hp"] = 1
    e = room.map_data["enemies"][0]
    e["x"], e["y"] = p2["x"] + 0.5, p2["y"]
    e["type"] = "stalker"
    e["attack_cooldown"] = 0.0
    room.step(0.1)
    assert p2["downed"] is True
    body_chests = [c for c in room.map_data["crates"] if c.get("type") == "body_chest"]
    assert len(body_chests) == 1
    assert len(body_chests[0]["items"]) > 0
    assert p2["inventory"]["equipped"]["primary_weapon"] is None
    assert len(p2["inventory"]["storage"]) == 0

    # Revive p2, add an extractable item to p1's storage, solve puzzles, and extract -> shared chest stacks!
    p2["downed"] = False
    p2["hp"] = 50
    p1["inventory"]["storage"].append(make_item("portable_turret"))
    before_turrets = shared_stash.stacks.get("portable_turret", 0)
    for pz in room.map_data["puzzles"]:
        if not pz.get("crate_id"):
            room._complete_puzzle(pz, p1["name"])
    ext = room.map_data["extraction_zone"]
    for p in room.players.values():
        p["x"] = ext["x"] + 2.0
        p["y"] = ext["y"] + 2.0
    room.step(0.1)
    assert room.phase == "victory"
    assert shared_stash.stacks.get("portable_turret", 0) >= before_turrets + 1

    shared_stash.stacks["medkit"] = max(2, initial_medkits)
    shared_stash.save()
    _reset_room()



def test_prep_screen_countdown_and_level_items():
    from backend.app.engine.inventory import shared_stash

    _reset_room()
    # Every persisted combat level has ground items and supply crates
    for rec in level_store.levels.values():
        mt = rec["map_template"]
        assert len(mt.get("loot", [])) >= 5
        assert len(mt.get("crates", [])) >= 3
        assert all("type" in lt for lt in mt["loot"])

    p1 = room.add_player("p1", "Lead")
    p2 = room.add_player("p2", "Wing")
    assert p1["class_chosen"] is False  # new players get the class overlay
    room.select_class("p1", "vanguard")
    room.select_class("p2", "medic")
    assert p1["class_chosen"] is True

    room.select_mission("p1", "outpost_101")
    assert room.prepare_mission("p2") is False  # only leader
    assert room.prepare_mission("p1") is True
    assert room.phase == "prep"

    # Move one item at a time from crate to a specific character
    shared_stash.add_template("tactical_light", 1)
    before = shared_stash.stacks.get("tactical_light", 0)
    res = room.stash_to_player("p1", "tactical_light", target_player_id="p2")
    assert res["ok"] is True
    assert shared_stash.stacks.get("tactical_light", 0) == before - 1
    light = next(it for it in p2["inventory"]["storage"] if it["template_id"] == "tactical_light")

    # Ready up -> 5s countdown -> mission starts with selected inventory
    room.toggle_ready("p1")
    assert room.prep_timer == 0.0
    room.toggle_ready("p2")
    assert room.prep_timer == 5.0
    room.step(2.0)
    assert room.phase == "prep" and 2.9 < room.prep_timer < 3.1
    room.toggle_ready("p2")  # un-ready cancels countdown
    assert room.prep_timer == 0.0
    room.toggle_ready("p2")
    for _ in range(6):
        room.step(1.0)
    assert room.phase in ("dropship", "active")
    assert any(it["id"] == light["id"] for it in p2["inventory"]["storage"])
    _reset_room()


def test_sprint_mechanic_and_noise_and_enemy_sight():
    from backend.app.config.classes import CHARACTER_CLASSES, ENEMY_TYPES
    from backend.app.engine.inventory import is_light_weapon

    # 1. Verify enemy sight ranges are reduced
    assert ENEMY_TYPES["stalker"]["sight_range"] <= 10.0
    assert ENEMY_TYPES["spitter"]["sight_range"] <= 12.0
    assert ENEMY_TYPES["brute"]["sight_range"] <= 8.5

    # 2. Verify base speed is slowed and sprint_speed is configured
    assert CHARACTER_CLASSES["vanguard"]["speed"] <= 3.5
    assert CHARACTER_CLASSES["vanguard"]["sprint_speed"] > CHARACTER_CLASSES["vanguard"]["speed"]

    _reset_room()
    p = room.add_player("p_test", "Tester")
    room.select_class("p_test", "vanguard")
    room.select_mission("p_test", "outpost_101")
    room.prepare_mission("p_test")
    room.toggle_ready("p_test")
    for _ in range(6):
        room.step(1.0)
    # Ensure active mission
    room.dropship_timer = 0.0
    room.step(0.1)
    assert room.phase == "active"

    # Aim East (angle = 0)
    room.update_input("p_test", {"aim_x": p["x"] + 10.0, "aim_y": p["y"], "dx": 1.0, "dy": 0.0, "running": False})
    room.step(0.1)
    walk_vx = p["vx"]
    assert p["running"] is False
    assert 2.5 <= walk_vx <= 3.5

    # Footstep emitted during walk is quieter
    p["footstep_timer"] = 0.0
    room.sound_events.clear()
    room.step(0.1)
    walk_sounds = [s for s in room.sound_events if s.get("kind") == "footstep"]
    assert walk_sounds and walk_sounds[0]["radius"] <= 2.5

    # Sprint forward (aim East, input East, running=True)
    room.update_input("p_test", {"aim_x": p["x"] + 10.0, "aim_y": p["y"], "dx": 1.0, "dy": 0.0, "running": True})
    room.step(0.1)
    assert p["running"] is True
    sprint_vx = p["vx"]
    assert sprint_vx >= 4.8  # Sprint speed is significantly faster

    # Footstep emitted during sprint is louder
    p["footstep_timer"] = 0.0
    room.sound_events.clear()
    room.step(0.1)
    sprint_sounds = [s for s in room.sound_events if s.get("kind") == "footstep"]
    assert sprint_sounds and sprint_sounds[0]["radius"] >= 5.0
    assert sprint_sounds[0]["radius"] > walk_sounds[0]["radius"] * 2.0

    # Running backward while aiming forward does NOT engage sprint
    room.update_input("p_test", {"aim_x": p["x"] + 10.0, "aim_y": p["y"], "dx": -1.0, "dy": 0.0, "running": True})
    room.step(0.1)
    assert p["running"] is False  # Cannot sprint backwards!
    assert p["vx"] < 0 and abs(p["vx"]) <= 3.5  # Moves at walk speed

    # Weapon firing restriction while sprinting:
    # Vanguard active weapon is Breaching Shotgun (heavy/primary)
    assert is_light_weapon(p) is False
    # Aim and try to fire shotgun while sprinting forward
    p["ammo"] = 6
    room.projectiles.clear()
    room.update_input("p_test", {"aim_x": p["x"] + 10.0, "aim_y": p["y"], "dx": 1.0, "dy": 0.0, "running": True, "firing": True})
    room.step(0.1)
    assert p["ammo"] == 6
    assert len(room.projectiles) == 0  # Blocked!

    # Switch to Service Pistol (light/secondary)
    room.handle_inventory_action("p_test", {"action": "switch_weapon", "slot": "secondary_weapon"})
    assert is_light_weapon(p) is True
    p["ammo"] = 12
    room.update_input("p_test", {"aim_x": p["x"] + 10.0, "aim_y": p["y"], "dx": 1.0, "dy": 0.0, "running": True, "firing": True})
    room.step(0.1)
    assert p["ammo"] < 12
    assert len(room.projectiles) > 0  # Pistol CAN fire while sprinting!

    _reset_room()

