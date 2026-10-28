import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.commands import parse_command
from backend.app.main import create_app, dispatch_command, simulation_loop
from backend.app.transport import Connection


@pytest.mark.parametrize("raw", ["[]", "null", "{", '{"type":"input","dx":null}', '{"type":"input","dx":"1"}', '{"type":"input","firing":1}', '{"type":"input","aim_x":NaN,"aim_y":0}', '{"type":"input","aim_x":1e999,"aim_y":0}', '{"type":"input","aim_x":0}', '{"type":"reload","name":"spoof"}', '{"type":"reload","type":"skill"}', '{"type":"puzzle_action","puzzle_id":"p","action":[]}', '{"type":"generate_level","seed":true}', '{"type":"nope"}'])
def test_invalid_commands_are_rejected(raw):
    with pytest.raises(ValueError):
        parse_command(raw)


def test_size_and_finite_numeric_bounds():
    with pytest.raises(ValueError):
        parse_command(' ' * 8193)
    assert parse_command('{"type":"input","dx":1,"dy":0,"firing":false}')["dx"] == 1


def test_direct_phase_and_unlock_guards(game_room):
    player = game_room.add_player("leader")
    for record in game_room.level_store.levels.values():
        record["completed"] = False
    game_room.select_mission("leader", "reactor_303")
    assert game_room.start_mission("leader") is False
    game_room.select_mission("leader", "outpost_101")
    assert game_room.start_mission("leader")
    before = copy.deepcopy(player)
    assert game_room.start_mission("leader") is False
    assert game_room.select_class("leader", "medic") is False
    assert game_room.restore_min_loadout("leader") is False
    assert player == before
    game_room.phase = "victory"
    assert game_room.trigger_reload("leader") is False
    assert game_room.handle_inventory_action("leader", {"action": "use_item", "slot": "quick_use"})["ok"] is False


def test_invalid_direct_input_cannot_partially_mutate_player(game_room):
    player = game_room.add_player("leader")
    game_room.phase = "active"
    original = copy.deepcopy(player["input"])
    assert game_room.update_input("leader", {"dx": 1, "aim_x": float("nan"), "aim_y": 0}) is False
    assert player["input"] == original
    json.dumps(game_room.get_snapshot(), allow_nan=False)


def test_spoofed_self_owned_actions_fail_but_squad_transfers_work(game_room):
    game_room.add_player("leader")
    other = game_room.add_player("other")
    with pytest.raises(ValueError, match="self-owned"):
        dispatch_command(game_room, "leader", {"type": "toggle_ready", "target_player_id": "other", "ready": True})
    with pytest.raises(ValueError, match="self-owned"):
        dispatch_command(game_room, "leader", {"type": "min_loadout", "target_player_id": "other"})
    assert not other["ready"]
    game_room.shared_stash.add_template("medkit")
    response = dispatch_command(game_room, "leader", {"type": "stash_to_player", "target_player_id": "other", "template_id": "medkit"})
    assert response["ok"] is True


def test_websocket_invalid_commands_keep_session_usable(game_room):
    async def idle(room, clients):
        await asyncio.Event().wait()

    application = create_app(game_room, simulation=idle)
    with TestClient(application) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            websocket.receive_json()
            websocket.send_text("[]")
            assert websocket.receive_json()["type"] == "command_error"
            websocket.send_json({"type": "set_name", "name": "Valid"})
            assert websocket.receive_json()["players"][0]["name"] == "Valid"
        assert not application.state.clients


def test_development_generation_disabled_and_validated(game_room):
    with TestClient(create_app(game_room)) as client:
        assert client.post("/api/levels/generate", json={}).status_code == 403
    with TestClient(create_app(game_room, development_tools=True)) as client:
        assert client.post("/api/levels/generate", json={"seed": "bad"}).status_code == 422
        assert client.post("/api/levels/generate", json={"mission_id": "unknown"}).status_code == 422


def test_slow_connection_does_not_block_fast_and_snapshots_are_bounded():
    async def verify():
        blocker = asyncio.Event()
        fast_sent = asyncio.Event()

        class Socket:
            def __init__(self, slow=False):
                self.slow = slow
                self.messages = []
                self.active = False

            async def send_text(self, message):
                assert not self.active
                self.active = True
                if self.slow:
                    await blocker.wait()
                self.messages.append(json.loads(message))
                self.active = False
                fast_sent.set()

            async def close(self):
                pass

        slow = Connection(Socket(True), lambda: None)
        fast = Connection(Socket(), lambda: None)
        try:
            slow.enqueue({"n": 0}, snapshot=True)
            fast.enqueue({"type": "feedback"})
            fast.enqueue({"type": "state"}, snapshot=True)
            for n in range(1000):
                slow.enqueue({"n": n}, snapshot=True)
            await asyncio.wait_for(fast_sent.wait(), 1)
            assert len(fast.websocket.messages) >= 1
            assert not slow.reliable
            assert len(slow.websocket.messages) == 0
            assert slow.snapshot is None or json.loads(slow.snapshot)["n"] == 999
            fast.tokens = 1
            now = fast.updated
            assert fast.admit(now)
            assert not fast.admit(now)
            assert fast.admit(now + 1)
        finally:
            await slow.close()
            await fast.close()

    asyncio.run(verify())


def test_reliable_queue_overflow_disconnects_once():
    async def verify():
        calls = []

        class Socket:
            async def send_text(self, message):
                await asyncio.Event().wait()

            async def close(self):
                pass

        connection = Connection(Socket(), lambda: calls.append("closed"), reliable_limit=1)
        assert connection.enqueue({"n": 1})
        assert not connection.enqueue({"n": 2})
        await connection.close()
        assert calls == ["closed"]

    asyncio.run(verify())


def test_monotonic_schedule_caps_catchup_and_does_not_add_step_duration(game_room):
    async def verify():
        now = [0.0]
        steps = []
        sleeps = []
        game_room.phase = "active"

        def step(dt):
            steps.append(dt)
            now[0] += 0.01

        async def sleep(delay):
            sleeps.append(delay)
            if len(sleeps) == 6:
                raise asyncio.CancelledError()
            if len(sleeps) == 1:
                now[0] += 10
            else:
                now[0] += delay

        game_room.step = step
        with pytest.raises(asyncio.CancelledError):
            await simulation_loop(game_room, {}, clock=lambda: now[0], sleep=sleep)
        assert all(dt == 1 / 30 for dt in steps)
        assert len(steps) <= 11
        assert any(0 < delay < 1 / 30 for delay in sleeps)

    asyncio.run(verify())


def test_stale_input_stops_movement_and_fire(game_room):
    player = game_room.add_player("leader")
    game_room.start_mission("leader")
    game_room.update_input("leader", {"dx": 1.0, "dy": 0.0, "firing": True})
    game_room.map_data["enemies"] = []
    for _ in range(40):
        game_room.step(1 / 30)
    assert player["input"]["dx"] == 0
    assert player["input"]["firing"] is False


def test_sender_disconnect_error_is_cleaned_once():
    from starlette.websockets import WebSocketDisconnect

    async def verify():
        calls = []

        class Socket:
            async def send_text(self, message):
                raise WebSocketDisconnect()

            async def close(self):
                raise WebSocketDisconnect()

        connection = Connection(Socket(), lambda: calls.append("closed"))
        connection.enqueue({"type": "state"}, snapshot=True)
        await asyncio.wait_for(connection.task, timeout=1)
        await connection.close()
        assert calls == ["closed"]

    asyncio.run(verify())


def test_slow_sender_times_out_and_cleans_up():
    async def verify():
        calls = []

        class Socket:
            async def send_text(self, message):
                await asyncio.Event().wait()

            async def close(self):
                pass

        connection = Connection(Socket(), lambda: calls.append("closed"), send_timeout=0.01)
        connection.enqueue({"type": "state"}, snapshot=True)
        await asyncio.wait_for(connection.task, timeout=1)
        assert connection.closed
        assert calls == ["closed"]

    asyncio.run(verify())


def test_development_overrides_and_launch_readiness_are_explicit(game_room):
    game_room.add_player("leader")
    with pytest.raises(ValueError, match="Development"):
        dispatch_command(game_room, "leader", {"type": "generate_level"})
    with pytest.raises(ValueError, match="Test mode"):
        dispatch_command(game_room, "leader", {"type": "start_mission", "test_mode": True})
    with pytest.raises(ValueError, match="all-ready"):
        dispatch_command(game_room, "leader", {"type": "start_mission"})
    game_room.prepare_mission("leader")
    game_room.toggle_ready("leader", True)
    dispatch_command(game_room, "leader", {"type": "start_mission"})
    assert game_room.phase == "dropship"
