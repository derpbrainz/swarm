"""Expand authored props into independent runtime geometry and existing terminals."""

import math

from backend.app.content import CATALOG, mutable_copy


def transformed_rect(rect, x, y, orientation):
    angle = math.radians(orientation)
    points = [(x + px * math.cos(angle) - py * math.sin(angle),
               y + px * math.sin(angle) + py * math.cos(angle))
              for px, py in ((rect["x"], rect["y"]), (rect["x"] + rect["w"], rect["y"]),
                             (rect["x"], rect["y"] + rect["h"]), (rect["x"] + rect["w"], rect["y"] + rect["h"]))]
    xs, ys = zip(*points)
    return {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}


def instantiate_props(placements, *, x=0, y=0, prefix="", catalog=CATALOG):
    props, puzzles = [], []
    from backend.app.engine.map_builder import generate_puzzle_data
    for placement in placements:
        archetype = catalog.definitions["props"][placement["archetype"]]
        prop = {"id": prefix + placement["id"], "archetype": placement["archetype"],
                "x": x + placement["x"], "y": y + placement["y"], "orientation": placement.get("orientation", 0),
                "visual_id": archetype["visual_id"], "geometry": {}}
        for channel in ("movement", "projectile", "sight", "acoustic", "interaction"):
            rect = archetype["geometry"].get(channel)
            if rect:
                prop["geometry"][channel] = transformed_rect(rect, prop["x"], prop["y"], prop["orientation"])
        prop["acoustic_material"] = archetype["geometry"]["acoustic_material"]
        interaction = archetype.get("interaction")
        if interaction:
            target = prop["geometry"].get("interaction")
            px, py = (target["x"] + target["w"] / 2, target["y"] + target["h"] / 2) if target else (prop["x"], prop["y"])
            prop["interaction_id"] = prop["id"] + "_terminal"
            if interaction["type"] in ("stash", "mission_selection"):
                puzzles.append({**mutable_copy(interaction), "id": prop["interaction_id"], "prop_id": prop["id"], "x": px, "y": py})
                props.append(prop)
                continue
            puzzles.append({**mutable_copy(interaction), "id": prop["interaction_id"], "prop_id": prop["id"],
                            "definition_id": interaction["type"], "definition": mutable_copy(catalog.definitions["puzzles"][interaction["type"]]),
                            "x": px, "y": py, "solved": False, "door_id": None,
                            "data": generate_puzzle_data(interaction["type"], catalog=catalog)})
        props.append(prop)
    return props, puzzles


def world_surfaces(map_data):
    """Single geometry list accepted by the existing physics/AI/acoustic consumers."""
    surfaces = list(map_data.get("walls", []))
    for prop in map_data.get("props", []):
        if prop.get("active", True) is False:
            continue
        for channel in ("movement", "projectile", "sight", "acoustic"):
            rect = prop["geometry"].get(channel)
            if rect:
                surfaces.append({**rect, "id": prop["id"] + ":" + channel, "active": True,
                                 "blocks_movement": channel == "movement", "blocks_projectile": channel == "projectile",
                                 "blocks_sight": channel == "sight", "blocks_acoustic": channel == "acoustic",
                                 "is_door": False, "material": prop["acoustic_material"]})
    return surfaces
