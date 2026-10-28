"""Pure acoustic raycasts over explicit active/material/door geometry, with bounded caching."""

from collections import OrderedDict

from backend.app.content import RULES
from backend.app.engine.physics import segment_intersects_aabb

_occlusion_cache = OrderedDict()


def clear_occlusion_cache():
    _occlusion_cache.clear()


def _rect(surface):
    rect = surface.get("rect", surface)
    if isinstance(rect, (tuple, list)):
        return dict(zip(("x", "y", "w", "h"), rect))
    return {k: rect[k] for k in ("x", "y", "w", "h")} if all(k in rect for k in ("x", "y", "w", "h")) else None


def calculate_occlusion(emitter_x, emitter_y, listener_x, listener_y, walls, doors=None, *, world_id=None, geometry_revision=0):
    # Exact endpoints avoid grid aliases on opposite sides of a thin surface.
    surfaces = [(w, bool(w.get("is_door", False))) for w in walls if w.get("blocks_acoustic", True)]
    surfaces += [(d, True) for d in (doors or [])]
    geometry = tuple((s.get("id"), tuple((_rect(s) or {}).values()), s.get("active", True),
                      door, s.get("state"), s.get("material", "door" if door else "solid")) for s, door in surfaces)
    endpoints = tuple(sorted(((emitter_x, emitter_y), (listener_x, listener_y))))
    key = (world_id, geometry_revision, endpoints, geometry)
    if key in _occlusion_cache:
        _occlusion_cache.move_to_end(key)
        return _occlusion_cache[key]
    score, blocks = 0.0, 0
    rules = RULES["audio"]
    for surface, door in surfaces:
        if not surface.get("active", True) or (door and surface.get("state") == "open"):
            continue
        rect = _rect(surface)
        penalty = rules["material_penalties"][surface.get("material", "door" if door else "solid")]
        if rect is not None and penalty > 0 and segment_intersects_aabb(emitter_x, emitter_y, listener_x, listener_y, rect) is not None:
            score += penalty
            blocks += 1
    gain, cutoff = rules["occlusion_fallback_gain"], rules["occlusion_fallback_cutoff"]
    for threshold, tier_gain, tier_cutoff in rules["occlusion_tiers"]:
        if score <= threshold:
            gain, cutoff = tier_gain, tier_cutoff
            break
    result = gain, cutoff, blocks
    _occlusion_cache[key] = result
    if len(_occlusion_cache) > rules["occlusion_cache_limit"]:
        _occlusion_cache.popitem(last=False)
    return result
