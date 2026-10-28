/**
 * Isometric coordinate projection, mouse inverse projection, screen-aligned WASD
 * vector calculation, and 2D raycast visibility polygon calculation.
 */

export const TILE_W = 64;
export const TILE_H = 32;
export const WALL_HEIGHT_PX = 42;

export function worldToIso(wx, wy, camera) {
  const dx = wx - camera.x;
  const dy = wy - camera.y;
  return {
    x: camera.screenW * 0.5 + (dx - dy) * (TILE_W * 0.5),
    y: camera.screenH * 0.5 + (dx + dy) * (TILE_H * 0.5),
  };
}

export function isoToWorld(sx, sy, camera) {
  const px = sx - camera.screenW * 0.5;
  const py = sy - camera.screenH * 0.5;
  return {
    x: camera.x + px / TILE_W + py / TILE_H,
    y: camera.y + py / TILE_H - px / TILE_W,
  };
}

export function screenInputToWorldDelta(up, down, left, right) {
  let dx = 0;
  let dy = 0;
  if (up) {
    dx -= 1;
    dy -= 1;
  }
  if (down) {
    dx += 1;
    dy += 1;
  }
  if (left) {
    dx -= 1;
    dy += 1;
  }
  if (right) {
    dx += 1;
    dy -= 1;
  }
  const len = Math.hypot(dx, dy);
  if (len > 0) {
    dx /= len;
    dy /= len;
  }
  return { dx, dy };
}

export function segmentIntersectsAABB(x1, y1, x2, y2, rect) {
  const x0 = rect.x;
  const y0 = rect.y;
  const x1r = x0 + rect.w;
  const y1r = y0 + rect.h;
  const dx = x2 - x1;
  const dy = y2 - y1;

  let tMin = 0.0;
  let tMax = 1.0;

  const checks = [
    [-dx, x1 - x0],
    [dx, x1r - x1],
    [-dy, y1 - y0],
    [dy, y1r - y1],
  ];

  for (let i = 0; i < 4; i++) {
    const p = checks[i][0];
    const q = checks[i][1];
    if (Math.abs(p) < 1e-8) {
      if (q < 0) return null;
    } else {
      const t = q / p;
      if (p < 0) {
        if (t > tMax) return null;
        if (t > tMin) tMin = t;
      } else {
        if (t < tMin) return null;
        if (t < tMax) tMax = t;
      }
    }
  }
  if (tMin <= tMax && tMin <= 1.0 && tMax >= 0.0) {
    return Math.max(0.0, tMin);
  }
  return null;
}

export function computeVisibilityPolygon(ox, oy, maxDist, walls, rayCount = 96) {
  const pts = [];
  for (let i = 0; i < rayCount; i++) {
    const angle = (i / rayCount) * Math.PI * 2;
    const ex = ox + Math.cos(angle) * maxDist;
    const ey = oy + Math.sin(angle) * maxDist;
    let bestT = 1.0;

    for (let j = 0; j < walls.length; j++) {
      const w = walls[j];
      if (w.active === false || w.blocks_sight === false) continue;
      const t = segmentIntersectsAABB(ox, oy, ex, ey, w);
      if (t !== null && t < bestT) {
        bestT = t;
      }
    }
    pts.push({
      x: ox + (ex - ox) * bestT,
      y: oy + (ey - oy) * bestT,
    });
  }
  return pts;
}

export function pointInPolygon(x, y, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i], b = polygon[j];
    if ((a.y > y) !== (b.y > y) && x < (b.x - a.x) * (y - a.y) / (b.y - a.y) + a.x) inside = !inside;
  }
  return inside;
}
