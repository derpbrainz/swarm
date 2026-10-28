"""Local browser test server with disposable copies of the legacy saves."""

import shutil
import json
import tempfile
from pathlib import Path

import uvicorn
from fastapi import Request, HTTPException
from backend.app.config.items import make_item
from backend.app.main import broadcast_snapshots

from backend.app.engine.game_loop import GameRoom
from backend.app.engine.inventory import SharedStash
from backend.app.engine.level_store import LevelStore
from backend.app.main import create_app
from backend.app.content import CATALOG, mutable_copy, validate_content
from backend.app.engine.props import instantiate_props
from backend.app.engine.puzzles import generate as generate_puzzle, runtime_puzzle
from backend.app.engine.doors import configure_threshold


if __name__ == "__main__":
    source = Path(__file__).resolve().parents[1] / "backend" / "app" / "data"
    with tempfile.TemporaryDirectory(prefix="swarm-browser-") as directory:
        directory = Path(directory)
        shutil.copyfile(source / "levels.json", directory / "levels.json")
        shutil.copyfile(source / "shared_stash.json", directory / "stash.json")
        room = GameRoom(LevelStore(directory / "levels.json"), SharedStash(directory / "stash.json"))
        app = create_app(room)

        @app.post("/__test__/fixture")
        async def fixture(request: Request):
            # This route exists only in this disposable loopback test executable.
            if request.client.host != "127.0.0.1":
                raise HTTPException(403)
            data = await request.json()
            action = data.get("action")
            if action == "enable_hub":
                room.hub_world = "flagship_stub"
                room.reset_to_lobby()
            elif action == "noncombat_active":
                room.phase = "active"
                room.map_data["world_kind"] = "mission"
                room.map_data["capabilities"]["objectives"] = True
                room.map_data["objectives"] = []
            elif action == "disable_hub":
                room.hub_world = None
                room.reset_to_lobby()
            elif action == "hub_mission":
                if room.map_data.get("world_kind") != "mission":
                    raise HTTPException(409)
                room.phase = "active"
                room.map_data.update(walls=[], props=[], enemies=[], loot=[], crates=[], puzzles=[],
                                     objective_definitions=[], objectives=[], spaceship=None,
                                     player_spawn_points=[(4, 4), (5, 4), (6, 4), (7, 4)],
                                     extraction_zone={"x": 10, "y": 10, "w": 4, "h": 4, "active": True})
                for p in room.players.values():
                    p.update(x=4.0, y=4.0)
                    p["input"].update(dx=0.0, dy=0.0, firing=False, running=False)
            elif action == "hub_defeat":
                for p in room.players.values():
                    room.damage(p, p["max_hp"] + 1, None)
                room.step(1 / 30)
            elif action == "restore_report":
                room.level_store.levels["outpost_101"].update(completed=False, tested=False, broken=False, failure_reports=[], status="uncompleted")
                room.selected_level_id = "outpost_101"
                room.selected_mission_id = "outpost"
            elif action in ("install", "install_visual", "install_encounter", "install_threshold", "install_report"):
                if not room.players:
                    raise HTTPException(409)
                if room.hub_world:
                    room.hub_world = None
                    room.reset_to_lobby()
                if room.phase != "lobby":
                    room.reset_to_lobby()
                room.selected_level_id = "outpost_101"
                room.selected_mission_id = "outpost"
                if action == "install_report":
                    room.selected_level_id = "outpost_101"
                    room.level_store.levels["outpost_101"].update(completed=False, tested=False, broken=False, failure_reports=[], status="uncompleted")
                room.start_mission(room.leader_id)
                room.phase = "active"
                room.map_data["walls"] = []
                room.map_data["enemies"] = []
                room.map_data["loot"] = []
                puzzle = {"id": "fixture-terminal", "label": "Fixture Terminal", "x": 12, "y": 10, "radius": 2.8,
                    "solved": False, "type": "power_reroute", "door_id": "fixture-door",
                    "data": {"type": "power_reroute", "nodes": [1, 1, 1, 1], "target": [0, 0, 0, 0]}}
                room.map_data["puzzles"] = [puzzle]
                room.map_data["crates"] = [
                    {"id": "fixture-near", "name": "Near Crate", "x": 10, "y": 10.5, "radius": 2.5, "locked": False, "items": []},
                    {"id": "fixture-chosen", "name": "Chosen Crate", "x": 10, "y": 12.5, "radius": 2.5, "locked": False,
                        "items": [make_item("credits")]},
                    {"id": "fixture-locked", "name": "Locked Crate", "x": 8, "y": 10, "radius": 2.5, "locked": True,
                        "items": [], "puzzle": {**puzzle, "id": "fixture-lock", "label": "Crate Lock", "data": {**puzzle["data"], "nodes": [1, 1, 1, 1]}}},
                ]
                room.map_data["extraction_zone"]["active"] = False
                for p in room.players.values():
                    p.update(x=10.0, y=10.0, vx=0.0, vy=0.0, running=False)
                    p["input"].update(dx=0.0, dy=0.0, firing=False, running=False)
                room.map_data["props"] = []
                for key in ("objectives", "objective_definitions"):
                    room.map_data.pop(key, None)
                if action == "install_threshold":
                    door = {"id": "fixture-proximity", "threshold_id": "automatic_door", "x": 12,
                            "y": 6, "w": .8, "h": 8, "active": True, "blocks_sight": True}
                    configure_threshold(door, room.catalog)
                    room.map_data.update(walls=[door], puzzles=[], crates=[], loot=[], props=[],
                                         objective_definitions=[], objectives=[], geometry_revision=0)
                    room.map_data["capabilities"].update(extraction=False, failure=False, ai=False)
                    for player in room.players.values():
                        player.update(x=8.0, y=10.0)
                elif action == "install_visual":
                    authored = json.loads((Path(__file__).parent / "fixtures" / "stage10-props.json").read_text())
                    content = mutable_copy(CATALOG.definitions)
                    content["props"]["console"]["visual_id"] = authored["console_visual"]
                    props, targets = instantiate_props(authored["placements"], catalog=validate_content(content))
                    room.map_data.update(props=props, puzzles=targets, crates=[], loot=[],
                                         segments=[{"index": 0, "name": "VISUAL FIXTURE", "theme": "hangar",
                                                     "x": 4, "y": 4, "w": 14, "h": 14, "material_id": authored["floor_material"]}])
                elif action == "install_encounter":
                    additions = json.loads((Path(__file__).parent / "fixtures" / "stage11-encounter.json").read_text())
                    content = mutable_copy(CATALOG.definitions)
                    for section, values in additions.items():
                        content[section].update(values)
                    room.catalog = validate_content(content)
                    target = {"id": "fixture-qte", "type": "fixture_timed", "label": "Timed Input Fixture", "x": 12, "y": 10,
                              "radius": 2.8, "solved": False, "door_id": None, "data": generate_puzzle("fixture_timed", catalog=room.catalog)}
                    runtime_puzzle(target, catalog=room.catalog)
                    room.map_data.update(puzzles=[target], crates=[], loot=[])
            elif action == "advance":
                # Opt-in fixture deterministic server simulation time, never a game command.
                room.step(float(data["seconds"]))
            elif action == "move":
                p = room.players[data["player_id"]]
                p.update(x=float(data["x"]), y=float(data["y"]))
            elif action == "barrier":
                room.map_data["walls"] = [{"id": "fixture-barrier", "x": 10.5, "y": 0, "w": .05, "h": 30, "active": True, "blocks_sight": True}]
            elif action == "delete":
                for collection in ("puzzles", "crates"):
                    room.map_data[collection] = [target for target in room.map_data[collection] if target["id"] != data["target_id"]]
            else:
                raise HTTPException(422)
            room.interaction_revision += 1
            await broadcast_snapshots(room, app.state.clients)
            return {"ok": True, "world_id": (room.map_data or {}).get("world_id")}

        uvicorn.run(app, host="127.0.0.1", port=8765)
