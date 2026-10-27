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

from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config.classes import CHARACTER_CLASSES, ENEMY_TYPES
from backend.app.config.missions import MISSIONS
from backend.app.engine.game_loop import GameRoom
from backend.app.engine.level_store import level_store

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src"

room = GameRoom()
clients: dict[str, WebSocket] = {}
_loop_task: asyncio.Task | None = None


async def broadcast_snapshots() -> None:
    if not clients:
        return
    stale: list[str] = []
    for pid, ws in list(clients.items()):
        try:
            snap = room.get_snapshot(listener_id=pid)
            await ws.send_text(json.dumps(snap))
        except Exception:
            stale.append(pid)
    for pid in stale:
        clients.pop(pid, None)
        room.remove_player(pid)

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
    last_prep_sec = -1
    while True:
        try:
            if room.phase == "prep":
                if room.prep_timer > 0.0:
                    room.step(dt)
                    cur_sec = int(room.prep_timer * 10)
                    if clients and (room.phase != "prep" or cur_sec != last_prep_sec):
                        last_prep_sec = cur_sec
                        await broadcast_snapshots()
            elif room.phase != "lobby":
                room.step(dt)
                if clients:
                    await broadcast_snapshots()
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


@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/api/config")
async def get_config() -> JSONResponse:
    return JSONResponse(
        {
            "classes": CHARACTER_CLASSES,
            "missions": MISSIONS,
            "enemies": ENEMY_TYPES,
            "levels": level_store.list_levels(),
        }
    )


@app.get("/api/levels")
async def get_levels() -> JSONResponse:
    return JSONResponse({"levels": level_store.list_levels()})


@app.post("/api/levels/generate")
async def generate_level(body: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
    mission_id = str(body.get("mission_id", "outpost")) if body else "outpost"
    seed = body.get("seed") if body else None
    rec = level_store.generate_level(mission_id, seed=seed, tested=False)
    return JSONResponse(rec)


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
                "levels": level_store.list_levels(),
                "state": room.get_snapshot(),
            }
        )
    )
    await broadcast_snapshots()

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "set_name":
                room.set_player_name(player_id, str(msg.get("name", "")))
                await broadcast_snapshots()

            elif mtype == "select_class":
                room.select_class(player_id, str(msg.get("class_id", "vanguard")))
                await broadcast_snapshots()

            elif mtype == "select_mission":
                level_id = msg.get("level_id") or msg.get("mission_id", "outpost")
                if room.select_mission(player_id, str(level_id), enforce_unlock=True):
                    if msg.get("prepare", True):
                        room.prepare_mission(player_id)
                await broadcast_snapshots()

            elif mtype == "generate_level":
                room.generate_level(player_id, msg.get("mission_id"), msg.get("seed"))
                await broadcast_snapshots()

            elif mtype == "prepare_mission":
                if room.prepare_mission(player_id):
                    await broadcast_snapshots()

            elif mtype == "start_mission":
                if room.start_mission(player_id, force_test_mode=msg.get("test_mode")):
                    await broadcast_snapshots()

            elif mtype == "toggle_ready":
                target_pid = str(msg.get("target_player_id") or player_id)
                room.toggle_ready(target_pid, msg.get("ready"))
                await broadcast_snapshots()

            elif mtype == "min_loadout":
                target_pid = str(msg.get("target_player_id") or player_id)
                room.restore_min_loadout(target_pid)
                await broadcast_snapshots()

            elif mtype == "stash_to_player":
                res = room.stash_to_player(
                    player_id,
                    str(msg.get("template_id", "")),
                    msg.get("target_slot"),
                    msg.get("target_player_id"),
                )
                await ws.send_text(json.dumps({"type": "inventory_feedback", **res}))
                await broadcast_snapshots()

            elif mtype == "player_to_stash":
                res = room.player_to_stash(
                    player_id,
                    str(msg.get("item_id", "")),
                    msg.get("target_player_id"),
                )
                await ws.send_text(json.dumps({"type": "inventory_feedback", **res}))
                await broadcast_snapshots()

            elif mtype == "inventory_action":
                res = room.handle_inventory_action(player_id, msg)
                await ws.send_text(json.dumps({"type": "inventory_feedback", **res}))
                await broadcast_snapshots()

            elif mtype == "pickup_item":
                item_id = str(msg.get("item_id", ""))
                room.pickup_ground_item(player_id, item_id)
                await broadcast_snapshots()

            elif mtype == "take_crate_item":
                res = room.take_crate_item(
                    player_id,
                    str(msg.get("crate_id", "")),
                    str(msg.get("item_id", "")),
                    msg.get("target_slot"),
                )
                await ws.send_text(json.dumps({"type": "chest_update", **res}))
                await broadcast_snapshots()

            elif mtype == "return_lobby":
                if player_id == room.leader_id or room.phase in ("victory", "defeat"):
                    room.reset_to_lobby()
                    await broadcast_snapshots()

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
                                "crate": res.get("crate"),
                                "item": res.get("item"),
                                "message": res.get("message"),
                                "target_id": res.get("target_id"),
                            }
                        )
                    )
                    await broadcast_snapshots()

            elif mtype == "puzzle_action":
                res = room.handle_puzzle_action(
                    player_id,
                    str(msg.get("puzzle_id", "")),
                    msg.get("action", {}),
                )
                await ws.send_text(json.dumps({"type": "puzzle_update", **res}))
                await broadcast_snapshots()

    except WebSocketDisconnect:
        pass
    finally:
        clients.pop(player_id, None)
        room.remove_player(player_id)
        await broadcast_snapshots()


if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))
