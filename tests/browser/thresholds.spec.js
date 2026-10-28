import { test, expect } from '@playwright/test';

test('approaching an automatic door opens it once and permits keyboard crossing', async ({ page, request }) => {
  const errors = [];
  const doorEvents = new Set();
  page.on('pageerror', error => errors.push(error.message));
  page.on('websocket', socket => socket.on('framereceived', ({ payload }) => {
    const state = JSON.parse(String(payload));
    for (const sound of state.audio_events || []) {
      if (sound.source_id === 'fixture-proximity') doorEvents.add(sound.event_id);
    }
  }));
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.localPlayerId);
  await request.post('/__test__/fixture', { data: { action: 'install_threshold' } });
  await page.waitForFunction(() => window.__game.state.map?.walls.some(w => w.id === 'fixture-proximity'));
  const closed = await page.evaluate(() => window.__game.state.map.walls.find(w => w.id === 'fixture-proximity'));
  expect(closed.active).toBe(true);
  expect(closed.mode).toBe('proximity');
  expect(closed.trigger_radius).toBe(2.4);
  await page.keyboard.down('d');
  await page.keyboard.down('s');
  try {
    await page.waitForFunction(() => {
      const game = window.__game;
      return game.state.players.find(p => p.id === game.localPlayerId).x > 13.5;
    }, null, { timeout: 10000 });
  } finally {
    await page.keyboard.up('d');
    await page.keyboard.up('s');
  }
  const opened = await page.evaluate(() => {
    const map = window.__game.state.map;
    const wall = map.walls.find(w => w.id === 'fixture-proximity');
    return { flags: ['active', 'blocks_movement', 'blocks_projectile', 'blocks_sight'].map(key => wall[key]),
      state: wall.state, revision: map.geometry_revision,
      puzzles: map.puzzles.length };
  });
  expect(opened.flags).toEqual([false, false, false, false]);
  expect(opened.state).toBe('open');
  expect(opened.revision).toBe(1);
  expect(doorEvents.size).toBe(1);
  expect(opened.puzzles).toBe(0);
  await request.post('/__test__/fixture', { data: { action: 'move',
    player_id: await page.evaluate(() => window.__game.localPlayerId), x: 22, y: 10 } });
  await page.waitForFunction(() => {
    const game = window.__game;
    return game.state.players.find(p => p.id === game.localPlayerId).x >= 22;
  });
  expect(await page.evaluate(() => window.__game.state.map.walls.find(w => w.id === 'fixture-proximity').active)).toBe(false);
  expect(errors).toEqual([]);
});
