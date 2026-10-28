"""Explicit, finite objective graph and idempotent supported completion effects."""

from backend.app.content import CATALOG, mutable_copy, validate_objectives
from backend.app.engine import doors


def map_objectives(map_data, *, catalog=CATALOG):
    puzzles = list(map_data.get("puzzles", [])) + [c["puzzle"] for c in map_data.get("crates", []) if c.get("puzzle")]
    if "objective_definitions" not in map_data:
        # Runtime compatibility mapping, never a saved-layout rewrite.
        records = []
        for puzzle in map_data.get("puzzles", []):
            effects = [{"handler": "open_door", "target_id": puzzle["door_id"]}] if puzzle.get("door_id") in {w["id"] for w in map_data["walls"]} else []
            records.append({"id": "puzzle:" + puzzle["id"], "label": puzzle["label"], "condition": "puzzle_solved", "target_id": puzzle["id"], "effects": effects})
        records.extend(mutable_copy(catalog.definitions["missions"][map_data["mission_id"]]["objectives"]))
        required = [r["id"] for r in records if r.get("required", True)]
        for crate in map_data.get("crates", []):
            if crate.get("puzzle"):
                records.append({"id": "puzzle:" + crate["puzzle"]["id"], "label": crate["puzzle"]["label"], "condition": "puzzle_solved", "target_id": crate["puzzle"]["id"],
                                "effects": [{"handler": "unlock_crate", "target_id": crate["id"]}], "required": False})
        records.append({"id": "mission_complete", "label": "Complete mission objectives", "condition": "complete",
                        "dependencies": required,
                        "effects": [{"handler": effect} for effect in catalog.definitions["missions"][map_data["mission_id"]]["completion_effects"]]})
        map_data["objective_definitions"] = records
    records = validate_objectives(map_data["objective_definitions"], puzzles={p["id"] for p in puzzles},
                                  enemies={e["id"] for e in map_data.get("enemies", [])}, walls={w["id"] for w in map_data["walls"]},
                                  crates={c["id"] for c in map_data.get("crates", [])})
    map_data["objective_definitions"] = records
    map_data["objectives"] = [{"id": r["id"], "label": r["label"], "required": r["required"], "completed": False} for r in records]


def open_door(room, effect):
    wall = next(w for w in room.map_data["walls"] if w["id"] == effect["target_id"])
    doors.open_door(room, wall)


def unlock_crate(room, effect):
    next(c for c in room.map_data["crates"] if c["id"] == effect["target_id"])["locked"] = False


def activate_extraction(room, effect):
    extraction = room.map_data.get("extraction_zone")
    if extraction and not extraction["active"]:
        extraction["active"] = True
        room.events_log.append("All mission objectives complete! Proceed to the Extraction Pad!")


EFFECTS = {"open_door": open_door, "unlock_crate": unlock_crate, "activate_extraction": activate_extraction}


def update(room):
    data = room.map_data
    if "objectives" not in data:
        map_objectives(data, catalog=room.catalog)
    states = {s["id"]: s for s in data["objectives"]}
    puzzles = {p["id"]: p for p in data["puzzles"]}
    puzzles.update({c["puzzle"]["id"]: c["puzzle"] for c in data.get("crates", []) if c.get("puzzle")})
    enemies = {e["id"]: e for e in data["enemies"]}
    changed = True
    while changed:
        changed = False
        for record in data["objective_definitions"]:
            state = states[record["id"]]
            if state["completed"] or not all(states[d]["completed"] for d in record["dependencies"]):
                continue
            condition = record["condition"]
            satisfied = condition == "complete" or (condition == "puzzle_solved" and puzzles.get(record["target_id"], {}).get("solved", False)) or (condition == "enemy_dead" and enemies.get(record["target_id"], {}).get("hp", 1) <= 0)
            if satisfied:
                for effect in record["effects"]:
                    EFFECTS[effect["handler"]](room, effect)
                state["completed"] = True
                changed = True
                room.interaction_revision += 1
