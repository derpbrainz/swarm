import { test, expect } from '@playwright/test';

test('actual Chrome local textures detail props fallback and immutable render scene', async ({ page }, testInfo) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.catalog);
  await page.locator('#class-modal .class-title').first().click();
  await page.request.post('/__test__/fixture', { data: { action: 'install_visual' } });
  await page.waitForFunction(() => window.__game.state.map?.props?.some(p => p.visual_id === 'test_detail'));
  const result = await page.evaluate(async () => {
    const { IsometricRenderer } = await import('/static/js/renderer.js');
    const { worldToIso } = await import('/static/js/iso.js');
    const canvas = document.createElement('canvas'); canvas.width = 1280; canvas.height = 720;
    canvas.id = 'visual-fixture-canvas'; document.body.append(canvas);
    canvas.style.cssText = 'position:fixed;inset:0;z-index:9999;width:1280px;height:720px';
    const r = window.__visualRenderer = new IsometricRenderer(canvas, window.__game.catalog.classes);
    r.setPresentation(window.__game.catalog.presentation);
    const state = structuredClone(window.__game.state);
    const before = JSON.stringify(state);
    const camera = { x: 10, y: 10, screenW: 1280, screenH: 720 };
    r.render(state, window.__game.localPlayerId, null, camera);
    await Promise.all([r.assets.get('test_floor').promise, r.assets.get('test_detail').promise]);
    r.render(state, window.__game.localPlayerId, null, camera);
    const textured = [...r.ctx.getImageData(400, 350, 100, 100).data];
    const noTexture = structuredClone(state); noTexture.map.segments[0].material_id = 'floor_hangar';
    r.render(noTexture, window.__game.localPlayerId, null, camera);
    const procedural = [...r.ctx.getImageData(400, 350, 100, 100).data];
    r.render(state, window.__game.localPlayerId, null, camera);
    const detail = r.scene.find(e => e.kind === 'prop' && e.entityId === 'console_a');
    const iso = worldToIso(detail.transform.x, detail.transform.y, camera);
    const detailPixel = [...r.ctx.getImageData(Math.round(iso.x), Math.round(iso.y - 48), 1, 1).data];
    const stable = JSON.stringify(state) === before;
    const presentation = structuredClone(window.__game.catalog.presentation);
    presentation.assets.test_detail.path = 'visual/absent.svg';
    r.setPresentation(presentation); r.render(state, window.__game.localPlayerId, null, camera);
    await r.assets.get('test_detail').promise;
    r.render(state, window.__game.localPlayerId, null, camera);
    // Direct physical pass excludes amber terminal HUD pixels from the fallback proof.
    r.ctx.clearRect(0, 0, 1280, 720);
    r.drawSceneEntity(r.ctx, detail, '', null, null, camera);
    const fallbackPixel = [...r.ctx.getImageData(Math.round(iso.x), Math.round(iso.y - 20), 1, 1).data];
    const failed = r.assets.get('test_detail').state;
    r.setPresentation(window.__game.catalog.presentation);
    await Promise.all([r.assets.get('test_floor').promise, r.assets.get('test_detail').promise]);
    r.render(state, window.__game.localPlayerId, null, camera);
    return { stable, textureDifferent: textured.some((v, i) => v !== procedural[i]), detailPixel, fallbackPixel, failed,
      ready: ['test_floor', 'test_detail'].map(id => r.assets.get(id).state),
      ids: r.scene.map(e => e.id), geometry: state.map.props.map(p => p.geometry) };
  });
  expect(result.stable).toBe(true);
  console.log('Stage10 texture/detail numeric evidence:', result);
  expect(result.textureDifferent).toBe(true);
  expect(result.ready).toEqual(['ready', 'ready']);
  expect(result.failed).toBe('error');
  expect(result.detailPixel[1]).toBeGreaterThan(result.detailPixel[0] * 2);
  expect(result.fallbackPixel).toEqual([30, 41, 59, 255]);
  expect(new Set(result.ids).size).toBe(result.ids.length);
  await page.locator('#visual-fixture-canvas').screenshot({ path: testInfo.outputPath('stage10-textured-detail.png') });
  await page.evaluate(() => { window.__visualRenderer.dispose(); document.getElementById('visual-fixture-canvas').remove(); });
  await page.request.post('/__test__/fixture', { data: { action: 'move', player_id: await page.evaluate(() => window.__game.localPlayerId), x: 12, y: 11 } });
  await page.waitForFunction(() => window.__game.state.players.find(p => p.id === window.__game.localPlayerId).x === 12);
  const targetPoint = await page.evaluate(async () => {
    const { worldToIso } = await import('/static/js/iso.js');
    const target = window.__game.state.map.puzzles.find(p => p.prop_id === 'console_a');
    return worldToIso(target.x, target.y, window.__game.camera);
  });
  await page.mouse.click(targetPoint.x, targetPoint.y);
  await expect(page.locator('#puzzle-modal')).toBeVisible();
  await expect(page.locator('#puzzle-title')).toContainText('Prop Console');
  expect(errors).toEqual([]);
});

test('long-wall front/back overlaps use segmented render bounds with numeric and pixel proof', async ({ page }, testInfo) => {
  await page.goto('/'); await page.waitForFunction(() => window.__game?.catalog);
  const result = await page.evaluate(async () => {
    const { IsometricRenderer } = await import('/static/js/renderer.js');
    const { buildScene } = await import('/static/js/scene.js');
    const { worldToIso } = await import('/static/js/iso.js');
    const canvas = document.createElement('canvas'); canvas.id = 'overlap-canvas'; canvas.width = 1280; canvas.height = 720;
    document.body.append(canvas); canvas.style.cssText = 'position:fixed;inset:0;z-index:9999;width:1280px;height:720px';
    const r = new IsometricRenderer(canvas); r.setPresentation(window.__game.catalog.presentation);
    await r.assets.get('test_actor').promise;
    const camera = { x: 1, y: 5, screenW: 1280, screenH: 720 };
    const s = { world_id: 'overlap', simulation_time: 0, map: { walls: [{ id: 'long', x: 0, y: 4, w: 10, h: 1, active: true,
      type: 'wall', material_id: 'test_red' }] }, players: [], enemies: [] };
    const check = y => {
      const actor = { id: 'actor', x: 1, y, visual_id: 'test_actor', angle: 0, radius: .42 };
      s.players = [actor];
      r.ctx.fillStyle = '#000000'; r.ctx.fillRect(0, 0, 1280, 720);
      const scene = buildScene(s, r.presentation, camera);
      for (const e of scene) r.drawSceneEntity(r.ctx, e, '', null, null, camera);
      const iso = worldToIso(actor.x, actor.y, camera);
      const pixel = [...r.ctx.getImageData(Math.round(iso.x), Math.round(iso.y - 8), 1, 1).data];
      return { pixel, order: scene.filter(e => e.kind === 'player' || e.bounds.x === 0).map(e => e.kind),
        parts: scene.filter(e => e.kind === 'wall').length };
    };
    const behind = check(3.2), front = check(5.6);
    const atlasCalls = [];
    const originalDrawImage = r.ctx.drawImage.bind(r.ctx);
    r.ctx.drawImage = (...args) => { atlasCalls.push(args.slice(1, 5)); originalDrawImage(...args); };
    s.map.walls = [];
    s.map.props = [{ id: 'table', x: 1, y: 4.5, orientation: 0, visual_id: 'table', material_id: 'test_red', geometry: {} }];
    const checkProp = y => {
      s.simulation_time = .5;
      s.players = [{ id: 'actor', x: 1, y, visual_id: 'test_actor', angle: Math.PI / 2, radius: .42 }];
      r.ctx.fillStyle = '#000000'; r.ctx.fillRect(0, 0, 1280, 720);
      const scene = buildScene(s, r.presentation, camera);
      for (const e of scene) r.drawSceneEntity(r.ctx, e, '', null, null, camera);
      const iso = worldToIso(1, y, camera);
      return { order: scene.map(e => e.kind), pixel: [...r.ctx.getImageData(Math.round(iso.x), Math.round(iso.y - 8), 1, 1).data] };
    };
    const propBehind = checkProp(3.55), propFront = checkProp(5.6);
    r.dispose();
    return { front, behind, propFront, propBehind, atlasCalls };
  });
  expect(result.front.parts).toBe(5);
  expect(result.front.order).toEqual(['wall', 'player']);
  expect(result.behind.order).toEqual(['player', 'wall']);
  expect(result.front.pixel.slice(0, 3)).toEqual([32, 224, 64]);
  expect(result.behind.pixel.slice(0, 3)).toEqual([224, 32, 32]);
  expect(result.propFront.order).toEqual(['prop', 'player']);
  expect(result.propBehind.order).toEqual(['player', 'prop']);
  expect(result.propFront.pixel.slice(0, 3)).toEqual([32, 224, 64]);
  expect(result.propBehind.pixel.slice(0, 3)).toEqual([224, 32, 32]);
  expect(result.atlasCalls).toEqual([[24, 40, 24, 40], [24, 40, 24, 40]]);
  await page.locator('#overlap-canvas').screenshot({ path: testInfo.outputPath('stage10-longwall-front.png') });
});

test('actual fog keeps occluded cells unknown, prior cells dim, current cells clear and resets world', async ({ page }, testInfo) => {
  await page.goto('/'); await page.waitForFunction(() => window.__game?.catalog);
  const result = await page.evaluate(async () => {
    const { IsometricRenderer } = await import('/static/js/renderer.js');
    const { worldToIso } = await import('/static/js/iso.js');
    const canvas = document.createElement('canvas'); canvas.width = 1280; canvas.height = 720; canvas.id = 'fog-proof';
    document.body.append(canvas); canvas.style.cssText = 'position:fixed;inset:0;z-index:9999;width:1280px;height:720px';
    const r = new IsometricRenderer(canvas);
    const camera = { x: 6, y: 5, screenW: 1280, screenH: 720 };
    const s = { world_id: 'fog-first', simulation_time: 50, map: { width: 20, height: 20, walls: [{ id: 'wall', x: 6, y: 0, w: .1, h: 20 }],
      segments: [{ x: 0, y: 0, w: 20, h: 20, theme: 'hangar', name: 'FOG', index: 0 }] },
      players: [{ id: 'p', x: 4, y: 5, hp: 100, max_hp: 100, angle: 0, sight_range: 12 }], enemies: [] };
    const sample = (x, y) => { const iso = worldToIso(x, y, camera); return r.fogCtx.getImageData(Math.round(iso.x), Math.round(iso.y), 1, 1).data[3]; };
    r.render(s, 'p', null, camera);
    const current = sample(5, 5), unknown = sample(9, 5), hiddenState = r.visibilityAt(9, 5);
    s.players = []; r.render(s, '', null, camera);
    const explored = sample(5, 5), exploredState = r.visibilityAt(5, 5);
    s.world_id = 'fog-second'; r.render(s, '', null, camera);
    const reset = r.visibilityAt(5, 5);
    r.dispose();
    return { current, unknown, explored, hiddenState, exploredState, reset };
  });
  expect(result.current).toBe(0);
  expect(result.unknown).toBeGreaterThan(245);
  expect(result.explored).toBeGreaterThan(140);
  expect(result.explored).toBeLessThan(175);
  expect(result.hiddenState).toBe('unknown');
  expect(result.exploredState).toBe('explored');
  expect(result.reset).toBe('unknown');
  await page.locator('#fog-proof').screenshot({ path: testInfo.outputPath('stage10-fog-numeric-proof.png') });
});

test('raised walls remain visible from every side without clearing hidden floor fog', async ({ page }) => {
  await page.goto('/'); await page.waitForFunction(() => window.__game?.catalog);
  const result = await page.evaluate(async () => {
    const { IsometricRenderer } = await import('/static/js/renderer.js');
    const { worldToIso } = await import('/static/js/iso.js');
    const canvas = document.createElement('canvas'); canvas.width = 1280; canvas.height = 720;
    const renderer = new IsometricRenderer(canvas);
    renderer.setPresentation(window.__game.catalog.presentation);
    renderer.drawPlayer = () => {};
    renderer.drawActorMarker = () => {};
    const camera = { x: 9, y: 9, screenW: 1280, screenH: 720 };
    const state = { world_id: 'directional-wall', simulation_time: 0,
      map: { width: 20, height: 20,
        segments: [{ x: 0, y: 0, w: 20, h: 20, theme: 'hangar', name: 'Walls', index: 0 }],
        walls: [{ id: 'wall', x: 8, y: 8, w: 2, h: .6, type: 'wall', material_id: 'test_red' }] },
      players: [], enemies: [] };
    const pixel = (x, y, height = 0, ctx = renderer.ctx) => {
      const projected = worldToIso(x, y, camera);
      return [...ctx.getImageData(Math.round(projected.x), Math.round(projected.y - height), 1, 1).data];
    };
    const samples = [];
    for (const [x, y] of [[11.5, 8.3], [6.5, 8.3], [9, 11.5], [9, 5], [11.5, 11.5], [6.5, 5]]) {
      renderer.resetExplored();
      state.players = [{ id: 'player', x, y, hp: 100, max_hp: 100, angle: 0, sight_range: 12 }];
      renderer.render(state, 'player', null, camera);
      samples.push({ player: [x, y], roof: pixel(9, 8.3, 42), face: pixel(10, 8.3, 21),
        roofFog: pixel(9, 8.3, 42, renderer.fogCtx)[3] });
    }
    renderer.resetExplored();
    state.map.walls = [{ id: 'barrier', x: 8, y: 0, w: .2, h: 20, type: 'wall' },
      { id: 'hidden-wall', x: 12, y: 8, w: 2, h: .6, type: 'wall', material_id: 'test_red' }];
    state.players = [{ id: 'player', x: 6, y: 10, hp: 100, max_hp: 100, angle: 0, sight_range: 12 }];
    renderer.render(state, 'player', null, camera);
    const hiddenFloor = pixel(11, 10, 0, renderer.fogCtx)[3];
    const unknownCell = renderer.visibilityAt(11, 10);
    const hiddenWall = pixel(13, 8.3, 42, renderer.fogCtx)[3];
    state.map.walls[0].active = false;
    state.map.props = [{ id: 'opaque-prop', x: 8, y: 0, active: true,
      geometry: { sight: { x: 8, y: 0, w: .2, h: 20 } } }];
    renderer.resetExplored();
    renderer.render(state, 'player', null, camera);
    const propOccludedWall = pixel(13, 8.3, 42, renderer.fogCtx)[3];
    renderer.resetExplored();
    state.map.props = [];
    state.map.walls[1].active = false;
    renderer.render(state, 'player', null, camera);
    const inactiveWallCount = renderer.scene.filter(entity => entity.kind === 'wall').length;
    renderer.dispose();
    return { samples, hiddenFloor, unknownCell, hiddenWall, propOccludedWall, inactiveWallCount };
  });
  for (const sample of result.samples) {
    expect(sample.roof, `wall roof from player ${sample.player}`).toEqual([224, 32, 32, 255]);
    expect(sample.roofFog, `raised visibility from player ${sample.player}`).toBe(0);
  }
  expect(result.samples[0].face).toEqual([224, 32, 32, 255]);
  expect(result.hiddenFloor).toBeGreaterThan(245);
  expect(result.unknownCell).toBe('unknown');
  expect(result.hiddenWall).toBeGreaterThan(245);
  expect(result.propOccludedWall).toBeGreaterThan(245);
  expect(result.inactiveWallCount).toBe(0);
});

test('explored wall sections stay uniformly dim outside shared sight and reset for a new world', async ({ page }) => {
  await page.goto('/'); await page.waitForFunction(() => window.__game?.catalog);
  const result = await page.evaluate(async () => {
    const { IsometricRenderer } = await import('/static/js/renderer.js');
    const { worldToIso } = await import('/static/js/iso.js');
    const canvas = document.createElement('canvas'); canvas.width = 1280; canvas.height = 720;
    const renderer = new IsometricRenderer(canvas);
    renderer.setPresentation(window.__game.catalog.presentation);
    renderer.drawPlayer = () => {};
    renderer.drawActorMarker = () => {};
    const camera = { x: 9, y: 9, screenW: 1280, screenH: 720 };
    const state = { world_id: 'wall-memory', simulation_time: 0,
      map: { width: 40, height: 40,
        segments: [{ x: 0, y: 0, w: 40, h: 40, theme: 'hangar', name: 'Memory', index: 0 }],
        walls: [{ id: 'seen', x: 8, y: 8, w: 4, h: .6, type: 'wall', material_id: 'test_red' },
          { id: 'hidden', x: 8, y: 4, w: 4, h: .6, type: 'wall', material_id: 'test_red' }] },
      players: [{ id: 'player', x: 10, y: 11.5, hp: 100, max_hp: 100, angle: 0, sight_range: 12 }], enemies: [] };
    const sample = (x, y, height = 42) => {
      const point = worldToIso(x, y, camera);
      const px = Math.round(point.x), py = Math.round(point.y - height);
      return { fog: renderer.fogCtx.getImageData(px, py, 1, 1).data[3], pixel: [...renderer.ctx.getImageData(px, py, 1, 1).data] };
    };
    renderer.render(state, 'player', null, camera);
    const current = sample(9, 8.3), hidden = sample(9, 4.3);
    state.players[0].x = 30; state.players[0].y = 30;
    renderer.render(state, 'player', null, camera);
    const remembered = sample(9, 8.3), seam = sample(10, 8.3);
    const hiddenStill = sample(9, 4.3);
    state.players.push({ id: 'teammate', x: 10, y: 11.5, hp: 100, max_hp: 100, angle: 0, sight_range: 12 });
    renderer.render(state, 'player', null, camera);
    const sharedCurrent = sample(9, 8.3);
    state.players.pop();
    state.map.walls[0].active = false;
    renderer.render(state, 'player', null, camera);
    const removedFromScene = !renderer.scene.some(entity => entity.entityId === 'seen');
    state.map.walls[0].active = true;
    state.world_id = 'new-wall-memory';
    renderer.render(state, 'player', null, camera);
    const reset = sample(9, 8.3);
    const resetMemory = renderer.exploredWalls.size;
    renderer.dispose();
    return { current, hidden, remembered, seam, hiddenStill, sharedCurrent, removedFromScene, reset, resetMemory };
  });
  expect(result.current.fog).toBe(0);
  expect(result.current.pixel).toEqual([224, 32, 32, 255]);
  expect(result.hidden.fog).toBeGreaterThan(245);
  expect(result.remembered.fog).toBeGreaterThan(140);
  expect(result.remembered.fog).toBeLessThan(175);
  expect(result.remembered.pixel[0]).toBeLessThan(result.current.pixel[0]);
  expect(result.remembered.pixel[0]).toBeGreaterThan(50);
  expect(Math.abs(result.seam.fog - result.remembered.fog)).toBeLessThanOrEqual(1);
  expect(result.hiddenStill.fog).toBeGreaterThan(245);
  expect(result.sharedCurrent.fog).toBe(0);
  expect(result.removedFromScene).toBe(true);
  expect(result.reset.fog).toBeGreaterThan(245);
  expect(result.resetMemory).toBe(0);
});

test('wall interiors seen through an offset opening enter exploration memory', async ({ page }) => {
  await page.goto('/'); await page.waitForFunction(() => window.__game?.catalog);
  const result = await page.evaluate(async () => {
    const { IsometricRenderer } = await import('/static/js/renderer.js');
    const { worldToIso } = await import('/static/js/iso.js');
    const canvas = document.createElement('canvas'); canvas.width = 1280; canvas.height = 720;
    const renderer = new IsometricRenderer(canvas);
    renderer.setPresentation(window.__game.catalog.presentation);
    renderer.drawPlayer = () => {};
    renderer.drawActorMarker = () => {};
    const camera = { x: 10, y: 10, screenW: 1280, screenH: 720 };
    const state = { world_id: 'offset-opening', map: { width: 40, height: 40,
      segments: [{ x: 0, y: 0, w: 40, h: 40, theme: 'hangar', name: 'Opening', index: 0 }],
      walls: [{ id: 'target', x: 8, y: 4, w: 2, h: .6, type: 'wall', material_id: 'test_red' },
        { id: 'left-jamb', x: 0, y: 8, w: 8.74, h: .2, type: 'wall' },
        { id: 'right-jamb', x: 9.94, y: 8, w: 30.06, h: .2, type: 'wall' }] },
      players: [{ id: 'player', x: 10, y: 16, hp: 100, max_hp: 100, angle: 0, sight_range: 14 }], enemies: [] };
    const point = worldToIso(9.15, 4.3, camera);
    const fog = () => renderer.fogCtx.getImageData(Math.round(point.x), Math.round(point.y - 42), 1, 1).data[3];
    renderer.render(state, 'player', null, camera);
    const current = fog();
    const rememberedId = renderer.scene.find(e => e.entityId === 'target').id;
    const discovered = renderer.exploredWalls.has(rememberedId);
    state.players[0].x = 35; state.players[0].y = 35;
    renderer.render(state, 'player', null, camera);
    const remembered = fog();
    renderer.dispose();
    return { current, discovered, remembered };
  });
  expect(result.discovered).toBe(true);
  expect(result.current).toBe(0);
  expect(result.remembered).toBeGreaterThan(140);
  expect(result.remembered).toBeLessThan(175);
});
