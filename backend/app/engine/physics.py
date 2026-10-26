"""
2D continuous geometry and collision helpers for an isometric shooter.
"""

import math


def circle_vs_aabb(cx: float, cy: float, r: float, rect: dict) -> tuple[float, float, bool]:
    """
    Resolve circle-vs-AABB overlap.
    rect keys: x, y, w, h (top-left coordinates and dimensions).
    Returns (pushed_cx, pushed_cy, True) if overlapping, else (cx, cy, False).
    """
    x0 = float(rect["x"])
    y0 = float(rect["y"])
    x1 = x0 + float(rect["w"])
    y1 = y0 + float(rect["h"])

    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0

    closest_x = min(max(cx, x0), x1)
    closest_y = min(max(cy, y0), y1)

    dx = cx - closest_x
    dy = cy - closest_y
    dist_sq = dx * dx + dy * dy

    if dist_sq > r * r:
        return cx, cy, False

    if dist_sq == 0.0:
        left = cx - x0
        right = x1 - cx
        top = cy - y0
        bottom = y1 - cy
        min_dist = min(left, right, top, bottom)
        if min_dist == left:
            return x0 - r, cy, True
        elif min_dist == right:
            return x1 + r, cy, True
        elif min_dist == top:
            return cx, y0 - r, True
        else:
            return cx, y1 + r, True

    dist = math.sqrt(dist_sq)
    penetration = r - dist
    if penetration <= 0.0:
        return cx, cy, True

    nx = dx / dist
    ny = dy / dist
    return cx + nx * penetration, cy + ny * penetration, True


def move_with_collisions(
    x: float,
    y: float,
    vx: float,
    vy: float,
    dt: float,
    radius: float,
    walls: list[dict],
) -> tuple[float, float]:
    """
    Move a circle by velocity * dt and resolve circle-vs-AABB collisions
    with sub-stepping to prevent tunneling through thin walls.
    """
    total_dx = vx * dt
    total_dy = vy * dt
    dist = math.hypot(total_dx, total_dy)
    max_step = max(0.05, radius * 0.45)
    steps = max(1, int(math.ceil(dist / max_step)))
    step_dx = total_dx / steps
    step_dy = total_dy / steps

    for _ in range(steps):
        x += step_dx
        y += step_dy
        for _ in range(2):
            for wall in walls:
                if not wall.get("active", True):
                    continue
                x, y, _ = circle_vs_aabb(x, y, radius, wall)

    return x, y


def segment_intersects_aabb(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    rect: dict,
) -> float | None:
    """
    Liang-Barsky slab method.
    Tests whether the segment (x1, y1) -> (x2, y2) intersects rect.
    Returns entry parameter t in [0.0, 1.0] if it intersects, else None.
    """
    x0 = float(rect["x"])
    y0 = float(rect["y"])
    x1_rect = x0 + float(rect["w"])
    y1_rect = y0 + float(rect["h"])

    dx = x2 - x1
    dy = y2 - y1

    t_min = 0.0
    t_max = 1.0

    for p, q in (
        (-dx, x1 - x0),
        (dx, x1_rect - x1),
        (-dy, y1 - y0),
        (dy, y1_rect - y1),
    ):
        if abs(p) < 1e-9:
            if q < 0.0:
                return None
        else:
            t = q / p
            if p < 0.0:
                if t > t_max:
                    return None
                if t > t_min:
                    t_min = t
            else:
                if t < t_min:
                    return None
                if t < t_max:
                    t_max = t

    if t_min <= t_max and t_min <= 1.0 and t_max >= 0.0:
        return max(0.0, t_min)
    return None


def has_line_of_sight(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    walls: list[dict],
) -> bool:
    """
    Returns True if no active sight-blocking wall intersects (x1, y1) -> (x2, y2).
    """
    for wall in walls:
        if not wall.get("active", True):
            continue
        if not wall.get("blocks_sight", True):
            continue
        if segment_intersects_aabb(x1, y1, x2, y2, wall) is not None:
            return False
    return True


def raycast_walls(
    ox: float,
    oy: float,
    angle: float,
    max_dist: float,
    walls: list[dict],
    check_sight_only: bool = False,
) -> tuple[float, float, float, dict | None]:
    """
    Casts a ray from (ox, oy) in direction `angle` up to `max_dist`.
    Returns (hit_x, hit_y, hit_dist, hit_wall).
    """
    ex = ox + math.cos(angle) * max_dist
    ey = oy + math.sin(angle) * max_dist

    best_t = 1.0
    best_wall = None

    for wall in walls:
        if not wall.get("active", True):
            continue
        if check_sight_only and not wall.get("blocks_sight", True):
            continue
        t = segment_intersects_aabb(ox, oy, ex, ey, wall)
        if t is not None and t < best_t:
            best_t = t
            best_wall = wall

    hit_x = ox + (ex - ox) * best_t
    hit_y = oy + (ey - oy) * best_t
    hit_dist = max_dist * best_t
    return hit_x, hit_y, hit_dist, best_wall


def point_in_rect(px: float, py: float, rect: dict) -> bool:
    """
    Returns True if (px, py) lies inside rect (x, y, w, h).
    """
    x0 = float(rect["x"])
    y0 = float(rect["y"])
    return x0 <= px <= x0 + float(rect["w"]) and y0 <= py <= y0 + float(rect["h"])
