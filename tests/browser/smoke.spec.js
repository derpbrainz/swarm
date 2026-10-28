import { test, expect } from '@playwright/test';

test('real browser loads ES modules and connects to the game', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('#class-modal')).toBeVisible();
  await page.waitForFunction(() => window.__game?.localPlayerId);
  expect(errors).toEqual([]);
});

test('class UI consumes the same catalog values and does not receive private answers', async ({ page }) => {
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.catalog);
  const config = await page.evaluate(() => window.__game.catalog);
  const vanguard = config.classes.vanguard;
  const card = page.locator('.class-card').filter({ hasText: 'Frontline Breacher' });
  await expect(card).toContainText(`${vanguard.carry_capacity} WT`);
  await expect(card).toContainText(`${vanguard.weapon.damage} damage`);
  await expect(card).toContainText(`Radius: ${vanguard.skill.radius}`);
  expect(config.segments).toBeUndefined();
  await page.evaluate(() => {
    window.__game.send({ type: 'select_class', class_id: 'vanguard' });
    window.__game.send({ type: 'select_mission', level_id: 'outpost_101', prepare: true });
    window.__game.send({ type: 'toggle_ready', ready: true });
  });
  await page.waitForFunction(() => window.__game.state.map);
  const puzzles = await page.evaluate(() => window.__game.state.map.puzzles);
  for (const puzzle of puzzles) {
    if (puzzle.type === 'power_reroute') expect(puzzle.data.target).toBeUndefined();
    if (puzzle.type === 'signal_decrypt') expect(puzzle.data.target_sequence.length).toBeGreaterThan(0);
  }
});

test('records representative Canvas rendering costs', async ({ page }) => {
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.localPlayerId);
  await page.evaluate(() => {
    window.__game.send({ type: 'select_class', class_id: 'vanguard' });
    window.__game.send({ type: 'select_mission', level_id: 'outpost_101', prepare: true });
  });
  await page.waitForFunction(() => window.__game.state.phase === 'prep');
  await page.evaluate(() => window.__game.send({ type: 'toggle_ready', ready: true }));
  await page.waitForFunction(() => window.__game.state.map);
  const measurement = await page.evaluate(async () => {
    const { IsometricRenderer } = await import('/static/js/renderer.js');
    const config = await (await fetch('/api/config')).json();
    const canvas = document.createElement('canvas');
    canvas.width = 1280;
    canvas.height = 720;
    const renderer = new IsometricRenderer(canvas, config.classes);
    renderer.setItemsConfig(config.items);
    renderer.setPresentation(config.presentation);
    const state = window.__game.state;
    const localId = window.__game.localPlayerId;
    const player = state.players.find(p => p.id === localId);
    const camera = { x: player.x, y: player.y, screenW: 1280, screenH: 720 };
    const times = [];
    for (let i = 0; i < 60; i++) {
      const start = performance.now();
      renderer.render(state, localId, player, camera);
      times.push(performance.now() - start);
    }
    times.sort((a, b) => a - b);
    renderer.dispose();
    return { medianMs: times[30], p95Ms: times[56], snapshotBytes: JSON.stringify(state).length };
  });
  console.log('Canvas baseline at 1280x720:', measurement);
  expect(Number.isFinite(measurement.p95Ms)).toBe(true);
});

test('player names remain literal text in another client and preparation', async ({ browser }) => {
  const context = await browser.newContext();
  const leader = await context.newPage();
  const peer = await context.newPage();
  try {
    await leader.goto('/');
    await leader.waitForFunction(() => window.__game?.localPlayerId);
    await peer.goto('/');
    await peer.waitForFunction(() => window.__game?.localPlayerId);
    await leader.evaluate(() => {
      window.__game.send({ type: 'set_name', name: '<svg onload=alert()>' });
    });
    await expect(peer.locator('#player-list')).toContainText('<svg onload=alert()>');
    await expect(peer.locator('#player-list svg')).toHaveCount(0);
    await leader.evaluate(() => {
      window.__game.send({ type: 'select_mission', level_id: 'outpost_101', prepare: true });
    });
    await expect(peer.locator('#prep-screen')).toContainText('<svg onload=alert()>');
    await expect(peer.locator('#prep-screen svg[onload]')).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test('actual Web Audio decodes assets, retries gesture/failure, deduplicates and disposes', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.catalog);
  let failedOnce = false;
  await page.route('**/assets/audio/music_combat.mp3', async route => {
    if (!failedOnce) { failedOnce = true; await route.fulfill({ status: 503, body: 'deliberate retry fixture' }); }
    else await route.continue();
  });
  await page.evaluate(async () => {
    const { AudioManager } = await import('/static/js/audio.js');
    const manager = window.__audioTest = new AudioManager();
    manager.setCatalog(window.__game.catalog);
    manager.ensureContext();
    await manager.audioContext.suspend();
    manager.consumeSnapshot({ world_id: 'audio-fixture', simulation_time: 1, audio_events: [], ambience_emitters: [],
      music: { state: 'combat', bpm: 118, start_t: 0, simulation_time: 1 } }, { x: 0, y: 0 });
    await manager.initPromise;
  });
  expect(await page.evaluate(() => ({ desired: window.__audioTest.desiredMusic.state, applied: window.__audioTest.currentMusicState,
    failure: window.__audioTest.failures.has('music_combat'), exploreDecoded: window.__audioTest.buffers.get('music_explore').duration > 0 })))
    .toEqual({ desired: 'combat', applied: null, failure: true, exploreDecoded: true });
  await page.locator('#class-modal .class-title').first().click();
  await page.waitForFunction(() => window.__audioTest.currentMusicState === 'combat' && window.__audioTest.initialized);
  const result = await page.evaluate(() => {
    const m = window.__audioTest;
    const snap = { world_id: 'audio-fixture', simulation_time: 2, ambience_emitters: [],
      music: { state: 'combat', bpm: 118, start_t: 0, simulation_time: 2 },
      audio_events: [{ event_id: 'audio-fixture:1', sequence: 1, world_id: 'audio-fixture', timestamp: 2, expires_at: 2.75,
        sound_type: 'weapon_fire_pistol', source_id: 'fixture', x: 0, y: 0 }] };
    m.consumeSnapshot(snap, { x: 0, y: 0 });
    const voice = [...m.activeVoices.values()][0];
    m.consumeSnapshot(snap, { x: 0, y: 0 });
    const dedup = [...m.activeVoices.values()][0] === voice;
    snap.ambience_emitters = [{ id: 'vent', world_id: 'audio-fixture', active: true, sound_type: 'ambient_vent', x: 0, y: 0 }];
    m.consumeSnapshot(snap, { x: 0, y: 0 });
    const ambience = m.activeAmbiences.size;
    m.consumeSnapshot({ world_id: null, simulation_time: 2, audio_events: [], ambience_emitters: [], music: { state: 'lobby' } }, { x: 0, y: 0 });
    const cleared = m.activeVoices.size === 0 && m.activeAmbiences.size === 0 && !m.musicLayers.combat;
    const decoded = m.buffers.size;
    m.dispose();
    return { dedup, ambience, cleared, decoded, disposed: m.disposed, buffers: m.buffers.size };
  });
  expect(result.dedup).toBe(true);
  expect(result.ambience).toBe(1);
  expect(result.cleared).toBe(true);
  expect(result.decoded).toBeGreaterThan(10);
  expect(result.disposed).toBe(true);
  expect(result.buffers).toBe(0);
  expect(errors).toEqual([]);
});

test('actual application mission to lobby transition resets the singleton audio consumer', async ({ page }) => {
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.catalog);
  await page.locator('#class-modal .class-title').first().click();
  await page.evaluate(() => {
    window.__game.send({ type: 'select_mission', level_id: 'outpost_101', prepare: true });
    window.__game.send({ type: 'toggle_ready', ready: true });
  });
  await page.waitForFunction(() => window.__game.state.map);
  await page.waitForFunction(async () => {
    const { audioManager } = await import('/static/js/audio.js');
    return audioManager.currentMusicState === 'explore' && audioManager.musicLayers.explore && audioManager.worldId === window.__game.state.world_id;
  });
  await page.evaluate(() => window.__game.send({ type: 'return_lobby' }));
  await page.waitForFunction(async () => {
    const { audioManager: m } = await import('/static/js/audio.js');
    return window.__game.state.phase === 'lobby' && m.worldId === null && m.currentMusicState === 'lobby' &&
      m.activeVoices.size === 0 && m.activeAmbiences.size === 0 && !m.musicLayers.explore && !m.musicLayers.combat;
  });
});
