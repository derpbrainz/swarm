import { worldToIso, TILE_W, TILE_H } from './iso.js';

export function rotateBounds(rect, x, y, angle = 0) {
  const c = Math.cos(angle), s = Math.sin(angle);
  const pts = [[rect.x, rect.y], [rect.x + rect.w, rect.y], [rect.x, rect.y + rect.h], [rect.x + rect.w, rect.y + rect.h]]
    .map(([px, py]) => ({ x: x + px * c - py * s, y: y + px * s + py * c }));
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
  return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
}

export function sceneOccluders(map) {
  return [...(map.walls || []), ...(map.props || []).filter(p => p.active !== false && p.geometry.sight)
    .map(p => ({ ...p.geometry.sight, active: true, blocks_sight: true }))];
}

// The adapter owns presentation only; a future renderer consumes this scene, not physics dictionaries.
export function buildScene(state, presentation, camera) {
  const entities = [];
  const add = (kind, source, suffix = '', colliderSource = source) => {
    const visualId = source.visual_id || presentation?.defaults[kind];
    const visual = presentation?.visuals[visualId];
    const angle = kind === 'prop' ? (source.orientation || 0) * Math.PI / 180 : source.angle || 0;
    const bounds = kind === 'wall' ? { x: source.x, y: source.y, w: source.w, h: source.h } :
      rotateBounds(visual?.footprint || { x: -.3, y: -.3, w: .6, h: .6 }, source.x, source.y, kind === 'prop' ? angle : 0);
    const iso = worldToIso(source.x, source.y, camera);
    const points = [[bounds.x, bounds.y], [bounds.x + bounds.w, bounds.y], [bounds.x, bounds.y + bounds.h], [bounds.x + bounds.w, bounds.y + bounds.h]]
      .map(([x, y]) => worldToIso(x, y, camera));
    const height = visual?.height || 56;
    const screenBounds = kind === 'wall' ? {
      x: Math.min(...points.map(p => p.x)), y: Math.min(...points.map(p => p.y)) - (visual?.elevation || 42),
      w: (bounds.w + bounds.h) * TILE_W / 2, h: (bounds.w + bounds.h) * TILE_H / 2 + (visual?.elevation || 42),
    } : { x: iso.x - (visual?.width || 48) * (visual?.pivot[0] ?? .5), y: iso.y - height * (visual?.pivot[1] ?? 1), w: visual?.width || 48, h: height };
    entities.push({ id: `${state.world_id || state.map.world_id || 'world'}:${kind}:${source.id}${suffix}`,
      entityId: source.id, kind, source, transform: { x: source.x, y: source.y, z: visual?.elevation || 0 },
      orientation: angle, animation: { state: source.downed ? 'downed' : source.state || (source.running ? 'run' : 'idle'), time: state.simulation_time || 0 },
      visualId, materialId: source.material_id || visual?.material_id, pivot: visual?.pivot || [.5, 1], bounds, screenBounds,
      layer: visual?.layer || 0, depth: bounds.x + bounds.y + (bounds.w + bounds.h) / 2,
      collider: kind === 'prop' ? { movement: source.geometry.movement, projectile: source.geometry.projectile } : {
        radius: colliderSource.radius, ...(kind === 'wall' ? { x: colliderSource.x, y: colliderSource.y, w: colliderSource.w, h: colliderSource.h } : {}) },
      occluder: kind === 'prop' ? { sight: source.geometry.sight, acoustic: source.geometry.acoustic, material: source.acoustic_material } : { sight: source.blocks_sight, acoustic: source.material },
      interaction: kind === 'prop' ? { id: source.interaction_id, bounds: source.geometry.interaction } : { id: source.id } });
  };
  for (const wall of state.map.walls || []) {
    if (wall.active === false) continue;
    // Bounded two-unit pieces prevent one long wall pivot from ordering its entire face.
    const axis = wall.w >= wall.h ? 'w' : 'h';
    const count = Math.ceil(wall[axis] / 2);
    for (let i = 0; i < count; i++) {
      const piece = { ...wall, [axis]: Math.min(2, wall[axis] - i * 2) };
      piece[axis === 'w' ? 'x' : 'y'] += i * 2;
      add('wall', piece, `:part${i}`, wall);
    }
  }
  for (const prop of state.map.props || []) if (prop.active !== false) add('prop', prop);
  for (const [kind, items] of [['crate', state.map.crates], ['loot', state.map.loot], ['puzzle', state.map.puzzles],
    ['turret', (state.deployables || []).filter(d => d.type === 'turret')], ['enemy', state.enemies], ['player', state.players]]) {
    for (const item of items || []) if (!item.collected && (kind !== 'enemy' || item.visible) && !(kind === 'puzzle' && item.prop_id)) add(kind, item);
  }
  return orderScene(entities);
}

export function orderScene(entities) {
  const sorted = [...entities].sort((a, b) => a.layer - b.layer || a.depth - b.depth || a.id.localeCompare(b.id));
  const edges = sorted.map(() => []), incoming = sorted.map(() => 0);
  const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  for (let i = 0; i < sorted.length; i++) for (let j = i + 1; j < sorted.length; j++) {
    const a = sorted[i], b = sorted[j];
    if (a.layer !== b.layer) { edges[i].push(j); incoming[j]++; continue; }
    if (!overlaps(a.screenBounds, b.screenBounds)) continue;
    const ar = a.bounds, br = b.bounds;
    let before = null;
    // Whole render footprints, not collider size or another centroid, determine occlusion.
    const aBehind = ar.x + ar.w <= br.x || ar.y + ar.h <= br.y;
    const bBehind = br.x + br.w <= ar.x || br.y + br.h <= ar.y;
    if (aBehind !== bBehind) before = aBehind ? i : j;
    if (before !== null) { const after = before === i ? j : i; edges[before].push(after); incoming[after]++; }
  }
  const result = [], done = new Set();
  while (result.length < sorted.length) {
    let index = incoming.findIndex((n, i) => n === 0 && !done.has(i));
    // Intersecting/ambiguous artwork has a stable fallback, never an unbounded sort cycle.
    if (index < 0) index = sorted.findIndex((_, i) => !done.has(i));
    done.add(index); result.push(sorted[index]);
    for (const next of edges[index]) incoming[next]--;
  }
  return result;
}
