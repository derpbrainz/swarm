"""Authored threshold instances and authoritative, one-way door activation."""

import math

from backend.app.content import RULES, mutable_copy


def configure_threshold(wall, catalog):
    policy = catalog.definitions["thresholds"][wall["threshold_id"]]
    wall.update(mutable_copy({key: value for key, value in policy.items() if key != "puzzle_id"}))
    wall.update(type=policy["door_type"], is_door=True, material="door", blocks_movement=True, blocks_projectile=True)


def distance_to_door(x, y, wall):
    """Distance to the actual rectangle, not the midpoint of a long doorway."""
    return math.hypot(max(wall["x"] - x, 0, x - wall["x"] - wall["w"]),
                      max(wall["y"] - y, 0, y - wall["y"] - wall["h"]))


def open_door(room, wall):
    if not wall.get("active", True):
        return
    wall.update(active=False, blocks_movement=False, blocks_projectile=False, blocks_sight=False, state="open")
    room.map_data["geometry_revision"] = room.map_data.get("geometry_revision", 0) + 1
    room._emit_sound(wall["x"] + wall["w"] / 2, wall["y"] + wall["h"] / 2, 0, "door",
                     sound_type=wall.get("sound_type", RULES["audio"]["door_sound"]), source_id=wall["id"])


def update_proximity_doors(room, *, player=None, position=None):
    if not room.system_enabled("movement"):
        return
    players = [player] if player is not None else room.players.values()
    for wall in room.map_data.get("walls", []):
        if wall.get("mode") != "proximity" or not wall.get("active", True):
            continue
        for operative in players:
            if operative.get("downed") or operative.get("hp", 0) <= 0 or operative["id"] in room.pending_disconnects:
                continue
            x, y = position if position is not None else (operative["x"], operative["y"])
            if distance_to_door(x, y, wall) <= wall["trigger_radius"]:
                open_door(room, wall)
                break
