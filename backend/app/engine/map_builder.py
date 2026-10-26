"""
Procedural segment stitcher that combines segment templates into a mission map.
"""

from __future__ import annotations

import math
import random
from backend.app.config.missions import MISSIONS, SEGMENT_TEMPLATES
from backend.app.config.classes import ENEMY_TYPES

HEIGHT = 16.0
WALL_THICKNESS = 0.8
DOOR_OPENING_Y = 6.0
DOOR_OPENING_HEIGHT = 4.0


def generate_puzzle_data(puzzle_type: str, seed_idx: int = 0) -> dict:
    """Generates interactive state for a terminal puzzle."""
    rng = random.Random(100 + seed_idx * 37)
    if puzzle_type == "power_reroute":
        # 4 nodes that must be rotated to target angles (0, 1, 2, 3) -> target is all 0
        nodes = [rng.choice([1, 2, 3]) for _ in range(4)]
        return {
            "type": "power_reroute",
            "nodes": nodes,
            "target": [0, 0, 0, 0],
        }
    else:
        # signal_decrypt: sequence of 4 hex codes to lock in
        symbols = ["A7", "C4", "F9", "E2", "B5", "D8"]
        target_seq = [rng.choice(symbols) for _ in range(4)]
        return {
            "type": "signal_decrypt",
            "target_sequence": target_seq,
            "progress": 0,
            "options": symbols,
        }


def build_mission_map(mission_id: str) -> dict:
    """
    Combines the mission's segment templates into a continuous world map.
    """
    mission = MISSIONS.get(mission_id, MISSIONS["outpost"])
    sequence = mission["segment_sequence"]
    enemy_mult = float(mission.get("enemy_multiplier", 1.0))

    walls: list[dict] = []
    puzzles: list[dict] = []
    enemies: list[dict] = []
    segments: list[dict] = []

    offset_x = 0.0
    segment_count = len(sequence)
    enemy_counter = 1

    spaceship = {
        "x": 1.8,
        "y": 5.0,
        "w": 6.4,
        "h": 6.0,
        "ramp_open": False,
        "door_wall_id": "spaceship_ramp",
    }
    player_spawn_points = [
        (3.8, 6.8),
        (3.8, 9.2),
        (5.6, 6.8),
        (5.6, 9.2),
    ]
    extraction_zone = None

    for idx, seg_key in enumerate(sequence):
        seg = SEGMENT_TEMPLATES[seg_key]
        width = float(seg["width"])
        height = HEIGHT

        segments.append(
            {
                "index": idx,
                "id": seg["id"],
                "name": seg["name"],
                "theme": seg.get("theme", "industrial"),
                "x": offset_x,
                "y": 0.0,
                "w": width,
                "h": height,
            }
        )

        # North & South perimeter walls
        walls.append(
            {
                "id": f"wall_{idx}_top",
                "x": offset_x,
                "y": 0.0,
                "w": width,
                "h": WALL_THICKNESS,
                "active": True,
                "blocks_sight": True,
                "type": "wall",
            }
        )
        walls.append(
            {
                "id": f"wall_{idx}_bottom",
                "x": offset_x,
                "y": height - WALL_THICKNESS,
                "w": width,
                "h": WALL_THICKNESS,
                "active": True,
                "blocks_sight": True,
                "type": "wall",
            }
        )

        # West perimeter wall and Spaceship bay on first segment
        if idx == 0:
            walls.append(
                {
                    "id": "wall_0_west",
                    "x": offset_x,
                    "y": 0.0,
                    "w": WALL_THICKNESS,
                    "h": height,
                    "active": True,
                    "blocks_sight": True,
                    "type": "wall",
                }
            )
            # Spaceship top, bottom, and rear hull walls
            walls.append(
                {
                    "id": "ship_hull_top",
                    "x": spaceship["x"],
                    "y": spaceship["y"],
                    "w": spaceship["w"],
                    "h": 0.5,
                    "active": True,
                    "blocks_sight": True,
                    "type": "ship_hull",
                }
            )
            walls.append(
                {
                    "id": "ship_hull_bottom",
                    "x": spaceship["x"],
                    "y": spaceship["y"] + spaceship["h"] - 0.5,
                    "w": spaceship["w"],
                    "h": 0.5,
                    "active": True,
                    "blocks_sight": True,
                    "type": "ship_hull",
                }
            )
            walls.append(
                {
                    "id": "ship_hull_rear",
                    "x": spaceship["x"],
                    "y": spaceship["y"],
                    "w": 0.5,
                    "h": spaceship["h"],
                    "active": True,
                    "blocks_sight": True,
                    "type": "ship_hull",
                }
            )
            # Spaceship front exit ramp (opens once dropship sequence finishes)
            walls.append(
                {
                    "id": "spaceship_ramp",
                    "x": spaceship["x"] + spaceship["w"] - 0.5,
                    "y": spaceship["y"] + 0.5,
                    "w": 0.5,
                    "h": spaceship["h"] - 1.0,
                    "active": True,
                    "blocks_sight": False,
                    "type": "ship_ramp",
                }
            )

        # Interior obstacle walls
        for w_idx, iw in enumerate(seg.get("interior_walls", [])):
            walls.append(
                {
                    "id": f"wall_{idx}_int_{w_idx}",
                    "x": offset_x + float(iw["x"]),
                    "y": float(iw["y"]),
                    "w": float(iw["w"]),
                    "h": float(iw["h"]),
                    "active": True,
                    "blocks_sight": bool(iw.get("blocks_sight", True)),
                    "type": "wall" if iw.get("blocks_sight", True) else "cover",
                }
            )

        # Segment boundary / doorway on East side
        if idx < segment_count - 1:
            walls.append(
                {
                    "id": f"wall_{idx}_east_upper",
                    "x": offset_x + width - 0.6,
                    "y": 0.0,
                    "w": 1.2,
                    "h": DOOR_OPENING_Y,
                    "active": True,
                    "blocks_sight": True,
                    "type": "wall",
                }
            )
            walls.append(
                {
                    "id": f"wall_{idx}_east_lower",
                    "x": offset_x + width - 0.6,
                    "y": DOOR_OPENING_Y + DOOR_OPENING_HEIGHT,
                    "w": 1.2,
                    "h": height - (DOOR_OPENING_Y + DOOR_OPENING_HEIGHT),
                    "active": True,
                    "blocks_sight": True,
                    "type": "wall",
                }
            )

            if seg.get("has_exit_door"):
                door_id = f"door_{idx}"
                walls.append(
                    {
                        "id": door_id,
                        "x": offset_x + width - 0.4,
                        "y": DOOR_OPENING_Y,
                        "w": 0.8,
                        "h": DOOR_OPENING_HEIGHT,
                        "active": True,
                        "blocks_sight": True,
                        "type": "door",
                    }
                )
                p_cfg = seg.get("puzzle")
                if p_cfg:
                    puzzles.append(
                        {
                            "id": f"puzzle_{idx}",
                            "type": p_cfg["type"],
                            "label": p_cfg["label"],
                            "x": offset_x + float(p_cfg["x"]),
                            "y": float(p_cfg["y"]),
                            "radius": 2.8,
                            "solved": False,
                            "door_id": door_id,
                            "segment_index": idx,
                            "data": generate_puzzle_data(p_cfg["type"], idx),
                        }
                    )
        else:
            # Final East perimeter wall and Extraction Pad
            walls.append(
                {
                    "id": f"wall_{idx}_east_end",
                    "x": offset_x + width - WALL_THICKNESS,
                    "y": 0.0,
                    "w": WALL_THICKNESS,
                    "h": height,
                    "active": True,
                    "blocks_sight": True,
                    "type": "wall",
                }
            )
            extraction_zone = {
                "x": offset_x + width - 7.5,
                "y": 5.0,
                "w": 5.0,
                "h": 6.0,
                "active": False,
            }

        # Enemy spawns for this segment
        for sp in seg.get("enemy_spawns", []):
            etype = sp["type"]
            e_cfg = ENEMY_TYPES[etype]
            sx = offset_x + float(sp["x"])
            sy = float(sp["y"])
            pdx = float(sp.get("patrol_dx", 0.0))
            pdy = float(sp.get("patrol_dy", 0.0))
            enemies.append(
                {
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
                    "state": "patrol",  # 'patrol' | 'alerted' | 'engaged'
                    "waypoints": [(sx, sy), (sx + pdx, sy + pdy)],
                    "waypoint_index": 1,
                    "investigate_target": None,
                    "alert_timer": 0.0,
                    "target_player_id": None,
                    "attack_timer": 0.0,
                    "segment_index": idx,
                }
            )
            enemy_counter += 1

        offset_x += width

    return {
        "mission_id": mission["id"],
        "mission_name": mission["name"],
        "width": offset_x,
        "height": HEIGHT,
        "segments": segments,
        "walls": walls,
        "puzzles": puzzles,
        "enemies": enemies,
        "spaceship": spaceship,
        "player_spawn_points": player_spawn_points,
        "extraction_zone": extraction_zone,
    }
