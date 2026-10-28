import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildScene, sceneOccluders } from '../../frontend/src/js/scene.js';
import { computeVisibilityPolygon, pointInPolygon, worldToIso } from '../../frontend/src/js/iso.js';
import { AssetCache, safeImagePath } from '../../frontend/src/js/assets.js';
import { IsometricRenderer } from '../../frontend/src/js/renderer.js';

const presentation = JSON.parse(readFileSync(new URL('../../backend/app/content.json', import.meta.url))).presentation;
const camera = { x: 4, y: 4, screenW: 1280, screenH: 720 };
const state = { world_id: 'fixture', simulation_time: 1, map: { width: 20, height: 20,
  walls: [{ id: 'long', x: 0, y: 4, w: 10, h: 1, active: true }], props: [], crates: [], loot: [], puzzles: [] },
  players: [{ id: 'actor', x: 1, y: 5.6, hp: 100, max_hp: 100, angle: 0, sight_range: 12 }], enemies: [] };

test('scene IDs, bounds and projected depth are independent of hitboxes and never mutate state', () => {
  const before = JSON.stringify(state);
  const scene = buildScene(state, presentation, camera);
  assert.equal(scene.filter(e => e.kind === 'wall').length, 5);
  assert.equal(scene.find(e => e.kind === 'wall').collider.w, 10);
  const actorIndex = scene.findIndex(e => e.kind === 'player');
  assert.ok(scene.findIndex(e => e.kind === 'wall' && e.bounds.x === 0) < actorIndex);
  const actor = scene.find(e => e.kind === 'player');
  assert.equal(actor.transform.x, 1);
  assert.ok(Math.abs(actor.bounds.w - .84) < 1e-12);
  assert.deepEqual(actor.pivot, [.5, 1]);
  const iso = worldToIso(actor.transform.x, actor.transform.y, camera);
  assert.equal(iso.y, camera.screenH / 2 + (1 + 5.6 - 8) * 16);
  assert.deepEqual(scene.map(e => e.id), buildScene(state, presentation, camera).map(e => e.id));
  assert.equal(JSON.stringify(state), before);
});

test('all physical kinds share ordering and markers are not scene entities', () => {
  const s = structuredClone(state); s.map.walls = [];
  s.map.crates = [{ id: 'crate', x: 2, y: 2 }]; s.map.loot = [{ id: 'loot', x: 3, y: 3 }];
  s.map.puzzles = [{ id: 'puzzle', x: 4, y: 4 }];
  s.map.props = [{ id: 'table', x: 5, y: 5, visual_id: 'table', geometry: {}, orientation: 90 }];
  s.players[0].x = s.players[0].y = 6;
  assert.deepEqual(buildScene(s, presentation, camera).map(e => e.kind), ['crate', 'loot', 'puzzle', 'prop', 'player']);
});

test('visibility polygon rejects hidden explored cells and respects independently authored prop sight', () => {
  const map = { walls: [], props: [{ x: 6, y: 0, geometry: { sight: { x: 6, y: 0, w: .1, h: 20 } } }] };
  const poly = computeVisibilityPolygon(4, 5, 12, sceneOccluders(map), 84);
  assert.equal(pointInPolygon(9, 5, poly), false);
  assert.equal(pointInPolygon(5, 5, poly), true);
});

test('image cache readiness, shared paths, failure, unsafe paths and in-flight disposal', async () => {
  const images = [];
  const cache = new AssetCache({ one: { path: 'visual/test-floor.svg' }, alias: { path: 'visual/test-floor.svg' }, bad: { path: '../x.svg' } },
    () => { const image = { naturalWidth: 32 }; images.push(image); return image; });
  const entry = cache.get('one'); assert.equal(entry.state, 'loading');
  assert.equal(cache.get('alias'), entry); assert.equal(images.length, 1);
  images[0].onload(); await entry.promise; assert.equal(entry.state, 'ready');
  assert.equal(cache.get('bad').state, 'error'); assert.equal(cache.get('missing').state, 'error');
  cache.registry.fail = { path: 'visual/fail.svg' };
  const failed = cache.get('fail'); images[1].onerror(); await failed.promise;
  assert.equal(failed.state, 'error');
  cache.registry.pending = { path: 'visual/pending.svg' };
  const pending = cache.get('pending'); cache.dispose(); await pending.promise;
  assert.equal(pending.state, 'disposed'); assert.equal(cache.entries.size, 0);
  for (const path of ['https://example.com/a.svg', '/x.svg', 'visual/a.svg?x', 'visual/../a.svg', 'visual/a%2e.svg']) assert.equal(safeImagePath(path), false);
});

test('full procedural renderer does not mutate frozen snapshots and separates physical markers', () => {
  const context = new Proxy({ measureText: () => ({ width: 10 }) }, { get: (o, key) => o[key] || (() => {}) });
  globalThis.document = { createElement: () => ({ width: 1280, height: 720, getContext: () => context }) };
  const renderer = new IsometricRenderer({ width: 1280, height: 720, getContext: () => context });
  renderer.setPresentation(presentation);
  const s = structuredClone(state);
  const freeze = value => { Object.freeze(value); for (const child of Object.values(value)) if (child && typeof child === 'object') freeze(child); };
  freeze(s); const before = JSON.stringify(s);
  renderer.render(s, 'actor', null, camera); renderer.render(s, 'actor', null, camera);
  assert.equal(JSON.stringify(s), before);
  assert.equal(renderer.visibilityAt(9, 1), 'unknown');
  renderer.dispose(); assert.equal(renderer.playerAnim.size, 0);
});

test('directional sprite atlas consumes orientation animation pivot and material swap without collider changes', () => {
  const calls = [];
  const context = { drawImage: (...args) => calls.push(args) };
  globalThis.document = { createElement: () => ({ getContext: () => context }) };
  const renderer = new IsometricRenderer({ getContext: () => context });
  renderer.setPresentation(presentation);
  renderer.assets.get = () => ({ state: 'ready', image: { naturalWidth: 96, naturalHeight: 80 } });
  const s = structuredClone(state); s.map.walls = [];
  s.players[0].visual_id = 'test_actor'; s.players[0].angle = Math.PI / 2; s.simulation_time = .5;
  const before = JSON.stringify(s);
  const e = buildScene(s, presentation, camera)[0];
  renderer.drawSceneEntity(context, e, '', null, null, camera);
  const iso = worldToIso(1, 5.6, camera);
  assert.deepEqual(calls[0].slice(1), [24, 40, 24, 40, iso.x - 12, iso.y - 40, 24, 40]);
  assert.equal(JSON.stringify(s), before);
  const alternate = structuredClone(presentation); alternate.visuals.test_actor.material_id = 'test_red';
  assert.equal(buildScene(s, alternate, camera)[0].materialId, 'test_red');
  assert.deepEqual(buildScene(s, alternate, camera)[0].collider, e.collider);
});

test('exploration uses actual visibility, distinguishes prior/current/unknown, and resets by world not mission time', () => {
  const context = new Proxy({ measureText: () => ({ width: 10 }) }, { get: (o, key) => o[key] || (() => {}) });
  globalThis.document = { createElement: () => ({ width: 1280, height: 720, getContext: () => context }) };
  const r = new IsometricRenderer({ width: 1280, height: 720, getContext: () => context });
  const s = structuredClone(state); s.map.walls = [{ id: 'sight', x: 6, y: 0, w: .1, h: 20 }];
  s.players[0].x = 4; s.players[0].y = 5;
  r.render(s, 'actor', null, camera);
  assert.equal(r.visibilityAt(5, 5), 'current');
  assert.equal(r.visibilityAt(9, 5), 'unknown');
  s.players = []; r.render(s, 'actor', null, camera);
  assert.equal(r.visibilityAt(5, 5), 'explored');
  s.world_id = 'second'; s.mission_time = 100;
  r.render(s, 'actor', null, camera);
  assert.equal(r.visibilityAt(5, 5), 'unknown');
});

test('vertical long walls and wide table footprints order front actors independently of array order', () => {
  const s = structuredClone(state);
  s.map.walls = [{ id: 'vertical', x: 4, y: 0, w: 1, h: 12 }];
  s.players[0].x = 5.6; s.players[0].y = 1;
  let scene = buildScene(s, presentation, camera);
  assert.ok(scene.findIndex(e => e.kind === 'wall' && e.bounds.y === 0) < scene.findIndex(e => e.kind === 'player'));
  s.map.walls = [];
  s.map.props = [{ id: 'table', x: 4, y: 4, visual_id: 'table', geometry: {}, orientation: 0 }];
  s.players[0].x = 4; s.players[0].y = 5;
  scene = buildScene(s, presentation, camera);
  assert.deepEqual(scene.map(e => e.kind), ['prop', 'player']);
});

test('asset constructor failure has explicit fallback and late callbacks cannot revive disposed entries', async () => {
  const registry = { one: { path: 'visual/test-floor.svg' } };
  const broken = new AssetCache(registry, () => { throw new Error('no image API'); });
  assert.equal(broken.get('one').state, 'error');
  const image = { naturalWidth: 32 };
  const cache = new AssetCache(registry, () => image);
  const entry = cache.get('one'), late = image.onload;
  cache.dispose(); late(); await entry.promise;
  assert.equal(entry.state, 'disposed');
});

test('wall exploration is shared, retained outside sight, and reset by world or explicit teardown', () => {
  const context = new Proxy({ measureText: () => ({ width: 10 }) }, { get: (o, key) => o[key] || (() => {}) });
  globalThis.document = { createElement: () => ({ width: 1280, height: 720, getContext: () => context }) };
  const renderer = new IsometricRenderer({ width: 1280, height: 720, getContext: () => context });
  const s = structuredClone(state);
  renderer.render(s, 'actor', null, camera);
  const remembered = [...renderer.exploredWalls];
  assert.ok(remembered.length > 0);
  s.players = [];
  renderer.render(s, '', null, camera);
  assert.deepEqual([...renderer.exploredWalls], remembered);
  s.world_id = 'different-world';
  renderer.render(s, '', null, camera);
  assert.equal(renderer.exploredWalls.size, 0);
  renderer.exploredWalls.add('remembered-piece');
  renderer.resetExplored();
  assert.equal(renderer.exploredWalls.size, 0);
  renderer.dispose();
});
