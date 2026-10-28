"""Procedural segment stitcher that combines WFC segments into a mission map."""

from __future__ import annotations

from collections import deque
import heapq
import math
import random

from backend.app.config.classes import ENEMY_TYPES
from backend.app.config.items import make_item
from backend.app.content import CATALOG, RULES, mutable_copy
from backend.app.config.missions import CELL_SIZE, CONNECTOR_OFFSET, CONNECTOR_WIDTH, MISSIONS, SEGMENT_TEMPLATES
from backend.app.engine.physics import collision_path_clear, point_in_rect
from backend.app.engine.props import instantiate_props, world_surfaces
from backend.app.engine.doors import configure_threshold, distance_to_door
from backend.app.engine.puzzles import generate as generate_puzzle
from backend.app.engine.wfc import DIRS, GENERATOR_VERSION, MAX_ATTEMPTS, GenerationError, _try_seed, run_wfc

WALL_THICKNESS = RULES["geometry"]["wall_thickness"]

# Distinct high-contrast colors matching doors to their unlocking security consoles
DOOR_PALETTE = [
    {"name": "Amber", "color": "#f59e0b", "glow": "#fbbf24"},
    {"name": "Cyan", "color": "#06b6d4", "glow": "#22d3ee"},
    {"name": "Magenta", "color": "#ec4899", "glow": "#f472b6"},
    {"name": "Emerald", "color": "#10b981", "glow": "#34d399"},
    {"name": "Violet", "color": "#8b5cf6", "glow": "#a78bfa"},
    {"name": "Coral", "color": "#f97316", "glow": "#fb923c"},
    {"name": "Lime", "color": "#84cc16", "glow": "#a3e635"},
]


def astar_wire_path(start: tuple[float, float], goal: tuple[float, float], segments: list[dict], walls: list[dict], ignore_wall_ids: set[str]) -> list[tuple[float, float]]:
    res = 0.5
    sx, sy = round(start[0] / res) * res, round(start[1] / res) * res

    blocking = [w for w in walls if w.get("active", True) and w.get("blocks_movement", True) and w.get("id") not in ignore_wall_ids]

    def is_blocked(x: float, y: float) -> bool:
        if not any(point_in_rect(x, y, s) for s in segments):
            return True
        return not collision_path_clear((x, y), (x, y), 0.22, blocking)

    if is_blocked(sx, sy) or not collision_path_clear(start, (sx, sy), 0.22, blocking):
        raise ValueError(f"Wire start blocked at {start}")

    open_set = []
    heapq.heappush(open_set, (0.0, 0.0, (sx, sy)))
    came_from = {}
    g_score = {(sx, sy): 0.0}

    while open_set:
        _, current_g, current = heapq.heappop(open_set)
        if math.hypot(current[0] - goal[0], current[1] - goal[1]) <= res * 2 and collision_path_clear(current, goal, 0.22, blocking):
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            path.insert(0, start)
            path.append(goal)
            return path

        for dx, dy in [(res, 0), (-res, 0), (0, res), (0, -res), (res, res), (-res, res), (res, -res), (-res, -res)]:
            nx, ny = round((current[0] + dx) / res) * res, round((current[1] + dy) / res) * res
            if is_blocked(nx, ny) or not collision_path_clear(current, (nx, ny), 0.22, blocking):
                continue
            step_cost = math.hypot(dx, dy)
            tentative_g = current_g + step_cost
            if tentative_g < g_score.get((nx, ny), float("inf")):
                came_from[(nx, ny)] = current
                g_score[(nx, ny)] = tentative_g
                h = math.hypot(nx - goal[0], ny - goal[1])
                heapq.heappush(open_set, (tentative_g + h, tentative_g, (nx, ny)))

    raise ValueError(f"No collision-free wire route from {start} to {goal}")


def generate_puzzle_data(puzzle_type: str, seed_idx: int = 0, *, rng: random.Random | None = None, catalog=None) -> dict:
    rng = rng if rng is not None else random.Random(100 + seed_idx * 37)
    return generate_puzzle(puzzle_type, rng=rng, catalog=catalog if catalog is not None else CATALOG)


def build_wfc_map(mission_id: str, seed: int = 42, *, layout: dict | None = None) -> dict:
    wfc = layout if layout is not None else run_wfc(mission_id, seed)
    rng = random.Random(wfc["seed"])
    mission = MISSIONS[mission_id]
    threshold_ids = sorted(mission["threshold_weights"])
    threshold_rng = random.Random(f"{wfc['seed']}:thresholds")
    threshold_choices = threshold_rng.choices(threshold_ids, weights=[mission["threshold_weights"][t] for t in threshold_ids], k=len(wfc["connectors"]))
    enemy_mult = float(mission["enemy_multiplier"])
    segments, walls, puzzles, enemies, loot, connectors_out = [], [], [], [], [], []
    ambience_emitters = []
    connector_edges = set()
    for c in wfc["connectors"]:
        connector_edges.add((c["gx"], c["gy"], c["dir"]))
        connector_edges.add((c["nx"], c["ny"], c["opp_dir"]))

    for seg in wfc["segments"]:
        tpl = SEGMENT_TEMPLATES[seg["id"]]
        seg_obj = {
            "index": seg["index"],
            "id": seg["id"],
            "role": tpl["role"],
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
        for emitter in tpl["ambience_emitters"]:
            ambience_emitters.append({**dict(emitter), "id": f"ambience_{seg['index']}_{emitter['id']}",
                                     "x": seg_obj["x"] + emitter["x"], "y": seg_obj["y"] + emitter["y"]})

    seg0 = segments[wfc["start_segment_index"]]
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

    end_seg = segments[wfc["end_segment_index"]]
    extraction_zone = {"x": end_seg["x"] + 5.5, "y": end_seg["y"] + 5.0, "w": 5.0, "h": 6.0, "active": False}

    # Perimeter walls around all non-connector external cell edges of each segment
    for seg in segments:
        cells = {tuple(c) for c in seg["cells"]}
        for gx, gy in sorted(cells):
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

    # All blocking interiors/cover exist before any decoration is routed.
    for seg in segments:
        for w_idx, iw in enumerate(SEGMENT_TEMPLATES[seg["id"]]["interior_walls"]):
            wall = {"id": f"wall_{seg['index']}_int_{w_idx}",
                          "x": seg["x"] + iw["x"], "y": seg["y"] + iw["y"],
                          "w": iw["w"], "h": iw["h"],
                          "blocks_sight": iw["blocks_sight"],
                          "is_door": iw["is_door"], "material": iw["material"], "active": iw["active"],
                          "type": "wall" if iw["blocks_sight"] else "cover"}
            if "threshold_id" in iw:
                wall["threshold_id"] = iw["threshold_id"]
                configure_threshold(wall, CATALOG)
            walls.append(wall)

    # Every connector retains jambs; only puzzle policies instantiate a terminal/wire.
    used_slots = {seg["index"]: set() for seg in segments}
    for c_idx, c in enumerate(wfc["connectors"]):
        gx, gy, d = c["gx"], c["gy"], c["dir"]
        x, y = gx * CELL_SIZE, gy * CELL_SIZE
        door_id = f"door_{c_idx}"
        threshold_id = threshold_choices[c_idx]
        policy = CATALOG.definitions["thresholds"][threshold_id]
        door_color_info = DOOR_PALETTE[c_idx % len(DOOR_PALETTE)]
        if d in ("E", "W"):
            bx = x + CELL_SIZE if d == "E" else x
            wall_x = bx - WALL_THICKNESS * 0.5
            door_center = (bx, y + CELL_SIZE * 0.5)
            for t in (0.0, CONNECTOR_OFFSET + CONNECTOR_WIDTH):
                walls.append({"id": f"flank_{c_idx}_{int(t)}", "x": wall_x, "y": y + t, "w": WALL_THICKNESS, "h": CONNECTOR_OFFSET, "active": True, "blocks_sight": True, "type": "wall"})
            door_rect = {"x": wall_x, "y": y + CONNECTOR_OFFSET, "w": WALL_THICKNESS, "h": CONNECTOR_WIDTH}
        else:
            by = y + CELL_SIZE if d == "S" else y
            wall_y = by - WALL_THICKNESS * 0.5
            door_center = (x + CELL_SIZE * 0.5, by)
            for t in (0.0, CONNECTOR_OFFSET + CONNECTOR_WIDTH):
                walls.append({"id": f"flank_{c_idx}_{int(t)}", "x": x + t, "y": wall_y, "w": CONNECTOR_OFFSET, "h": WALL_THICKNESS, "active": True, "blocks_sight": True, "type": "wall"})
            door_rect = {"x": x + CONNECTOR_OFFSET, "y": wall_y, "w": CONNECTOR_WIDTH, "h": WALL_THICKNESS}

        from_seg = segments[c["from_seg"]]
        to_seg = segments[c["to_seg"]]
        connector = {"id": f"conn_{c_idx}", "threshold_id": threshold_id, "mode": policy["mode"],
                     "from_segment": from_seg["index"], "to_segment": to_seg["index"],
                     "gx": gx, "gy": gy, "dir": d, "x": door_center[0], "y": door_center[1]}
        connectors_out.append(connector)
        if policy["mode"] == "open":
            continue
        door = {**door_rect, "id": door_id, "threshold_id": threshold_id,
                "active": True, "blocks_movement": True, "blocks_projectile": True, "blocks_sight": True,
                "type": policy["door_type"], "color": door_color_info["color"], "color_name": door_color_info["name"]}
        configure_threshold(door, CATALOG)
        walls.append(door)
        connector["door_id"] = door_id
        if policy["mode"] == "proximity":
            continue

        tpl = SEGMENT_TEMPLATES[from_seg["id"]]
        slots = tpl.get("puzzle_slots", [])
        slot_idx = next((i for i in range(len(slots)) if i not in used_slots[from_seg["index"]]), None)
        if slot_idx is not None:
            used_slots[from_seg["index"]].add(slot_idx)
            slot = slots[slot_idx]
            px, py = from_seg["x"] + float(slot["x"]), from_seg["y"] + float(slot["y"])
            plabel = f"{slot['label']} ({from_seg['name']} -> {to_seg['name']})"
        else:
            dx, dy, _ = DIRS[d]
            px = door_center[0] - dx * 2.4
            py = door_center[1] - dy * 2.4
            plabel = f"Unlock {to_seg['name']} Bulkhead"

        puzzle_id = f"puzzle_{c_idx}"
        ptype = policy["puzzle_id"]
        puzzles.append({
            "id": puzzle_id,
            "type": ptype,
            "definition_id": ptype,
            "definition": mutable_copy(CATALOG.definitions["puzzles"][ptype]),
            "label": plabel,
            "x": round(px, 2),
            "y": round(py, 2),
            "radius": RULES["interaction"]["puzzle_radius"],
            "solved": False,
            "door_id": door_id,
            "color": door_color_info["color"],
            "color_name": door_color_info["name"],
            "segment_index": from_seg["index"],
            "target_segment_index": to_seg["index"],
            "data": generate_puzzle_data(ptype, rng=rng),
        })
        connector["puzzle_id"] = puzzle_id


    props, prop_puzzles = [], []
    for seg in segments:
        instances, targets = instantiate_props(SEGMENT_TEMPLATES[seg["id"]]["props"],
                                              x=seg["x"], y=seg["y"], prefix=f"prop_{seg['index']}_")
        props.extend(instances)
        prop_puzzles.extend(targets)
    routing_surfaces = world_surfaces({"walls": walls, "props": props})

    # Compute trailing floor wires from puzzle console to the side of the door using A*
    wires = []
    for pz in puzzles:
        d_id = pz.get("door_id")
        door_wall = next((w for w in walls if w.get("id") == d_id), None)
        if door_wall:
            ignore_ids = {door_wall["id"]}
            # Determine attachment point at the side/jamb of the door frame
            if door_wall["w"] > door_wall["h"]:
                door_side = (door_wall["x"] + 0.35, door_wall["y"] + door_wall["h"] * 0.5)
            else:
                door_side = (door_wall["x"] + door_wall["w"] * 0.5, door_wall["y"] + 0.35)
            path = astar_wire_path((pz["x"], pz["y"]), door_side, segments, routing_surfaces, ignore_ids)
            wires.append({
                "puzzle_id": pz["id"],
                "door_id": d_id,
                "color": pz.get("color", "#f59e0b"),
                "path": path,
            })

    # Enemies, authored loot, and skill-check supply crates per segment.
    enemy_counter = 1
    loot_counter = 1
    crates: list[dict] = []
    crate_loot_pools = CATALOG.definitions["loot_tables"]["supply_crates"]
    ground_variety = CATALOG.definitions["loot_tables"]["ground_variety"]

    for seg in segments:
        idx = seg["index"]
        tpl = SEGMENT_TEMPLATES[seg["id"]]
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

        available_loot = tpl.get("loot_spawns", [])
        if available_loot:
            loot_count_in_seg = tpl["loot_count"]
            for lt_idx in range(min(loot_count_in_seg, len(available_loot))):
                chosen_idx = (idx + lt_idx) % len(available_loot)
                lt = available_loot[chosen_idx]
                ltype = rng.choice(ground_variety) if lt["variety"] else lt["type"]
                item_obj = make_item(ltype, f"loot_{loot_counter}")
                item_obj.update({
                    "x": seg["x"] + float(lt["x"]),
                    "y": seg["y"] + float(lt["y"]),
                    "radius": RULES["interaction"]["ground_item_radius"],
                    "collected": False,
                    "segment_index": idx,
                })
                loot.append(item_obj)
                loot_counter += 1

        # Add an interactive supply crate (locked with a skill-check puzzle) in segments
        if tpl.get("crate_spawn") is not None:
            spawn = tpl["crate_spawn"]
            cx, cy = seg["x"] + spawn["x"], seg["y"] + spawn["y"]
            pool = rng.choice(crate_loot_pools)
            crate_id = f"crate_{idx}"
            crates.append({
                "id": crate_id,
                "type": "supply_crate",
                "name": f"{seg['name']} Supply Crate",
                "x": round(cx, 2),
                "y": round(cy, 2),
                "radius": RULES["interaction"]["crate_radius"],
                "locked": spawn["locked"],
                "puzzle": {
                    "id": f"crate_pz_{idx}",
                    "type": "power_reroute" if idx % 2 == 0 else "signal_decrypt",
                    "definition_id": "power_reroute" if idx % 2 == 0 else "signal_decrypt",
                    "definition": mutable_copy(CATALOG.definitions["puzzles"]["power_reroute" if idx % 2 == 0 else "signal_decrypt"]),
                    "label": f"Skill Check: Unlock {seg['name']} Supply Crate",
                    "crate_id": crate_id,
                    "solved": not spawn["locked"],
                    "data": generate_puzzle_data("power_reroute" if idx % 2 == 0 else "signal_decrypt", rng=rng),
                },
                "items": [make_item(tid, f"crate_{idx}_item_{i}") for i, tid in enumerate(pool)],
                "segment_index": idx,
            })

    for wall in walls:
        wall.setdefault("is_door", wall["type"] in ("door", "ship_ramp"))
        wall.setdefault("material", "door" if wall["is_door"] else "solid")

    return {
        "mission_id": mission["id"],
        "mission_name": mission["name"],
        "seed": wfc["seed"],
        "requested_seed": wfc.get("requested_seed", seed),
        "resolved_seed": wfc["seed"],
        "generator_version": GENERATOR_VERSION,
        "content_version": CATALOG.definitions["content_version"],
        "start_segment_index": wfc["start_segment_index"],
        "end_segment_index": wfc["end_segment_index"],
        "grid_w": wfc["grid_w"],
        "grid_h": wfc["grid_h"],
        "width": wfc["grid_w"] * CELL_SIZE,
        "height": wfc["grid_h"] * CELL_SIZE,
        "segments": segments,
        "connectors": connectors_out,
        "walls": walls,
        "ambience_emitters": ambience_emitters,
        "props": props,
        "puzzles": puzzles + prop_puzzles,
        "wires": wires,
        "enemies": enemies,
        "loot": loot,
        "crates": crates,
        "spaceship": ship,
        "player_spawn_points": player_spawn_points,
        "extraction_zone": extraction_zone,
    }


def validate_generated_map(map_data: dict) -> None:
    """Strict generation gate, not an automatic legacy-save migration or test pass."""
    segments, walls = map_data["segments"], world_surfaces(map_data)
    radius = RULES["player"]["radius"]
    if not segments or not walls or not map_data["player_spawn_points"]:
        raise ValueError("Missing segments, collision enclosure, or player spawns")
    mission = MISSIONS[map_data["mission_id"]]
    if not mission["min_segments"] <= len(segments) <= mission["max_segments"]:
        raise ValueError("Final segment count outside mission bounds")
    by_index = {s["index"]: s for s in segments}
    if len(by_index) != len(segments) or set(by_index) != set(range(len(segments))):
        raise ValueError("Invalid segment indices")
    for role, field in (("start", "start_segment_index"), ("end", "end_segment_index")):
        matches = [s["index"] for s in segments if s.get("role") == role]
        if matches != [map_data.get(field)]:
            raise ValueError(f"Requires exactly one explicit {role} segment")
    if not set(mission["required_templates"]).issubset(s["id"] for s in segments):
        raise ValueError("Missing required mission templates")
    cells = set()
    for s in segments:
        if s["id"] not in mission["allowed_templates"] or s.get("role") != SEGMENT_TEMPLATES[s["id"]]["role"]:
            raise ValueError("Invalid template or role")
        expected = {(x, y) for x in range(s["grid_x"], s["grid_x"] + s["grid_w"])
                    for y in range(s["grid_y"], s["grid_y"] + s["grid_h"])}
        tpl = SEGMENT_TEMPLATES[s["id"]]
        if ((s["grid_w"], s["grid_h"]) != (tpl["grid_w"], tpl["grid_h"]) or
                (s["x"], s["y"], s["w"], s["h"]) !=
                (s["grid_x"] * CELL_SIZE, s["grid_y"] * CELL_SIZE, tpl["width"], tpl["height"]) or
                expected != {tuple(c) for c in s["cells"]} or cells & expected or
                any(not (0 <= x < mission["grid_w"] and 0 <= y < mission["grid_h"]) for x, y in expected)):
            raise ValueError("Invalid/overlapping segment footprint")
        cells.update(expected)
    wall_by_id = {w["id"]: w for w in walls}
    if len(wall_by_id) != len(walls):
        raise ValueError("Duplicate wall IDs")
    for w in walls:
        if any(not math.isfinite(w[k]) for k in ("x", "y", "w", "h")) or w["w"] <= 0 or w["h"] <= 0:
            raise ValueError("Invalid collision rectangle")
    all_puzzles = map_data["puzzles"] + [c["puzzle"] for c in map_data.get("crates", []) if c.get("puzzle")]
    if len({p["id"] for p in all_puzzles}) != len(all_puzzles):
        raise ValueError("Duplicate puzzle IDs")
    puzzles = [p for p in map_data["puzzles"] if not p.get("prop_id")]
    puzzle_by_id = {p["id"]: p for p in puzzles}
    if len(puzzle_by_id) != len(puzzles):
        raise ValueError("Duplicate puzzle IDs")
    puzzle_doors = {p["door_id"] for p in puzzles}
    if len(puzzle_doors) != len(puzzles) or any(wall_by_id.get(d, {}).get("type") != "door" for d in puzzle_doors):
        raise ValueError("Invalid puzzle door references")
    threshold_doors = set()
    proximity_doors = []
    for wall in walls:
        if "threshold_id" not in wall:
            if "mode" in wall or wall.get("type") == "door":
                raise ValueError("Door mode requires threshold policy reference")
            continue
        policy = CATALOG.definitions["thresholds"].get(wall["threshold_id"])
        if not policy or policy["mode"] == "open" or any(wall.get(k) != v for k, v in policy.items() if k != "puzzle_id"):
            raise ValueError("Invalid door threshold policy")
        if (wall.get("type") != policy["door_type"] or wall.get("is_door") is not True or wall.get("active") is not True or
                any(wall.get(flag) is not True for flag in ("blocks_movement", "blocks_projectile", "blocks_sight"))):
            raise ValueError("Threshold door must initially block movement and sight")
        threshold_doors.add(wall["id"])
        if policy["mode"] == "proximity":
            proximity_doors.append(wall)
    ramp_id = map_data["spaceship"]["door_wall_id"]
    if ramp_id not in wall_by_id:
        raise ValueError("Missing dropship ramp")
    opened = {ramp_id}
    all_open = threshold_doors | opened
    permanent = [w for w in walls if w.get("active", True) and w["id"] not in all_open]
    adj = {i: set() for i in by_index}
    connectors = map_data["connectors"]
    if len({c["id"] for c in connectors}) != len(connectors):
        raise ValueError("Duplicate connector IDs")
    used_doors, used_puzzles, used_ports = set(), set(), set()
    for c in connectors:
        policy = CATALOG.definitions["thresholds"].get(c.get("threshold_id"))
        if not policy or c.get("mode") != policy["mode"] or mission["threshold_weights"].get(c["threshold_id"], 0) <= 0:
            raise ValueError("Invalid connector threshold policy")
        u, v = c["from_segment"], c["to_segment"]
        if u not in by_index or v not in by_index or u == v or c["dir"] not in DIRS:
            raise ValueError("Invalid connector segment/direction")
        dx, dy, opposite = DIRS[c["dir"]]
        a, b = by_index[u], by_index[v]
        pa = (c["gx"] - a["grid_x"], c["gy"] - a["grid_y"], c["dir"])
        pb = (c["gx"] + dx - b["grid_x"], c["gy"] + dy - b["grid_y"], opposite)
        def ports(s):
            return {(p["cell_x"], p["cell_y"], p["dir"]) for p in SEGMENT_TEMPLATES[s["id"]]["connector_ports"]}
        endpoints = {(u, *pa), (v, *pb)}
        if pa not in ports(a) or pb not in ports(b) or used_ports & endpoints:
            raise ValueError("Unmatched or duplicate connector ports")
        used_ports.update(endpoints)
        if policy["mode"] == "open":
            if "door_id" in c or "puzzle_id" in c:
                raise ValueError("Open passage forbids door/puzzle references")
        else:
            door = wall_by_id.get(c.get("door_id"), {})
            if door.get("threshold_id") != c["threshold_id"] or c.get("door_id") not in threshold_doors or c["door_id"] in used_doors:
                raise ValueError("Unmatched connector door policy")
            used_doors.add(c["door_id"])
            if policy["mode"] == "proximity":
                if "puzzle_id" in c:
                    raise ValueError("Proximity connector forbids puzzle reference")
            else:
                p = puzzle_by_id.get(c.get("puzzle_id"), {})
                if (p.get("door_id") != c["door_id"] or p.get("definition_id") != policy["puzzle_id"] or
                        p.get("type") != policy["puzzle_id"] or p.get("definition") != mutable_copy(CATALOG.definitions["puzzles"][policy["puzzle_id"]]) or
                        p.get("segment_index") != u or p.get("target_segment_index") != v or c.get("puzzle_id") in used_puzzles):
                    raise ValueError("Unmatched connector puzzle policy")
                used_puzzles.add(c["puzzle_id"])
        center = ((c["gx"] + (1 if dx == 1 else 0.5 if dx == 0 else 0)) * CELL_SIZE,
                  (c["gy"] + (1 if dy == 1 else 0.5 if dy == 0 else 0)) * CELL_SIZE)
        expected_door = ({"x": center[0] - WALL_THICKNESS / 2, "y": center[1] - CONNECTOR_WIDTH / 2, "w": WALL_THICKNESS, "h": CONNECTOR_WIDTH}
                         if dx else {"x": center[0] - CONNECTOR_WIDTH / 2, "y": center[1] - WALL_THICKNESS / 2, "w": CONNECTOR_WIDTH, "h": WALL_THICKNESS})
        if ((c["x"], c["y"]) != center or (policy["mode"] != "open" and any(door[k] != v for k, v in expected_door.items())) or
                not collision_path_clear((center[0] - dx * 2, center[1] - dy * 2),
                                         (center[0] + dx * 2, center[1] + dy * 2), radius, permanent)):
            raise ValueError("Blocked or misplaced connector")
        # An open threshold still needs both solid jambs, not an entire open cell edge.
        for offset in (-CELL_SIZE / 2, CONNECTOR_WIDTH / 2):
            jamb = ({"x": center[0] - WALL_THICKNESS / 2, "y": center[1] + offset, "w": WALL_THICKNESS, "h": CONNECTOR_OFFSET}
                    if dx else {"x": center[0] + offset, "y": center[1] - WALL_THICKNESS / 2, "w": CONNECTOR_OFFSET, "h": WALL_THICKNESS})
            if not any(all(w.get(k) == v for k, v in jamb.items()) and w.get("active", True) and
                       w.get("blocks_movement", True) and w.get("blocks_sight", True) and not w.get("is_door", False) for w in permanent):
                raise ValueError("Missing connector jamb enclosure")
        adj[u].add(v)
        adj[v].add(u)
    if used_puzzles != set(puzzle_by_id) or used_doors & puzzle_doors != puzzle_doors:
        raise ValueError("Unmatched puzzle/connector references")
    if threshold_doors - used_doors != {w["id"] for w in proximity_doors if w["id"] not in used_doors}:
        raise ValueError("Puzzle door requires a connector")
    connected, queue = {map_data["start_segment_index"]}, deque([map_data["start_segment_index"]])
    while queue:
        for v in sorted(adj[queue.popleft()] - connected):
            connected.add(v)
            queue.append(v)
    if len(connected) != len(segments):
        raise ValueError("Disconnected final segment graph")

    # Exact interval coverage on every exterior cell edge detects even sub-grid holes.
    for gx, gy in sorted(cells):
        for d, (dx, dy, _) in DIRS.items():
            if (gx + dx, gy + dy) in cells:
                continue
            vertical = d in ("E", "W")
            boundary = (gx + (d == "E")) * CELL_SIZE if vertical else (gy + (d == "S")) * CELL_SIZE
            low = (gy if vertical else gx) * CELL_SIZE
            intervals = []
            for w in permanent:
                normal, extent = (w["x"], w["w"]) if vertical else (w["y"], w["h"])
                if normal <= boundary <= normal + extent:
                    tangent, length = (w["y"], w["h"]) if vertical else (w["x"], w["w"])
                    intervals.append((tangent, tangent + length))
            cursor = low
            for begin, end in sorted(intervals):
                if begin > cursor + 1e-8:
                    break
                cursor = max(cursor, end)
            if cursor < low + CELL_SIZE:
                raise ValueError(f"Unenclosed exterior edge {gx},{gy},{d}")

    # A small spatial index keeps swept navigation independent of wall-list size.
    buckets = {}
    for w in walls:
        if not w.get("active", True):
            continue
        for bx in range(math.floor((w["x"] - radius) / 4), math.floor((w["x"] + w["w"] + radius) / 4) + 1):
            for by in range(math.floor((w["y"] - radius) / 4), math.floor((w["y"] + w["h"] + radius) / 4) + 1):
                buckets.setdefault((bx, by), []).append(w)

    def blockers(a, b):
        near = {}
        for bx in range(math.floor(min(a[0], b[0]) / 4), math.floor(max(a[0], b[0]) / 4) + 1):
            for by in range(math.floor(min(a[1], b[1]) / 4), math.floor(max(a[1], b[1]) / 4) + 1):
                for w in buckets.get((bx, by), ()):
                    near[w["id"]] = w
        return [w for w in near.values() if w["id"] not in opened]

    def inside(p):
        return any(point_in_rect(*p, s) for s in segments)

    def clear(a, b):
        return collision_path_clear(a, b, radius, blockers(a, b))

    starts = []
    for spawn in map_data["player_spawn_points"]:
        node = (round(spawn[0]), round(spawn[1]))
        if not point_in_rect(*spawn, by_index[map_data["start_segment_index"]]) or not inside(node) or not clear(spawn, node):
            raise ValueError(f"Blocked player spawn {spawn}")
        starts.append(node)
    solved = set()
    visited = set()
    while True:
        visited = {starts[0]}
        queue = deque(visited)
        while queue:
            a = queue.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                b = (a[0] + dx, a[1] + dy)
                if b not in visited and inside(b) and clear(a, b):
                    visited.add(b)
                    queue.append(b)
        reachable = []
        for p in puzzles:
            if p["id"] in solved:
                continue
            target = (p["x"], p["y"])
            if any(point_in_rect(*v, by_index[p["segment_index"]]) and
                   math.dist(v, target) <= p["radius"] and
                   collision_path_clear(v, target, 0, blockers(v, target)) for v in visited):
                reachable.append(p)
        triggered = [w for w in proximity_doors if w["id"] not in opened and
                     any(distance_to_door(*v, w) <= w["trigger_radius"] for v in visited)]
        if not reachable and not triggered:
            break
        opened.update(w["id"] for w in triggered)
        for p in reachable:
            solved.add(p["id"])
            opened.add(p["door_id"])
    if len(solved) != len(puzzles):
        raise ValueError(f"Unreachable upstream puzzles: {sorted(set(puzzle_by_id) - solved)}")
    if any(s not in visited for s in starts):
        raise ValueError("Disconnected player spawns")
    if any(not any(point_in_rect(*v, s) for v in visited) for s in segments):
        raise ValueError("Unreachable segment interior")
    for c in connectors:
        center = (c["x"], c["y"])
        if not any(math.dist(v, center) <= 1.5 and clear(v, center) for v in visited):
            raise ValueError(f"Unreachable connector {c['id']}")
    for e in map_data["enemies"]:
        position = (e["x"], e["y"])
        if not inside(position) or not collision_path_clear(position, position, e["radius"], permanent) or not any(
                math.dist(v, position) <= 1.5 and clear(v, position) for v in visited):
            raise ValueError(f"Blocked enemy spawn {e['id']}")
    for target in map_data.get("crates", []) + map_data.get("loot", []) + [p for p in map_data["puzzles"] if p.get("prop_id")]:
        position = (target["x"], target["y"])
        reach = min(RULES["interaction"]["interact_radius"], target["radius"]) if target.get("type") == "supply_crate" and target.get("locked") else RULES["interaction"]["interact_radius"]
        if not inside(position) or not any(math.dist(v, position) <= reach and
                collision_path_clear(v, position, 0, permanent) for v in visited):
            raise ValueError(f"Unreachable interaction {target['id']}")
    ext = map_data["extraction_zone"]
    end = by_index[map_data["end_segment_index"]]
    if not point_in_rect(ext["x"], ext["y"], end) or not point_in_rect(ext["x"] + ext["w"], ext["y"] + ext["h"], end):
        raise ValueError("Extraction outside explicit end segment")
    if not any(ext["x"] + radius <= x <= ext["x"] + ext["w"] - radius and
               ext["y"] + radius <= y <= ext["y"] + ext["h"] - radius for x, y in visited):
        raise ValueError("Unreachable extraction footprint")
    wires = map_data.get("wires", [])
    if {w["puzzle_id"] for w in wires} != set(puzzle_by_id) or len(wires) != len(puzzles):
        raise ValueError("Missing or duplicate wire routes")
    for wire in wires:
        path = wire["path"]
        puzzle = puzzle_by_id.get(wire["puzzle_id"])
        if not puzzle or wire["door_id"] != puzzle["door_id"] or not path or tuple(path[0]) != (puzzle["x"], puzzle["y"]) or not point_in_rect(*path[-1], wall_by_id[wire["door_id"]]):
            raise ValueError("Invalid wire endpoints/references")
        blocking = [w for w in walls if w["id"] != wire["door_id"] and w.get("active", True)]
        if len(path) < 2 or any(not inside(p) for p in path) or any(
                not collision_path_clear(a, b, 0.22, blocking) for a, b in zip(path, path[1:])):
            raise ValueError(f"Blocked wire route {wire['puzzle_id']}")


def verify_level_solvability(map_data: dict) -> bool:
    try:
        validate_generated_map(map_data)
    except (ValueError, KeyError, TypeError, IndexError):
        return False
    return True


def build_mission_map(mission_id: str, seed: int = 42) -> dict:
    """Builds a verified WFC level map for a mission."""
    if mission_id not in MISSIONS or type(seed) is not int:
        raise ValueError("Known mission and integer seed required")
    diagnostics = []
    for attempt in range(MAX_ATTEMPTS):
        resolved_seed = seed + attempt * 17
        layout = _try_seed(mission_id, resolved_seed, diagnostics)
        if layout is None:
            continue
        try:
            candidate = build_wfc_map(mission_id, seed, layout=layout)
            validate_generated_map(candidate)
        except ValueError as exc:
            diagnostics.append({"seed": resolved_seed, "reason": str(exc)})
            continue
        candidate.update(requested_seed=seed, resolved_seed=resolved_seed,
                         generation_diagnostics=diagnostics, generation_attempts=attempt + 1,
                         geometry_validated=True)
        return candidate
    raise GenerationError(mission_id, seed, diagnostics)
