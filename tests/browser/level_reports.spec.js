import { test, expect } from '@playwright/test';

test('a player reports an uncompleted level, returns the squad, and retains the broken map reason', async ({ browser, request }) => {
  const context = await browser.newContext();
  const leader = await context.newPage();
  const peer = await context.newPage();
  try {
    await leader.goto('/');
    await leader.waitForFunction(() => window.__game?.localPlayerId);
    await peer.goto('/');
    await peer.waitForFunction(() => window.__game?.localPlayerId);
    await request.post('/__test__/fixture', { data: { action: 'install_report' } });
    await peer.waitForFunction(() => window.__game.state.map?.can_report_broken);
    const levelId = await peer.evaluate(() => window.__game.state.map.level_id);
    await peer.locator('#report-level-btn').click();
    await expect(peer.locator('#level-report-modal')).toBeVisible();
    await peer.locator('#level-report-reason').fill('Door never opens and blocks extraction.');
    await peer.locator('#submit-level-report').click();
    await leader.waitForFunction(() => window.__game.state.phase === 'lobby' && !window.__game.state.map);
    await peer.waitForFunction(() => window.__game.state.phase === 'lobby' && !window.__game.state.map);
    const broken = await peer.evaluate(id => window.__game.state.levels.find(l => l.level_id === id), levelId);
    expect(broken.broken).toBe(true);
    expect(broken.completed).toBe(false);
    expect(broken.tested).toBe(false);
    expect(broken.unlocked).toBe(false);
    expect(broken.failure_reports[0].reason).toBe('Door never opens and blocks extraction.');
    await expect(peer.locator('#level-report-modal')).toBeHidden();
    await expect(peer.locator('#mission-grid')).toContainText('BROKEN');
    await expect(peer.locator('#mission-grid')).toContainText('Door never opens and blocks extraction.');
  } finally {
    await context.close();
    await request.post('/__test__/fixture', { data: { action: 'restore_report' } });
  }
});

test('defeated players can report through the end screen without automatically marking a loss broken', async ({ page, request }) => {
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.localPlayerId);
  await request.post('/__test__/fixture', { data: { action: 'install_report' } });
  await request.post('/__test__/fixture', { data: { action: 'hub_defeat' } });
  await page.waitForFunction(() => window.__game.state.phase === 'defeat');
  expect(await page.evaluate(() => window.__game.state.levels.find(l => l.level_id === 'outpost_101').broken)).toBe(false);
  await page.locator('#end-report-level-btn').click();
  await page.locator('#level-report-reason').fill('Spawn traps the squad inside collision geometry.');
  await page.locator('#submit-level-report').click();
  await page.waitForFunction(() => window.__game.state.phase === 'lobby' && !window.__game.state.map);
  await expect(page.locator('#mission-grid')).toContainText('Spawn traps the squad inside collision geometry.');
  await request.post('/__test__/fixture', { data: { action: 'restore_report' } });
});
