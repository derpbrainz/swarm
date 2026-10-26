"""Procedural segment stitcher that combines WFC segments into a mission map."""

from __future__ import annotations

from collections import deque
import math
import random

from backend.app.config.classes import ENEMY_TYPES
from backend.app.config.items import make_item
from backend.app.config.missions import CELL_SIZE, CONNECTOR_OFFSET, CONNECTOR_WIDTH, MISSIONS, SEGMENT_TEMPLATES
from backend.app.engine.physics import circle_vs_aabb, point_in_rect
from backend.app.engine.wfc import DIRS, run_wfc

WALL_THICKNESS = 0.8


def generate_puzzle_data(puzzle_type: str, seed_idx: int = 0) -> dict:
    rng = random.Random(100 + seed_idx * 37)
    if puzzle_type == "power_reroute":
        return {"type": puzzle_type, "nodes": [rng.choice([1, 2, 3]) for _ in range(4)], "target": [0, 0, 0, 0]}
    symbols = ["A7", "C4", "F9", "E2", "B5", "D8"]
    return {"type": puzzle_type, "target_sequence": [rng.choice(symbols) for _ in range(4)], "progress": 0, "options": symbols}


def build_wfc_map(mission_id: str, seed: int = 42) -> dict:
    wfc = run_wfc(mission_id, seed)
    mission = MISSIONS.get(mission_id, MISSIONS["outpost"])
    enemy_mult = float(mission.get("enemy_multiplier", 1.0))
    segments, walls, puzzles, enemies, loot, connectors_out = [], [], [], [], [], []
    connector_edges = set()
    for c in wfc["connectors"]:
        connector_edges.add((c["gx"], c["gy"], c["dir"]))
        connector_edges.add((c["nx"], c["ny"], c["opp_dir"]))

    for seg in wfc["segments"]:
        tpl = SEGMENT_TEMPLATES[seg["id"]]
        seg_obj = {
            "index": seg["index"],
            "id": seg["id"],
            "name": tpl["name"],
            "theme": tpl.get("theme", "industrial"),
            "x": seg["ax"] * CELL_SIZE,
            "y": seg["ay"] * CELL_SIZE,
            "w": tpl["grid_w"] * CELL_SIZE,
            "h": tpl["grid_h"] * CELL_SIZE,
            "grid_x": seg["ax"],
            "grid_y": seg["ay"],
            "grid_w": tpl["grid_w"],
            "grid_h": tpl["grid_h"],
            "cells": [list(c) for c in seg["cells"]],
        }
        segments.append(seg_obj)

    seg0 = segments[0]
    ship = {
        "x": seg0["x"] + 1.6,
        "y": seg0["y"] + 5.0,
        "w": 5.6,
        "h": 6.0,
        "ramp_open": False,
        "door_wall_id": "spaceship_ramp",
    }
    player_spawn_points = [
        (ship["x"] + 2.0, ship["y"] + 1.8),
        (ship["x"] + 2.0, ship["y"] + 4.2),
        (ship["x"] + 3.6, ship["y"] + 1.8),
        (ship["x"] + 3.6, ship["y"] + 4.2),
    ]
    walls.extend([
        {"id": "ship_hull_top", "x": ship["x"], "y": ship["y"], "w": ship["w"], "h": 0.5, "active": True, "blocks_sight": True, "type": "ship_hull"},
        {"id": "ship_hull_bottom", "x": ship["x"], "y": ship["y"] + ship["h"] - 0.5, "w": ship["w"], "h": 0.5, "active": True, "blocks_sight": True, "type": "ship_hull"},
        {"id": "ship_hull_rear", "x": ship["x"], "y": ship["y"], "w": 0.5, "h": ship["h"], "active": True, "blocks_sight": True, "type": "ship_hull"},
        {"id": "spaceship_ramp", "x": ship["x"] + ship["w"] - 0.5, "y": ship["y"] + 0.5, "w": 0.5, "h": ship["h"] - 1.0, "active": True, "blocks_sight": False, "type": "ship_ramp"},
    ])

    end_seg = segments[-1]
    extraction_zone = {"x": end_seg["x"] + 5.5, "y": end_seg["y"] + 5.0, "w": 5.0, "h": 6.0, "active": False}

    # Perimeter walls around all non-connector external cell edges of each segment
    for seg in segments:
        cells = {tuple(c) for c in seg["cells"]}
        for gx, gy in cells:
            for d, (dx, dy, opp) in DIRS.items():
                nb = (gx + dx, gy + dy)
                if nb in cells or (gx, gy, d) in connector_edges or (nb[0], nb[1], opp) in connector_edges:
                    continue
                x, y = gx * CELL_SIZE, gy * CELL_SIZE
                if d == "N":
                    walls.append({"id": f"wall_{gx}_{gy}_N", "x": x, "y": y, "w": CELL_SIZE, "h": WALL_THICKNESS, "active": True, "blocks_sight": True, "type": "wall"})
                elif d == "S":
                    walls.append({"id": f"wall_{gx}_{gy}_S", "x": x, "y": y + CELL_SIZE - WALL_THICKNESS, "w": CELL_SIZE, "h": WALL_THICKNESS, "active": True, "blocks_sight": True, "type": "wall"})
                elif d == "W":
                    walls.append({"id": f"wall_{gx}_{gy}_W", "x": x, "y": y, "w": WALL_THICKNESS, "h": CELL_SIZE, "active": True, "blocks_sight": True, "type": "wall"})
                else:
                    walls.append({"id": f"wall_{gx}_{gy}_E", "x": x + CELL_SIZE - WALL_THICKNESS, "y": y, "w": WALL_THICKNESS, "h": CELL_SIZE, "active": True, "blocks_sight": True, "type": "wall"})

    # Connectors (flat grid-aligned doorways) and upstream button/puzzle terminals
    used_slots = {seg["index"]: set() for seg in segments}
    for c_idx, c in enumerate(wfc["connectors"]):
        gx, gy, d = c["gx"], c["gy"], c["dir"]
        x, y = gx * CELL_SIZE, gy * CELL_SIZE
        door_id = f"door_{c_idx}"
        if d in ("E", "W"):
            bx = x + CELL_SIZE if d == "E" else x
            wall_x = bx - WALL_THICKNESS * 0.5
            door_center = (bx, y + 8.0)
            for t in (0.0, CONNECTOR_OFFSET + CONNECTOR_WIDTH):
                walls.append({"id": f"flank_{c_idx}_{int(t)}", "x": wall_x, "y": y + t, "w": WALL_THICKNESS, "h": CONNECTOR_OFFSET, "active": True, "blocks_sight": True, "type": "wall"})
            walls.append({"id": door_id, "x": bx - 0.4, "y": y + CONNECTOR_OFFSET, "w": 0.8, "h": CONNECTOR_WIDTH, "active": True, "blocks_sight": True, "type": "door"})
        else:
            by = y + CELL_SIZE if d == "S" else y
            wall_y = by - WALL_THICKNESS * 0.5
            door_center = (x + 8.0, by)
            for t in (0.0, CONNECTOR_OFFSET + CONNECTOR_WIDTH):
                walls.append({"id": f"flank_{c_idx}_{int(t)}", "x": x + t, "y": wall_y, "w": CONNECTOR_OFFSET, "h": WALL_THICKNESS, "active": True, "blocks_sight": True, "type": "wall"})
            walls.append({"id": door_id, "x": x + CONNECTOR_OFFSET, "y": by - 0.4, "w": CONNECTOR_WIDTH, "h": 0.8, "active": True, "blocks_sight": True, "type": "door"})

        from_seg = segments[c["from_seg"]]
        to_seg = segments[c["to_seg"]]
        tpl = SEGMENT_TEMPLATES[from_seg["id"]]
        slots = tpl.get("puzzle_slots", [])
        slot_idx = next((i for i in range(len(slots)) if i not in used_slots[from_seg["index"]]), None)
        if slot_idx is not None:
            used_slots[from_seg["index"]].add(slot_idx)
            slot = slots[slot_idx]
            px, py = from_seg["x"] + float(slot["x"]), from_seg["y"] + float(slot["y"])
            ptype = slot["type"]
            plabel = f"{slot['label']} ({from_seg['name']} -> {to_seg['name']})"
        else:
            dx, dy, _ = DIRS[d]
            px = door_center[0] - dx * 2.4
            py = door_center[1] - dy * 2.4
            ptype = "power_reroute" if c_idx % 2 == 0 else "signal_decrypt"
            plabel = f"Unlock {to_seg['name']} Bulkhead"

        puzzle_id = f"puzzle_{c_idx}"
        puzzles.append({
            "id": puzzle_id,
            "type": ptype,
            "label": plabel,
            "x": round(px, 2),
            "y": round(py, 2),
            "radius": 2.8,
            "solved": False,
            "door_id": door_id,
            "segment_index": from_seg["index"],
            "target_segment_index": to_seg["index"],
            "data": generate_puzzle_data(ptype, c_idx + wfc["seed"]),
        })
        connectors_out.append({
            "id": f"conn_{c_idx}",
            "door_id": door_id,
            "puzzle_id": puzzle_id,
            "from_segment": from_seg["index"],
            "to_segment": to_seg["index"],
            "gx": gx,
            "gy": gy,
            "dir": d,
            "x": door_center[0],
            "y": door_center[1],
        })

    # Interior walls, enemies, loot, and skill-check supply crates per segment
    enemy_counter = 1
    loot_counter = 1
    crates: list[dict] = []
    crate_loot_pools = [
        ["plasma_lmg", "medkit", "ammo_pack"],
        ["rail_carbine", "tactical_light", "stim_injector"],
        ["portable_turret", "machine_pistol", "heavy_ammo"],
        ["flare_revolver", "sonar_beacon", "medkit"],
        ["tactical_light", "portable_turret", "isotope_cell"],
    ]
    ground_variety = ["medkit", "ammo_pack", "tactical_light", "portable_turret", "sonar_beacon", "stim_injector", "machine_pistol", "credits"]

    for seg in segments:
        idx = seg["index"]
        tpl = SEGMENT_TEMPLATES[seg["id"]]
        for w_idx, iw in enumerate(tpl.get("interior_walls", [])):
            walls.append({
                "id": f"wall_{idx}_int_{w_idx}",
                "x": seg["x"] + float(iw["x"]),
                "y": seg["y"] + float(iw["y"]),
                "w": float(iw["w"]),
                "h": float(iw["h"]),
                "active": True,
                "blocks_sight": bool(iw.get("blocks_sight", True)),
                "type": "wall" if iw.get("blocks_sight", True) else "cover",
            })

        for sp in tpl.get("enemy_spawns", []):
            etype = sp["type"]
            e_cfg = ENEMY_TYPES[etype]
            sx = seg["x"] + float(sp["x"])
            sy = seg["y"] + float(sp["y"])
            pdx = float(sp.get("patrol_dx", 0.0))
            pdy = float(sp.get("patrol_dy", 0.0))
            enemies.append({
                "id": f"enemy_{enemy_counter}",
                "type": etype,
                "name": e_cfg["name"],
                "color": e_cfg["color"],
                "x": sx,
                "y": sy,
                "vx": 0.0,
                "vy": 0.0,
                "angle": math.pi,
                "hp": int(round(e_cfg["max_hp"] * enemy_mult)),
                "max_hp": int(round(e_cfg["max_hp"] * enemy_mult)),
                "radius": e_cfg["radius"],
                "state": "patrol",
                "waypoints": [(sx, sy), (sx + pdx, sy + pdy)],
                "waypoint_index": 1,
                "investigate_target": None,
                "alert_timer": 0.0,
                "target_player_id": None,
                "attack_timer": 0.0,
                "segment_index": idx,
            })
            enemy_counter += 1

        for lt_idx, lt in enumerate(tpl.get("loot_spawns", [])):
            ltype = lt["type"] if lt["type"] != "credits" or lt_idx == 0 else ground_variety[(idx + lt_idx) % len(ground_variety)]
            item_obj = make_item(ltype, f"loot_{loot_counter}")
            item_obj.update({
                "x": seg["x"] + float(lt["x"]),
                "y": seg["y"] + float(lt["y"]),
                "radius": 2.0,
                "collected": False,
                "segment_index": idx,
            })
            loot.append(item_obj)
            loot_counter += 1

        # Add an interactive supply crate (locked with a skill-check puzzle) in segments
        if seg["id"] != "extraction_pad":
            cx = seg["x"] + (10.0 if seg["id"] == "drop_hangar" else 3.4)
            cy = seg["y"] + (8.0 if seg["id"] == "drop_hangar" else seg["h"] - 3.2)
            pool = crate_loot_pools[(idx + wfc["seed"]) % len(crate_loot_pools)]
            crate_id = f"crate_{idx}"
            crates.append({
                "id": crate_id,
                "type": "supply_crate",
                "name": f"{seg['name']} Supply Crate",
                "x": round(cx, 2),
                "y": round(cy, 2),
                "radius": 2.4,
                "locked": idx > 0,  # Hangar crate unlocked for immediate testing; deeper crates have skill-check puzzle
                "puzzle": {
                    "id": f"crate_pz_{idx}",
                    "type": "power_reroute" if idx % 2 == 0 else "signal_decrypt",
                    "label": f"Skill Check: Unlock {seg['name']} Supply Crate",
                    "crate_id": crate_id,
                    "solved": idx == 0,
                    "data": generate_puzzle_data("power_reroute" if idx % 2 == 0 else "signal_decrypt", 50 + idx),
                },
                "items": [make_item(tid, f"crate_{idx}_item_{i}") for i, tid in enumerate(pool)],
                "segment_index": idx,
            })

    return {
        "mission_id": mission["id"],
        "mission_name": mission["name"],
        "seed": wfc["seed"],
        "grid_w": wfc["grid_w"],
        "grid_h": wfc["grid_h"],
        "width": wfc["grid_w"] * CELL_SIZE,
        "height": wfc["grid_h"] * CELL_SIZE,
        "segments": segments,
        "connectors": connectors_out,
        "walls": walls,
        "puzzles": puzzles,
        "enemies": enemies,
        "loot": loot,
        "crates": crates,
        "spaceship": ship,
        "player_spawn_points": player_spawn_points,
        "extraction_zone": extraction_zone,
        "test_mode": False,
    }


def verify_level_solvability(map_data: dict) -> bool:
    """
    Verifies via 2D flood-fill that:
    1. Every puzzle/button is accessible upstream of the door it opens.
    2. Unlocking each door in sequence allows reaching the extraction zone from player spawn.
    3. Players remain enclosed within the segments.
    """
    segments = map_data["segments"]
    unlocked_doors: set[str] = {"spaceship_ramp"}
    solved_puzzles: set[str] = set()
    spawn_x, spawn_y = map_data["player_spawn_points"][0]
    start_node = (round(spawn_x * 2) / 2.0, round(spawn_y * 2) / 2.0)

    def is_walkable(px: float, py: float) -> bool:
        if not any(point_in_rect(px, py, s) for s in segments):
            return False
        for w in map_data["walls"]:
            if not w.get("active", True) or w["id"] in unlocked_doors:
                continue
            if circle_vs_aabb(px, py, 0.32, w)[2]:
                return False
        return True

    while True:
        visited = {start_node}
        q = deque([start_node])
        while q:
            cx, cy = q.popleft()
            for dx, dy in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) not in visited and is_walkable(nx, ny):
                    visited.add((nx, ny))
                    q.append((nx, ny))

        progressed = False
        for pz in map_data["puzzles"]:
            if pz["id"] in solved_puzzles:
                continue
            # Check if any visited upstream walkable position can interact with this puzzle
            if any(math.hypot(vx - pz["x"], vy - pz["y"]) <= pz["radius"] for vx, vy in visited):
                solved_puzzles.add(pz["id"])
                unlocked_doors.add(pz["door_id"])
                progressed = True

        if not progressed:
            break

    if len(solved_puzzles) < len(map_data["puzzles"]):
        return False

    ext = map_data["extraction_zone"]
    ext_cx = ext["x"] + ext["w"] * 0.5
    ext_cy = ext["y"] + ext["h"] * 0.5
    return any(math.hypot(vx - ext_cx, vy - ext_cy) <= 1.5 for vx, vy in visited)


def build_mission_map(mission_id: str, seed: int = 42, test_mode: bool = False) -> dict:
    """Builds a verified WFC level map for a mission."""
    for attempt in range(25):
        candidate = build_wfc_map(mission_id, seed + attempt * 31)
        if verify_level_solvability(candidate):
            if test_mode:
                candidate["test_mode"] = True
                candidate["enemies"] = []
                candidate["loot"] = []
                candidate["crates"] = []
            return candidate
    raise RuntimeError(f"Could not generate solvable WFC map for {mission_id}")