"""
backend/app/engine/sound_occlusion.py

Sound occlusion calculations for Alien Swarm's 2D AABB level geometry.

This module determines how much a sound emitted at one point in the level
is attenuated and low-pass filtered by the time it reaches a listener,
based on solid walls and closed doors intersecting the direct line of
sight (line of hearing) between the two points.

Design notes
------------
- Walls and doors are axis-aligned bounding boxes (AABBs) represented as
  dicts with at least a ``rect`` key: ``{"x": float, "y": float,
  "w": float, "h": float}`` (or an equivalent 4-tuple), consumed by
  ``physics.segment_intersects_aabb``.
- Doors are identified by an explicit ``is_door`` flag or by their ``id``
  containing the substrings "door" or "bulkhead" (case-insensitive).
- A door is OPEN (non-blocking) if ``active`` is explicitly ``False`` or
  ``state == "open"``. Otherwise it is treated as CLOSED (blocking),
  matching the default "active: True" behaviour of regular walls.
- Obstruction scoring:
    * each intersecting solid wall    -> +1.5 points
    * each intersecting CLOSED door   -> +1.0 points
    * open doors                      -> +0.0 points (never block sound)
- A small spatial/geometry cache avoids redundant raycasting when many
  sounds share the same emitter/listener grid cell within a single tick.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from backend.app.engine.physics import segment_intersects_aabb

__all__ = ["calculate_occlusion", "clear_occlusion_cache"]

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

#: Grid resolution (in world units/tiles) used to quantize emitter/listener
#: positions for cache-key purposes. Sounds whose emitter and listener both
#: round to the same quantized cell pair reuse the previous raycast result.
_CACHE_GRID_RESOLUTION: float = 0.5

#: Obstruction points contributed by a single solid wall intersection.
_WALL_PENALTY: float = 1.5

#: Obstruction points contributed by a single closed-door intersection.
_DOOR_PENALTY: float = 1.0

#: Maximum number of entries retained in the occlusion cache (simple LRU).
_CACHE_MAX_SIZE: int = 2048

#: Occlusion gain/cutoff tiers, as (max_obstruction_points, gain, cutoff_hz).
#: Evaluated in ascending order; the first tier whose threshold is not
#: exceeded by the total obstruction score is applied.
_OCCLUSION_TIERS: Tuple[Tuple[float, float, float], ...] = (
    (0.0, 1.00, 20000.0),
    (1.0, 0.45, 1800.0),
    (2.0, 0.22, 900.0),
    (3.5, 0.09, 550.0),
)

#: Fallback tier applied when obstruction points exceed every tier threshold.
_MAX_OCCLUSION_GAIN: float = 0.03
_MAX_OCCLUSION_CUTOFF: float = 350.0

#: Absolute clamp bounds for the returned low-pass cutoff frequency.
_MIN_LPF_HZ: float = 350.0
_MAX_LPF_HZ: float = 20000.0

# ---------------------------------------------------------------------------
# Internal cache
# ---------------------------------------------------------------------------

# Cache key -> (occl_gain, lpf_cutoff, blocks_count)
_occlusion_cache: "OrderedDict[tuple, Tuple[float, float, int]]" = OrderedDict()


def clear_occlusion_cache() -> None:
    """Clear the module-level occlusion cache.

    Should be called whenever level geometry changes outside of the normal
    door state toggling captured by the cache key (e.g. on level load),
    or periodically between ticks if callers want a hard guarantee of
    freshness.
    """
    _occlusion_cache.clear()


def _quantize(value: float, resolution: float = _CACHE_GRID_RESOLUTION) -> float:
    """Round ``value`` to the nearest multiple of ``resolution``."""
    return round(value / resolution) * resolution


def _is_door(surface: Dict) -> bool:
    """Return True if ``surface`` represents a door/bulkhead."""
    if surface.get("is_door"):
        return True
    surface_id = surface.get("id", "")
    if isinstance(surface_id, str):
        lowered = surface_id.lower()
        if "door" in lowered or "bulkhead" in lowered:
            return True
    return False


def _is_door_open(door: Dict) -> bool:
    """Return True if ``door`` is currently open (non-blocking)."""
    if door.get("active") is False:
        return True
    if door.get("state") == "open":
        return True
    return False


def _get_rect(surface: Dict) -> Optional[Dict[str, float]]:
    """Extract an {'x': float, 'y': float, 'w': float, 'h': float} rect dict from a wall/door dict.

    Supports either a nested ``rect`` dict/tuple or flat x/y/w/h keys.
    Returns None if no usable geometry is present.
    """
    rect = surface.get("rect")
    if rect is not None:
        if isinstance(rect, dict):
            try:
                return {"x": float(rect["x"]), "y": float(rect["y"]), "w": float(rect["w"]), "h": float(rect["h"])}
            except KeyError:
                return None
        if isinstance(rect, (tuple, list)) and len(rect) == 4:
            return {"x": float(rect[0]), "y": float(rect[1]), "w": float(rect[2]), "h": float(rect[3])}
        return None

    if all(k in surface for k in ("x", "y", "w", "h")):
        return {"x": float(surface["x"]), "y": float(surface["y"]), "w": float(surface["w"]), "h": float(surface["h"])}

    return None


def _geometry_fingerprint(walls: List[Dict], doors: Optional[List[Dict]]) -> tuple:
    """Build a hashable fingerprint of geometry state relevant to occlusion.

    Captures each surface's identity and door open/closed state so that the
    cache is correctly invalidated the moment a door opens or closes,
    without requiring a full geometric hash of static wall positions.
    """
    fingerprint = []

    for wall in walls:
        is_door = _is_door(wall)
        blocking = (not is_door) or (not _is_door_open(wall))
        fingerprint.append((wall.get("id"), blocking))

    if doors:
        for door in doors:
            blocking = not _is_door_open(door)
            fingerprint.append((door.get("id"), blocking))

    return tuple(fingerprint)


def _score_surface(
    emitter_x: float,
    emitter_y: float,
    listener_x: float,
    listener_y: float,
    surface: Dict,
) -> float:
    """Return the obstruction score contributed by a single surface.

    Returns 0.0 if the surface does not intersect the sound path, is an
    open door, or lacks usable geometry.
    """
    rect = _get_rect(surface)
    if rect is None:
        return 0.0

    is_door = _is_door(surface)

    # Open doors never block sound; skip the raycast entirely.
    if is_door and _is_door_open(surface):
        return 0.0

    if not segment_intersects_aabb(emitter_x, emitter_y, listener_x, listener_y, rect):
        return 0.0

    return _DOOR_PENALTY if is_door else _WALL_PENALTY


def _resolve_tier(obstruction_points: float) -> Tuple[float, float]:
    """Map a total obstruction score to (occl_gain, lpf_cutoff)."""
    for threshold, gain, cutoff in _OCCLUSION_TIERS:
        if obstruction_points <= threshold:
            return gain, cutoff

    # Last defined tier threshold is 3.5; anything above falls through.
    if obstruction_points <= 3.5:
        _, gain, cutoff = _OCCLUSION_TIERS[-1]
        return gain, cutoff

    return _MAX_OCCLUSION_GAIN, _MAX_OCCLUSION_CUTOFF


def calculate_occlusion(
    emitter_x: float,
    emitter_y: float,
    listener_x: float,
    listener_y: float,
    walls: List[Dict],
    doors: Optional[List[Dict]] = None,
) -> Tuple[float, float, int]:
    """Compute sound occlusion attenuation/filtering between two points.

    Casts a straight line ("line of hearing") from the sound emitter to the
    listener and tallies every solid wall and closed door intersecting that
    line. The resulting obstruction score is mapped to an amplitude gain
    multiplier and a low-pass filter cutoff frequency, simulating how
    muffled a sound becomes when heard through walls/doors.

    Open doors never obstruct sound. Closed doors obstruct less than solid
    walls (1.0 point vs 1.5 points), reflecting their typically thinner/
    weaker construction.

    Results are cached per-tick (effectively) using a quantized grid key so
    that repeated queries for emitters/listeners occupying the same
    approximate grid cell -- and against unchanged geometry -- avoid
    redundant raycasting.

    Args:
        emitter_x: World X coordinate of the sound source.
        emitter_y: World Y coordinate of the sound source.
        listener_x: World X coordinate of the listener (player/camera).
        listener_y: World Y coordinate of the listener.
        walls: List of wall dicts, each exposing AABB geometry consumable
            by ``segment_intersects_aabb`` (either nested ``rect`` or flat
            ``x``/``y``/``w``/``h`` keys), plus optional door metadata
            (``is_door``, ``id``, ``active``, ``state``).
        doors: Optional separate list of door dicts, in the same shape as
            ``walls``. Useful if the caller keeps doors in a distinct
            collection from static walls.

    Returns:
        A 3-tuple of:
            - occl_gain (float): Amplitude multiplier in [0.0, 1.0].
            - lpf_cutoff (float): Low-pass filter cutoff in Hz, clamped to
              [350.0, 20000.0].
            - blocks_count (int): Total number of obstructing surfaces
              (solid walls + closed doors) intersecting the sound path.
    """
    doors = doors or []

    qex = _quantize(emitter_x)
    qey = _quantize(emitter_y)
    qlx = _quantize(listener_x)
    qly = _quantize(listener_y)

    # Normalize direction so that (A->B) and (B->A) share a cache entry.
    if (qex, qey) > (qlx, qly):
        qex, qey, qlx, qly = qlx, qly, qex, qey

    geometry_key = _geometry_fingerprint(walls, doors)
    cache_key = (qex, qey, qlx, qly, geometry_key)

    cached = _occlusion_cache.get(cache_key)
    if cached is not None:
        # Mark as recently used (manual LRU bookkeeping).
        _occlusion_cache.move_to_end(cache_key)
        return cached

    obstruction_points = 0.0
    blocks_count = 0

    for wall in walls:
        score = _score_surface(emitter_x, emitter_y, listener_x, listener_y, wall)
        if score > 0.0:
            obstruction_points += score
            blocks_count += 1

    for door in doors:
        score = _score_surface(emitter_x, emitter_y, listener_x, listener_y, door)
        if score > 0.0:
            obstruction_points += score
            blocks_count += 1

    occl_gain, lpf_cutoff = _resolve_tier(obstruction_points)
    lpf_cutoff = max(_MIN_LPF_HZ, min(_MAX_LPF_HZ, lpf_cutoff))
    occl_gain = max(0.0, min(1.0, occl_gain))

    result = (occl_gain, lpf_cutoff, blocks_count)

    _occlusion_cache[cache_key] = result
    _occlusion_cache.move_to_end(cache_key)
    if len(_occlusion_cache) > _CACHE_MAX_SIZE:
        _occlusion_cache.popitem(last=False)

    return result