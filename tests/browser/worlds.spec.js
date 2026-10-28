import { test, expect } from '@playwright/test';

async function join(page) {
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.localPlayerId);
  if (await page.evaluate(() => ['lobby', 'prep'].includes(window.__game.state.phase))) await page.locator('.class-card').first().click();
  await expect(page.locator('#class-modal')).toBeHidden();
}

async function fixture(page, action, extra = {}) {
  const response = await page.request.post('/__test__/fixture', { data: { action, ...extra } });
  expect(response.ok()).toBe(true);
  return response.json();
}

async function clickConsole(page, type) {
  await page.bringToFront();
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  const point = await page.evaluate(async type => {
    const { worldToIso } = await import('/static/js/iso.js');
    const target = window.__game.state.map.interactions.find(t => t.type === type);
    return worldToIso(target.x, target.y, window.__game.camera);
  }, type);
  await page.mouse.click(point.x, point.y);
  await expect(page.locator('#service-modal')).toBeVisible();
}

async function move(page, x, y) {
  await page.bringToFront();
  await page.evaluate(() => { document.activeElement.blur(); window.dispatchEvent(new Event('focus')); });
  for (const [axis, destination, positive, negative] of [['x', x, ['s', 'd'], ['w', 'a']], ['y', y, ['s', 'a'], ['w', 'd']]]) {
    if (await page.evaluate(() => ['victory', 'defeat'].includes(window.__game.state.phase))) break;
    const start = await page.evaluate(axis => window.__game.state.players.find(p => p.id === window.__game.localPlayerId)[axis], axis);
    if (Math.abs(start - destination) < .2) continue;
    const sign = destination > start ? 1 : -1;
    const keys = sign > 0 ? positive : negative;
    for (const key of keys) await page.keyboard.down(key);
    await page.waitForFunction(({ axis, destination, sign }) => ['victory', 'defeat'].includes(window.__game.state.phase) || sign * (window.__game.state.players.find(p => p.id === window.__game.localPlayerId)[axis] - destination) >= -.15, { axis, destination, sign });
    for (const key of keys) await page.keyboard.up(key);
    await page.waitForFunction(() => { const p = window.__game.state.players.find(p => p.id === window.__game.localPlayerId); return ['victory', 'defeat'].includes(window.__game.state.phase) || p.vx === 0 && p.vy === 0; });
  }
}

test('actual hub movement prop and perimeter collisions stash and noncombat authority', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await join(page);
  const entered = await fixture(page, 'enable_hub');
  await page.waitForFunction(id => window.__game.state.world_id === id, entered.world_id);
  await expect(page.locator('#game-screen')).toBeVisible();
  await expect(page.locator('#lobby-screen')).toBeHidden();
  await expect(page.locator('#hud-ammo-text')).toHaveText('NONCOMBAT');
  expect(await page.evaluate(() => ({ ship: 'spaceship' in window.__game.state.map, ext: 'extraction_zone' in window.__game.state.map }))).toEqual({ ship: false, ext: false });
  const id = await page.evaluate(() => window.__game.localPlayerId);
  await fixture(page, 'move', { player_id: id, x: 7, y: 8.5 });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.keyboard.down('w'); await page.keyboard.down('d');
  await page.waitForFunction(() => { const p = window.__game.state.players.find(p => p.id === window.__game.localPlayerId); return p.y < 7.9; });
  await fixture(page, 'advance', { seconds: .5 });
  await page.keyboard.up('w'); await page.keyboard.up('d');
  const y = await page.evaluate(() => window.__game.state.players.find(p => p.id === window.__game.localPlayerId).y);
  expect(y).toBeGreaterThanOrEqual(7.769);
  await clickConsole(page, 'stash');
  const medkit = page.locator('.hub-stock .inv-slot-row').filter({ hasText: 'Medkit' });
  const initial = Number((await medkit.innerText()).match(/x(\d+)/)[1]);
  await medkit.getByRole('button', { name: 'Take' }).click();
  await expect(page.locator('.hub-deposit')).toHaveCount(1);
  await page.locator('.hub-deposit').click();
  await expect(page.locator('.hub-deposit')).toHaveCount(0);
  await expect(medkit).toContainText(`x${initial}`);
  await page.locator('#close-service-btn').click();
  await fixture(page, 'move', { player_id: id, x: 1, y: 3 });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.keyboard.down('w'); await page.keyboard.down('a');
  await page.waitForFunction(() => window.__game.state.players.find(p => p.id === window.__game.localPlayerId).x < .8);
  await fixture(page, 'advance', { seconds: .5 });
  await page.keyboard.up('w'); await page.keyboard.up('a');
  expect(await page.evaluate(() => window.__game.state.players.find(p => p.id === window.__game.localPlayerId).x)).toBeGreaterThanOrEqual(.719);
  const replies = [];
  // Direct hostile commands bypass UI suppression but still hit server policy.
  await page.evaluate(() => { const socket = new WebSocket(`ws://${location.host}/ws`); window.__hostileProbe = socket; socket.onmessage = e => (window.__probeReplies ||= []).push(JSON.parse(e.data)); });
  await page.waitForFunction(() => window.__probeReplies?.some(r => r.type === 'welcome'));
  await page.evaluate(() => ['reload', 'skill'].forEach(type => window.__hostileProbe.send(JSON.stringify({ type }))));
  await page.waitForFunction(() => window.__probeReplies.filter(r => r.type === 'command_error').length === 2);
  replies.push(...await page.evaluate(() => window.__probeReplies.filter(r => r.type === 'command_error')));
  expect(replies).toHaveLength(2);
  await page.evaluate(() => window.__hostileProbe.close());
  expect(await page.evaluate(() => window.__game.state.mission_time)).toBe(0);
  await fixture(page, 'noncombat_active');
  await page.waitForFunction(() => window.__game.state.phase === 'active' && window.__game.state.world_kind === 'mission');
  await expect(page.locator('#game-screen')).toBeVisible();
  await expect(page.locator('#hud-objective-text')).toHaveText('WORLD OBJECTIVES COMPLETE');
  expect(await page.evaluate(() => window.__game.state.map.extraction_zone)).toBeUndefined();
  expect(errors).toEqual([]);
  await fixture(page, 'disable_hub');
});

test('two actual clients select prepare depart victory and defeat return late join disconnect reconnect', async ({ browser }) => {
  test.setTimeout(60000);
  const context = await browser.newContext();
  const leader = await context.newPage();
  const peer = await context.newPage();
  const errors = [];
  leader.on('pageerror', error => errors.push(error.message));
  peer.on('pageerror', error => errors.push(error.message));
  try {
    await join(leader); await join(peer);
    const entry = await fixture(leader, 'enable_hub');
    await leader.waitForFunction(id => window.__game.state.world_id === id, entry.world_id);
    await peer.waitForFunction(id => window.__game.state.world_id === id, entry.world_id);
    await move(leader, 7.5, 8.5);
    await clickConsole(leader, 'stash');
    const medkit = leader.locator('.hub-stock .inv-slot-row').filter({ hasText: 'Medkit' });
    const initial = Number((await medkit.innerText()).match(/x(\d+)/)[1]);
    await medkit.getByRole('button', { name: 'Take' }).click();
    await expect(leader.locator('.hub-deposit')).toHaveCount(1);
    await leader.locator('#close-service-btn').click();
    for (const outcome of ['victory', 'defeat']) {
      if (outcome === 'defeat') {
        await move(leader, 7.5, 8.5); await clickConsole(leader, 'stash');
        await medkit.getByRole('button', { name: 'Take' }).click();
        await expect(leader.locator('.hub-deposit')).toHaveCount(1);
        await leader.locator('#close-service-btn').click();
      }
      await move(leader, 10.5, 8.5);
      await clickConsole(leader, 'mission_selection');
      await leader.locator('.hub-level[data-level-id="outpost_101"]').click();
      await expect(leader.locator('.hub-level[data-level-id="outpost_101"]')).toContainText('Selected');
      await leader.getByRole('button', { name: 'Prepare Mission', exact: true }).click();
      await leader.waitForFunction(() => window.__game.state.phase === 'prep');
      await expect(leader.locator('#game-screen')).toBeVisible();
      await expect(leader.locator('#prep-screen')).toBeHidden();
      await clickConsole(leader, 'mission_selection');
      await leader.locator('.hub-ready').click();
      await expect(peer.locator('#hub-ready-btn')).toBeEnabled();
      await peer.locator('#hub-ready-btn').click();
      await expect(leader.locator('.hub-depart')).toBeEnabled();
      const hubWorld = await leader.evaluate(() => window.__game.state.world_id);
      await leader.locator('.hub-depart').click();
      await leader.waitForFunction(id => window.__game.state.world_kind === 'mission' && window.__game.state.world_id !== id, hubWorld);
      const mission = await fixture(leader, 'hub_mission');
      await peer.waitForFunction(id => window.__game.state.world_id === id && window.__game.state.phase === 'active', mission.world_id);
      const missionLate = await context.newPage();
      await join(missionLate);
      await missionLate.waitForFunction(id => window.__game.state.world_id === id, mission.world_id);
      await expect(missionLate.locator('#game-screen')).toBeVisible();
      await missionLate.close();
      await leader.waitForFunction(() => window.__game.state.players.length === 2);
      if (outcome === 'victory') {
        await move(leader, 11, 11); await move(peer, 11, 11);
        await expect(leader.locator('#end-title')).toHaveText('EXTRACTION SUCCESSFUL');
      } else {
        await fixture(leader, 'hub_defeat');
        await expect(leader.locator('#end-title')).toHaveText('SQUAD WIPE — MISSION FAILED');
      }
      await leader.locator('#return-lobby-btn').click();
      await leader.waitForFunction(id => window.__game.state.world_kind === 'hub' && window.__game.state.world_id !== id, mission.world_id);
      await expect(leader.locator('#end-banner')).toBeHidden();
      await expect(leader.locator('#service-modal')).toBeHidden();
      expect(await leader.evaluate(() => ({ sounds: window.__game.state.audio_events.length, shots: window.__game.state.projectiles.length, missionTime: window.__game.state.mission_time,
        hp: window.__game.state.players.every(p => p.hp === p.max_hp && !p.downed), ready: window.__game.state.players.some(p => p.ready) }))).toEqual({ sounds: 0, shots: 0, missionTime: 0, hp: true, ready: false });
      await move(leader, 7.5, 8.5); await clickConsole(leader, 'stash');
      await expect(medkit).toContainText(`x${initial}`);
      await leader.locator('#close-service-btn').click();
    }
    const late = await context.newPage();
    await join(late);
    expect(await late.evaluate(() => window.__game.state.world_kind)).toBe('hub');
    await move(leader, 7.5, 8.5); await clickConsole(leader, 'stash');
    await leader.locator('.stash-recipient').selectOption(await late.evaluate(() => window.__game.localPlayerId));
    await medkit.getByRole('button', { name: 'Take' }).click();
    await expect(leader.locator('.hub-deposit')).toHaveCount(1);
    await late.close();
    await expect(leader.locator('.hub-deposit')).toHaveCount(0);
    await expect(medkit).toContainText(`x${initial}`);
    const oldPeer = await peer.evaluate(() => window.__game.localPlayerId);
    await peer.reload();
    await peer.waitForFunction(id => window.__game.localPlayerId && window.__game.localPlayerId !== id, oldPeer);
    await expect(peer.locator('#game-screen')).toBeVisible();
    await peer.locator('.class-card').first().click();
    expect(errors).toEqual([]);
    await fixture(leader, 'disable_hub');
  } finally { await context.close(); }
});
