import { test, expect } from '@playwright/test';

async function install(page) {
  const response = await page.request.post('/__test__/fixture', { data: { action: 'install_encounter' } });
  expect(response.ok()).toBe(true);
  await page.waitForFunction(() => window.__game.state.map?.puzzles[0]?.id === 'fixture-qte');
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  const point = await page.evaluate(async () => {
    const { worldToIso } = await import('/static/js/iso.js');
    return worldToIso(12, 10, window.__game.camera);
  });
  await page.mouse.click(point.x, point.y);
  await expect(page.locator('#puzzle-title')).toHaveText('Timed Input Fixture');
}

test('actual QTE deadline input valid completion and authoritative replay error', async ({ page }) => {
  const errors = [];
  const sent = [];
  const replies = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.routeWebSocket('**/ws', route => {
    const server = route.connectToServer();
    route.onMessage(message => { sent.push(JSON.parse(message)); server.send(message); });
    server.onMessage(message => { replies.push(JSON.parse(message)); route.send(message); });
  });
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.localPlayerId);
  await page.locator('.class-card').first().click();
  await install(page);
  await page.getByRole('button', { name: 'Start attempt' }).click();
  await expect(page.locator('.qte-deadline')).toContainText('Server deadline');
  await expect(page.locator('.qte-deadline')).toContainText('active');
  await expect(page.getByRole('button', { name: 'Input ACK' })).toBeVisible();
  const session = await page.evaluate(() => ({ ...window.__game.activePuzzle.session }));
  expect(session.deadline - session.started_at).toBe(2);
  await page.getByRole('button', { name: 'Input ACK' }).click();
  await expect(page.locator('#puzzle-modal')).toBeHidden();
  await page.waitForFunction(() => window.__game.state.map.puzzles[0].solved);
  const input = sent.find(c => c.action?.action === 'timed_input');
  expect(input.action).toEqual({ action: 'timed_input', symbol: 'ACK', attempt_id: session.id, puzzle_session_id: session.id });
  expect(input.session_id).toBe(session.player_id);
  await page.evaluate(c => window.__game.send({ ...c, request_id: 'browser-replay' }), input);
  await expect.poll(() => replies.some(r => r.request_id === 'browser-replay' && r.ok === false && /replayed/.test(r.message))).toBe(true);
  expect(errors).toEqual([]);
});

test('actual QTE tick timeout renders expired and rejects late input and wrong attempt', async ({ page }) => {
  const replies = [];
  await page.routeWebSocket('**/ws', route => {
    const server = route.connectToServer();
    server.onMessage(message => { replies.push(JSON.parse(message)); route.send(message); });
  });
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.localPlayerId);
  await page.locator('.class-card').first().click();
  await install(page);
  await page.getByRole('button', { name: 'Start attempt' }).click();
  await expect(page.locator('.qte-deadline')).toContainText('active');
  const command = await page.evaluate(() => ({ type: 'puzzle_action', puzzle_id: 'fixture-qte', session_id: window.__game.localPlayerId,
    world_id: window.__game.state.world_id, action: { action: 'timed_input', symbol: 'ACK', attempt_id: window.__game.activePuzzle.session.id } }));
  await page.evaluate(() => { window.__lateInputButton = [...document.querySelectorAll('#puzzle-body button')].find(b => b.textContent === 'Input ACK'); });
  await page.evaluate(c => window.__game.send({ ...c, request_id: 'browser-wrong', action: { ...c.action, attempt_id: 'wrong' } }), command);
  await expect.poll(() => replies.some(r => r.request_id === 'browser-wrong' && r.ok === false && /session/.test(r.message))).toBe(true);
  await page.request.post('/__test__/fixture', { data: { action: 'advance', seconds: 2 } });
  await expect(page.locator('.qte-deadline')).toContainText('expired');
  await expect(page.locator('.qte-expired')).toContainText('Attempt expired');
  await expect(page.getByRole('button', { name: 'Input ACK' })).toHaveCount(0);
  // A late event from the actual input element still receives correlated server feedback.
  await page.evaluate(() => window.__lateInputButton.click());
  await expect(page.locator('#command-feedback')).toContainText('Attempt expired');
  await page.evaluate(c => window.__game.send({ ...c, request_id: 'browser-late' }), command);
  await expect.poll(() => replies.some(r => r.request_id === 'browser-late' && r.ok === false && /expired/.test(r.message))).toBe(true);
  await expect(page.locator('#puzzle-modal')).toBeVisible();
  expect(await page.evaluate(() => window.__game.state.map.puzzles[0].solved)).toBe(false);
});

test('actual consumed QTE input replay presents server error without changing objective state', async ({ page }) => {
  let corruptOnce = true;
  await page.routeWebSocket('**/ws', route => {
    const server = route.connectToServer();
    route.onMessage(message => {
      const command = JSON.parse(message);
      if (corruptOnce && command.action?.action === 'timed_input') {
        corruptOnce = false;
        command.action.symbol = 'WRONG';
        server.send(JSON.stringify(command));
      } else server.send(message);
    });
  });
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.localPlayerId);
  await page.locator('.class-card').first().click();
  await install(page);
  await page.getByRole('button', { name: 'Start attempt' }).click();
  await expect(page.getByRole('button', { name: 'Input ACK' })).toBeVisible();
  await page.evaluate(() => { window.__replayInputButton = [...document.querySelectorAll('#puzzle-body button')].find(b => b.textContent === 'Input ACK'); });
  await page.getByRole('button', { name: 'Input ACK' }).click();
  await expect(page.locator('.qte-deadline')).toContainText('consumed');
  await page.evaluate(() => window.__replayInputButton.click());
  await expect(page.locator('#command-feedback')).toContainText('Attempt replayed or consumed');
  expect(await page.evaluate(() => window.__game.state.map.puzzles[0].solved)).toBe(false);
});
