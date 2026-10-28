"""FastAPI transport and explicit single-room application lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os
from pathlib import Path
import time
from typing import Any
import uuid

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from backend.app.commands import Command, parse_command
from backend.app.config.classes import CHARACTER_CLASSES, ENEMY_TYPES
from backend.app.config.missions import MISSIONS
from backend.app.content import CATALOG, RULES, public_payload
from backend.app.engine.game_loop import GameRoom
from backend.app.engine.persistence import SaveError, writer_lease
from backend.app.transport import Connection

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src"
logger = logging.getLogger(__name__)


async def broadcast_snapshots(room: GameRoom, clients: dict[str, Connection]) -> None:
    for pid, connection in list(clients.items()):
        connection.enqueue(room.get_snapshot(listener_id=pid), snapshot=True)


async def broadcast_json(payload: dict[str, Any], room: GameRoom, clients: dict[str, Connection]) -> None:
    for connection in list(clients.values()):
        connection.enqueue(payload)


async def simulation_loop(room: GameRoom, clients: dict[str, Connection], *, clock=time.monotonic, sleep=asyncio.sleep) -> None:
    dt = 1 / RULES["timing"]["tick_rate"]
    deadline = clock()
    while True:
        now = clock()
        if now < deadline:
            await sleep(deadline - now)
            continue
        catchup = min(RULES["timing"]["max_catchup"], int((now - deadline) / dt) + 1)
        for _ in range(catchup):
            if room.phase != "lobby" or room.map_data or room.pending_disconnects:
                try:
                    room.step(dt)
                except SaveError:
                    logger.exception("Save failed; pending settlement will retry next simulation step")
            deadline += dt
        if deadline <= now:
            deadline = now + dt
        if clients and (room.phase != "lobby" or room.map_data):
            await broadcast_snapshots(room, clients)
        await sleep(0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    room = app.state.room
    with writer_lease([room.level_store.data_file, room.shared_stash.path]):
        room.initialize()
        app.state.loop_task = asyncio.create_task(app.state.simulation(room, app.state.clients))
        try:
            yield
        finally:
            app.state.loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.loop_task
            await asyncio.gather(*(connection.close() for connection in list(app.state.clients.values())))


async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    return response


async def get_config(request: Request) -> JSONResponse:
    return JSONResponse({
        **CATALOG.client_view(),
        "levels": request.app.state.room.level_store.list_levels(),
        "development_tools": request.app.state.development_tools,
    })


async def get_levels(request: Request) -> JSONResponse:
    return JSONResponse({"levels": request.app.state.room.level_store.list_levels()})


async def generate_level(request: Request, body: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
    if not request.app.state.development_tools:
        raise HTTPException(403, "Development generation disabled")
    if len(await request.body()) > 8192:
        raise HTTPException(413, "Generation request too large")
    now = time.monotonic()
    if now - request.app.state.last_generation < 1:
        raise HTTPException(429, "Generation rate exceeded")
    try:
        command = Command.model_validate({**(body or {}), "type": "generate_level"})
        mission = command.mission_id or "outpost"
        if mission not in MISSIONS:
            raise ValueError("Unknown mission")
        request.app.state.last_generation = now
        record = request.app.state.room.level_store.generate_level(mission, command.seed)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "Invalid generation request") from exc
    except SaveError as exc:
        raise HTTPException(503, str(exc)) from exc
    return JSONResponse(public_payload(record))


def dispatch_command(room: GameRoom, player_id: str, command: dict, *, development_tools=False) -> dict | None:
    correlation = {key: command[key] for key in ("request_id", "session_id", "world_id") if key in command} if command.get("request_id") else {}
    if correlation:
        if command.get("session_id") != player_id or command.get("world_id") != (room.map_data or {}).get("world_id"):
            raise ValueError("Stale interaction session or world")
        requests = room.interaction_requests.setdefault(player_id, [])
        if command["request_id"] in requests:
            raise ValueError("Duplicate interaction request")
        requests.append(command["request_id"])
        del requests[:-128]
    result = _dispatch_command(room, player_id, command, development_tools=development_tools)
    if result is not None:
        target_id = command.get("target_id") or command.get("crate_id") or command.get("puzzle_id") or result.get("target_id")
        if command["type"] == "puzzle_action":
            _, _, crate = room.interaction_target(command["puzzle_id"])
            if crate:
                target_id = crate["id"]
        result.update(correlation)
        result["target_id"] = target_id
        result["interaction_revision"] = room.interaction_revision
    return result


def _dispatch_command(room: GameRoom, player_id: str, command: dict, *, development_tools=False) -> dict | None:
    kind = command["type"]
    target = command.get("target_player_id")
    if kind in ("toggle_ready", "min_loadout") and target not in (None, player_id):
        raise ValueError("This action is self-owned")
    if kind == "set_name":
        result = room.set_player_name(player_id, command["name"])
    elif kind == "select_class":
        result = room.select_class(player_id, command["class_id"])
    elif kind == "select_mission":
        result = room.select_mission(player_id, command.get("level_id") or command["mission_id"], enforce_unlock=True, target_id=command.get("target_id"))
        if result and command.get("prepare", True):
            result = room.prepare_mission(player_id, command.get("target_id"))
    elif kind == "generate_level":
        if not development_tools:
            raise ValueError("Development generation disabled")
        result = room.generate_level(player_id, command.get("mission_id"), command.get("seed"))
    elif kind == "prepare_mission":
        result = room.prepare_mission(player_id, command.get("target_id"))
    elif kind == "start_mission":
        if room.phase != "prep" or not room.players or not all(p.get("ready") for p in room.players.values()):
            raise ValueError("Launch requires all-ready preparation")
        result = room.start_mission(player_id, command.get("target_id"))
    elif kind == "toggle_ready":
        result = room.toggle_ready(player_id, command.get("ready"))
    elif kind == "min_loadout":
        result = room.restore_min_loadout(player_id)
    elif kind == "stash_to_player":
        return {"type": "inventory_feedback", **room.stash_to_player(player_id, command["template_id"], command.get("target_slot"), target, command.get("target_id"))}
    elif kind == "player_to_stash":
        return {"type": "inventory_feedback", **room.player_to_stash(player_id, command["item_id"], target, command.get("target_id"))}
    elif kind == "inventory_action":
        return {"type": "inventory_feedback", **room.handle_inventory_action(player_id, command)}
    elif kind == "pickup_item":
        return {"type": "inventory_feedback", **room.pickup_ground_item(player_id, command["item_id"])}
    elif kind == "take_crate_item":
        return {"type": "chest_update", **room.take_crate_item(player_id, command["crate_id"], command["item_id"], command.get("target_slot"))}
    elif kind == "return_lobby":
        result = player_id == room.leader_id or room.phase in ("victory", "defeat")
        if result:
            room.reset_to_lobby()
    elif kind == "report_broken_level":
        return {"type": "level_report_feedback", **room.report_broken_level(player_id, command["level_id"], command["world_id"], command["reason"])}
    elif kind == "input":
        result = room.update_input(player_id, command)
    elif kind == "reload":
        result = room.trigger_reload(player_id)
    elif kind == "skill":
        result = room.trigger_skill(player_id)
    elif kind == "interact":
        interaction = room.handle_interact(player_id, command.get("target_id"))
        if not interaction:
            raise ValueError("No available interaction")
        return {"type": "interact_result", "interact_type": interaction["type"], **{key: value for key, value in interaction.items() if key != "type"}}
    elif kind == "puzzle_action":
        return {"type": "puzzle_update", **room.handle_puzzle_action(player_id, command["puzzle_id"], command["action"])}
    else:
        raise ValueError("Unknown command")
    if not result:
        raise ValueError("Action not allowed or unavailable")
    return {"type": "service_feedback", "ok": True} if command.get("request_id") else None


async def websocket_endpoint(ws: WebSocket) -> None:
    room, clients = ws.app.state.room, ws.app.state.clients
    await ws.accept()
    if len(clients) >= 4:
        await ws.close(code=1013, reason="Squad full")
        return
    player_id = uuid.uuid4().hex

    def disconnected():
        clients.pop(player_id, None)
        room.remove_player(player_id)

    connection = Connection(ws, disconnected)
    clients[player_id] = connection
    room.add_player(player_id)
    try:
        connection.enqueue({
            "type": "welcome", "player_id": player_id,
            "catalog": CATALOG.client_view(),
            "levels": room.level_store.list_levels(), "state": room.get_snapshot(player_id),
        })
        await broadcast_snapshots(room, clients)
        while not connection.closed:
            raw = await ws.receive_text()
            command = None
            try:
                command = parse_command(raw)
                if not connection.admit():
                    raise ValueError("Command rate exceeded")
                response = dispatch_command(room, player_id, command, development_tools=ws.app.state.development_tools)
                if response:
                    connection.enqueue(public_payload(response))
                if command["type"] != "input":
                    await broadcast_snapshots(room, clients)
            except (ValueError, ValidationError) as exc:
                metadata = {key: command[key] for key in ("request_id", "session_id", "world_id") if command and key in command}
                target_id = (command or {}).get("target_id") or (command or {}).get("crate_id") or (command or {}).get("puzzle_id")
                if command and command["type"] == "puzzle_action":
                    _, _, crate = room.interaction_target(command["puzzle_id"])
                    if crate:
                        target_id = crate["id"]
                connection.enqueue({"type": "command_error", "message": str(exc)[:300], "target_id": target_id, **metadata})
            except SaveError:
                logger.exception("Persistence failed during command")
                metadata = {key: command[key] for key in ("request_id", "session_id", "world_id") if command and key in command}
                target_id = (command or {}).get("target_id") or (command or {}).get("crate_id") or (command or {}).get("puzzle_id")
                if command and command["type"] == "puzzle_action":
                    _, _, crate = room.interaction_target(command["puzzle_id"])
                    if crate:
                        target_id = crate["id"]
                connection.enqueue({"type": "command_error", "message": "Save failed; action requires recovery", "target_id": target_id, **metadata})
    except WebSocketDisconnect:
        pass
    finally:
        await connection.close()
        await broadcast_snapshots(room, clients)


async def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))


def create_app(room: GameRoom | None = None, *, simulation=simulation_loop, development_tools=False, hub_world=None) -> FastAPI:
    application = FastAPI(title="Alien Swarm Isometric Extraction Co-op", lifespan=lifespan)
    if room is not None and hub_world is not None:
        raise ValueError("Configure hub_world on the supplied room")
    application.state.room = room if room is not None else GameRoom(hub_world=hub_world)
    application.state.clients = {}
    application.state.simulation = simulation
    application.state.development_tools = development_tools
    application.state.last_generation = float("-inf")
    application.state.loop_task = None
    application.middleware("http")(add_no_cache_headers)
    application.add_api_route("/api/config", get_config, methods=["GET"])
    application.add_api_route("/api/levels", get_levels, methods=["GET"])
    application.add_api_route("/api/levels/generate", generate_level, methods=["POST"])
    application.add_api_websocket_route("/ws", websocket_endpoint)
    application.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")
    application.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
    application.add_api_route("/", index, methods=["GET"])
    return application


app = create_app(hub_world=os.environ.get("SWARM_HUB_WORLD") or None)
