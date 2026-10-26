"""
Unit and integration tests for the Co-op Isometric Extraction Game.
"""

from fastapi.testclient import TestClient
from backend.app.main import app, room
from backend.app.engine.map_builder import build_mission_map
from backend.app.engine.physics import (
    circle_vs_aabb,
    has_line_of_sight,
    move_with_collisions,
)


def test_map_segment_stitching():
    m = build_mission_map("outpost")
    assert len(m["segments"]) == 4
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
    room.players.clear()
    room.join_order.clear()
    room.leader_id = None
    room.reset_to_lobby()

    p1 = room.add_player("p1", "Scout")
    room.select_class("p1", "vanguard")
    room.start_mission("p1")
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

