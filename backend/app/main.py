"""
FastAPI webserver and WebSocket hub for the Co-op Isometric Extraction Game.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config.classes import CHARACTER_CLASSES, ENEMY_TYPES
from backend.app.config.missions import MISSIONS
from backend.app.engine.game_loop import GameRoom

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src"

room = GameRoom()
clients: dict[str, WebSocket] = {}
_loop_task: asyncio.Task | None = None


async def broadcast_json(payload: dict[str, Any]) -> None:
    if not clients:
        return
    msg = json.dumps(payload)
    stale: list[str] = []
    for pid, ws in list(clients.items()):
        try:
            await ws.send_text(msg)
        except Exception:
            stale.append(pid)
    for pid in stale:
        clients.pop(pid, None)
        room.remove_player(pid)


async def simulation_loop() -> None:
    tick_rate = 30.0
    dt = 1.0 / tick_rate
    while True:
        try:
            room.step(dt)
            if clients:
                await broadcast_json(room.get_snapshot())
        except Exception as exc:
            print(f"[SimLoop Error] {exc}")
        await asyncio.sleep(dt)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop_task
    _loop_task = asyncio.create_task(simulation_loop())
    try:
        yield
    finally:
        if _loop_task:
            _loop_task.cancel()


app = FastAPI(title="Alien Swarm Isometric Extraction Co-op", lifespan=lifespan)


@app.get("/api/config")
async def get_config() -> JSONResponse:
    return JSONResponse(
        {
            "classes": CHARACTER_CLASSES,
            "missions": MISSIONS,
            "enemies": ENEMY_TYPES,
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    player_id = uuid.uuid4().hex[:8]
    clients[player_id] = ws
    player = room.add_player(player_id)

    await ws.send_text(
        json.dumps(
            {
                "type": "welcome",
                "player_id": player_id,
                "classes": CHARACTER_CLASSES,
                "missions": MISSIONS,
                "state": room.get_snapshot(),
            }
        )
    )
    await broadcast_json(room.get_snapshot())

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "set_name":
                room.set_player_name(player_id, str(msg.get("name", "")))
                await broadcast_json(room.get_snapshot())

            elif mtype == "select_class":
                room.select_class(player_id, str(msg.get("class_id", "vanguard")))
                await broadcast_json(room.get_snapshot())

            elif mtype == "select_mission":
                room.select_mission(player_id, str(msg.get("mission_id", "outpost")))
                await broadcast_json(room.get_snapshot())

            elif mtype == "start_mission":
                if room.start_mission(player_id):
                    await broadcast_json(room.get_snapshot())

            elif mtype == "return_lobby":
                if player_id == room.leader_id or room.phase in ("victory", "defeat"):
                    room.reset_to_lobby()
                    await broadcast_json(room.get_snapshot())

            elif mtype == "input":
                room.update_input(player_id, msg)

            elif mtype == "reload":
                room.trigger_reload(player_id)

            elif mtype == "skill":
                room.trigger_skill(player_id)

            elif mtype == "interact":
                res = room.handle_interact(player_id)
                if res:
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "interact_result",
                                "interact_type": res.get("type"),
                                "puzzle": res.get("puzzle"),
                                "target_id": res.get("target_id"),
                            }
                        )
                    )

            elif mtype == "puzzle_action":
                res = room.handle_puzzle_action(
                    player_id,
                    str(msg.get("puzzle_id", "")),
                    msg.get("action", {}),
                )
                await ws.send_text(json.dumps({"type": "puzzle_update", **res}))
                await broadcast_json(room.get_snapshot())

    except WebSocketDisconnect:
        pass
    finally:
        clients.pop(player_id, None)
        room.remove_player(player_id)
        await broadcast_json(room.get_snapshot())


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))
