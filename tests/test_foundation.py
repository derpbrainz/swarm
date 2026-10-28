import asyncio
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_imports_do_not_load_or_write_saves():
    source = """
from unittest.mock import patch
from pathlib import Path
with patch.object(Path, 'write_text', side_effect=AssertionError('import writes save')):
    import backend.app.main as main
    from backend.app.engine.game_loop import GameRoom
    assert main.app.state.room.level_store.levels == {}
    assert GameRoom().shared_stash.stacks == {}
"""
    subprocess.run([sys.executable, "-c", source], check=True, cwd=Path(__file__).resolve().parents[1])


def test_rooms_and_apps_have_independent_state(game_room):
    from backend.app.engine.game_loop import GameRoom

    other = GameRoom()
    game_room.add_player("one")
    assert not other.players
    assert other.level_store is not game_room.level_store
    assert create_app(game_room).state.clients is not create_app(other).state.clients


def test_lifespan_runs_and_awaits_simulation_shutdown(game_room):
    events = []

    async def simulation(room, clients):
        events.append("started")
        try:
            room.step(1 / 30)
            await asyncio.Event().wait()
        finally:
            events.append("stopped")

    application = create_app(game_room, simulation=simulation)
    with TestClient(application) as client:
        assert client.get("/api/config").status_code == 200
        assert events == ["started"]
    assert events == ["started", "stopped"]
    assert application.state.loop_task.done()


def test_default_simulation_executes_a_fixed_step(game_room, monkeypatch):
    async def verify():
        stepped = asyncio.Event()
        observed = []
        game_room.phase = "prep"
        game_room.prep_timer = 1
        original = game_room.step

        def step(dt):
            observed.append(dt)
            original(dt)
            stepped.set()

        monkeypatch.setattr(game_room, "step", step)
        application = create_app(game_room)
        async with application.router.lifespan_context(application):
            await asyncio.wait_for(stepped.wait(), timeout=2)
        assert observed[0] == 1 / 30
        assert game_room.prep_timer < 1
        assert application.state.loop_task.done()

    asyncio.run(verify())
