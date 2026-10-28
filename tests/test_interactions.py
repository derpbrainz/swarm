import copy
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.commands import parse_command
from backend.app.config.items import make_item
from backend.app.content import RULES
from backend.app.main import create_app, dispatch_command


@pytest.fixture
def interaction_room(game_room):
    player = game_room.add_player("leader")
    game_room.start_mission("leader")
    game_room.phase = "active"
    player.update(x=10.0, y=10.0)
    game_room.map_data["walls"] = []
    game_room.map_data["enemies"] = []
    game_room.map_data["loot"] = []
    game_room.map_data["puzzles"] = [{"id": "terminal", "x": 10, "y": 12, "radius": 2.8, "solved": False,
        "type": "power_reroute", "label": "Terminal", "door_id": "door", "data": {"type": "power_reroute", "nodes": [1, 1, 1, 1], "target": [0, 0, 0, 0]}}]
    game_room.map_data["crates"] = [
        {"id": "near", "name": "Nearby", "x": 10.2, "y": 10, "radius": 2.5, "locked": False, "items": []},
        {"id": "chosen", "name": "Chosen", "x": 12.5, "y": 10, "radius": 2.5, "locked": False, "items": [make_item("credits")]},
    ]
    return game_room


def test_explicit_chosen_crate_never_selects_closer_terminal_or_crate(interaction_room):
    assert interaction_room.handle_interact("leader")["crate"]["id"] == "near"
    result = interaction_room.handle_interact("leader", "chosen")
    assert result["crate"]["id"] == "chosen"
    assert result["target_id"] == "chosen"


@pytest.mark.parametrize("distance,ok", [(3.5, True), (3.5001, False), (3.6, False)])
def test_common_crate_open_and_take_range(interaction_room, distance, ok):
    crate = interaction_room.map_data["crates"][1]
    crate["x"] = 10 + distance
    original = copy.deepcopy(interaction_room.players["leader"])
    opened = interaction_room.handle_interact("leader", crate["id"])
    assert opened.get("ok", True) is ok
    result = interaction_room.take_crate_item("leader", crate["id"], crate["items"][0]["id"])
    assert result["ok"] is ok
    if not ok:
        assert "range" in result["message"]
        assert interaction_room.players["leader"] == original


@pytest.mark.parametrize("kind", ["crate", "puzzle", "loot", "revive"])
def test_explicit_targets_and_mutations_reject_line_of_sight(interaction_room, kind):
    room = interaction_room
    room.map_data["walls"] = [{"id": "barrier", "x": 11, "y": 0, "w": .05, "h": 30, "active": True, "blocks_sight": True}]
    if kind == "crate":
        target = room.map_data["crates"][1]
    elif kind == "puzzle":
        target = room.map_data["puzzles"][0]
        target.update(x=12, y=10)
    elif kind == "loot":
        target = make_item("credits")
        target.update(x=12, y=10, collected=False)
        room.map_data["loot"] = [target]
    else:
        target = room.add_player("ally")
        target.update(x=12, y=10, downed=True)
    before = copy.deepcopy((room.players, room.map_data, room.shared_stash.holdings))
    result = room.handle_interact("leader", target["id"])
    assert not result["ok"] and "line of sight" in result["message"]
    if kind == "crate":
        assert not room.take_crate_item("leader", target["id"], target["items"][0]["id"])["ok"]
    if kind == "puzzle":
        assert not room.handle_puzzle_action("leader", target["id"], {"action": "rotate", "node_index": 0})["ok"]
    if kind == "loot":
        assert not room.pickup_ground_item("leader", target["id"])["ok"]
    assert (room.players, room.map_data, room.shared_stash.holdings) == before
    room.map_data["walls"][0]["active"] = False
    assert room.handle_interact("leader", target["id"]).get("ok", True)


@pytest.mark.parametrize("state", ["deleted", "solved", "collected", "locked", "ineligible"])
def test_stale_or_ineligible_target_has_feedback_and_no_mutation(interaction_room, state):
    room = interaction_room
    if state == "deleted":
        identity = "missing"
    elif state == "solved":
        target = room.map_data["puzzles"][0]
        target["solved"] = True
        identity = target["id"]
    elif state == "collected":
        target = make_item("credits")
        target.update(x=10, y=10, collected=True)
        room.map_data["loot"] = [target]
        identity = target["id"]
    elif state == "locked":
        target = room.map_data["crates"][1]
        target["locked"] = True
        identity = target["id"]
    else:
        identity = "leader"
    before = copy.deepcopy((room.players, room.map_data))
    assert room.handle_interact("leader", identity)["ok"] is False
    assert (room.players, room.map_data) == before


def test_correlated_responses_reject_duplicate_wrong_session_and_old_world(interaction_room):
    room = interaction_room
    command = {"type": "interact", "target_id": "chosen", "request_id": "0:1", "session_id": "leader", "world_id": room.map_data["world_id"]}
    response = dispatch_command(room, "leader", parse_command(json.dumps(command)))
    for key in ("request_id", "session_id", "world_id", "target_id"):
        assert response[key] == command[key]
    assert response["interaction_revision"] == room.get_snapshot()["interaction_revision"]
    with pytest.raises(ValueError, match="Duplicate"):
        dispatch_command(room, "leader", command)
    for key in ("world_id", "session_id"):
        bad = {**command, key: "previous", "request_id": "0:2"}
        with pytest.raises(ValueError, match="Stale"):
            dispatch_command(room, "leader", bad)


@pytest.mark.parametrize("extra", [{"request_id": "a"}, {"session_id": "p", "world_id": "w"}, {"target_id": None}, {"request_id": 1}])
def test_correlation_schema_is_strict(extra):
    with pytest.raises(ValueError):
        parse_command(json.dumps({"type": "interact", **extra}))


def test_runtime_range_uses_shared_rules(interaction_room, monkeypatch):
    monkeypatch.setattr("backend.app.engine.game_loop.RULES", {**RULES, "interaction": {**RULES["interaction"], "interact_radius": 2}})
    assert not interaction_room.handle_interact("leader", "chosen")["ok"]


@pytest.mark.parametrize("dx", [0.0, -1.0, 1.0])
def test_shift_is_authoritative_actual_sprint_not_held_key(interaction_room, dx):
    room = interaction_room
    p = room.players["leader"]
    room.update_input("leader", {"dx": dx, "dy": 0.0, "aim_x": 30.0, "aim_y": 10.0, "firing": True, "running": True})
    ammo = p["ammo"]
    room.step(1 / 30)
    assert p["input"]["firing"] is True
    assert p["running"] is (dx > 0)
    assert (p["ammo"] < ammo) is (dx <= 0)


def test_test_fixture_endpoints_not_in_production_application(game_room):
    with TestClient(create_app(game_room)) as client:
        assert client.post("/__test__/fixture", json={"action": "install"}).status_code == 404


@pytest.mark.parametrize("phase", ["lobby", "prep", "victory", "defeat"])
def test_neutral_input_accepted_in_noncombat_phase_without_commands_or_motion(game_room, phase):
    p = game_room.add_player("leader")
    game_room.phase = phase
    p["input"].update(dx=1.0, dy=1.0, running=True, firing=True)
    assert game_room.update_input("leader", {"dx": 0.0, "dy": 0.0, "running": False, "firing": False})
    assert not p["input"]["firing"] and not p["input"]["running"]
    assert not game_room.update_input("leader", {"dx": 1.0, "dy": 0.0, "running": False, "firing": False})


def test_failed_crate_take_reply_retains_request_target_and_retryable_modal(interaction_room, monkeypatch):
    from backend.app.engine.persistence import SaveError
    import asyncio

    async def idle(room, clients):
        await asyncio.Event().wait()

    def fail(*args, **kwargs):
        raise SaveError("fixture failure")

    application = create_app(interaction_room, simulation=idle)
    with TestClient(application) as client:
        with client.websocket_connect("/ws") as socket:
            welcome = socket.receive_json()
            socket.receive_json()
            identity = welcome["player_id"]
            interaction_room.players[identity].update(x=10.0, y=10.0)
            monkeypatch.setattr(interaction_room.shared_stash, "commit_player", fail)
            command = {"type": "take_crate_item", "crate_id": "chosen", "item_id": interaction_room.map_data["crates"][1]["items"][0]["id"],
                "request_id": "1:1", "session_id": identity, "world_id": interaction_room.map_data["world_id"]}
            socket.send_json(command)
            reply = socket.receive_json()
            assert reply["type"] == "command_error"
            for key in ("request_id", "session_id", "world_id"):
                assert reply[key] == command[key]
            assert reply["target_id"] == "chosen"


def test_puzzle_direct_action_revision_preserves_coop_progress_order(interaction_room):
    room = interaction_room
    before = room.interaction_revision
    assert room.handle_puzzle_action("leader", "terminal", {"action": "rotate", "node_index": 0})["ok"]
    assert room.interaction_revision > before
    assert room.get_snapshot()["interaction_revision"] == room.interaction_revision
