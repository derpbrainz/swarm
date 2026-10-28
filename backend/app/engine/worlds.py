"""Authored world instantiation, independent of mission generation and progress saves."""

import uuid

from backend.app.content import mutable_copy
from backend.app.engine.props import instantiate_props, world_surfaces
from backend.app.engine.physics import circle_vs_aabb


def instantiate_world(identity, catalog):
    definition = mutable_copy(catalog.definitions["worlds"][identity])
    props, targets = instantiate_props(definition["props"], catalog=catalog)
    world = {"world_id": uuid.uuid4().hex, "world_kind": definition["kind"], "world_definition_id": identity,
             "world_name": definition["name"], "capabilities": mutable_copy(catalog.definitions["world_rules"][definition["rules_id"]]),
             "width": definition["width"], "height": definition["height"], "geometry_revision": 0,
             "segments": [{**tile, "index": i} for i, tile in enumerate(definition["tiles"])],
             "walls": [{**wall, "id": f"wall_{i}"} for i, wall in enumerate(definition["walls"])],
             "props": props, "puzzles": [t for t in targets if t["type"] not in ("stash", "mission_selection")],
             "interactions": [t for t in targets if t["type"] in ("stash", "mission_selection")],
             "player_spawn_points": [(p["x"], p["y"]) for p in definition["spawns"]],
             "enemies": [], "loot": [], "crates": [], "objective_definitions": []}
    radius = catalog.definitions["rules"]["player"]["radius"]
    for x, y in world["player_spawn_points"]:
        if any(w.get("active", True) and w.get("blocks_movement", True) and circle_vs_aabb(x, y, radius, w)[2] for w in world_surfaces(world)):
            raise ValueError(f"worlds.{identity}.spawns: blocked player spawn")
    return world
