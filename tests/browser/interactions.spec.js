import { test, expect } from '@playwright/test';

async function install(page) {
  const response = await page.request.post('/__test__/fixture', { data: { action: 'install' } });
  expect(response.ok()).toBe(true);
  const installed = await response.json();
  await page.waitForFunction(world => window.__game?.state?.world_id === world && window.__game.state.phase === 'active' && window.__game.state.map?.puzzles[0]?.id === 'fixture-terminal', installed.world_id);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
}

async function join(page, classId = 'vanguard') {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.waitForFunction(() => window.__game?.localPlayerId);
  const index = await page.evaluate(id => Object.values(window.__game.catalog.classes).findIndex(c => c.id === id), classId);
  await page.locator('.class-card').nth(index).click();
  await expect(page.locator('#class-modal')).toBeHidden();
  return errors;
}

async function clickTarget(page, identity) {
  await page.bringToFront();
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  const point = await page.evaluate(async id => {
    const { worldToIso } = await import('/static/js/iso.js');
    const game = window.__game;
    const target = [...game.state.map.crates, ...game.state.map.puzzles].find(t => t.id === id);
    return worldToIso(target.x, target.y, game.camera);
  }, identity);
  await page.mouse.click(point.x, point.y);
}

test('two real clients reconcile puzzle progress, teammate solve, crate unlock, chest updates and deletion', async ({ browser }) => {
  const context = await browser.newContext();
  const leader = await context.newPage();
  const peer = await context.newPage();
  try {
    const leaderErrors = await join(leader);
    const peerErrors = await join(peer, 'tech');
    await install(leader);
    await peer.waitForFunction(() => window.__game.state.phase === 'active');
    await clickTarget(leader, 'fixture-terminal');
    await expect(leader.locator('#puzzle-modal')).toBeVisible();
    await clickTarget(peer, 'fixture-terminal');
    await expect(peer.locator('#puzzle-modal')).toBeVisible();
    await peer.locator('.puzzle-node-btn').first().click();
    await expect(leader.locator('.puzzle-node-btn').first()).toContainText('180');
    await peer.locator('.tech-bypass-btn').click();
    await expect(leader.locator('#puzzle-modal')).toBeHidden();
    await expect(peer.locator('#puzzle-modal')).toBeHidden();

    await clickTarget(leader, 'fixture-locked');
    await clickTarget(peer, 'fixture-locked');
    await peer.locator('.tech-bypass-btn').click();
    await expect(leader.locator('#chest-title')).toHaveText('Locked Crate');
    await expect(peer.locator('#chest-title')).toHaveText('Locked Crate');
    await leader.locator('#close-chest-btn').click();
    await peer.locator('#close-chest-btn').click();

    await clickTarget(leader, 'fixture-chosen');
    await clickTarget(peer, 'fixture-chosen');
    await expect(leader.locator('#chest-title')).toHaveText('Chosen Crate');
    await peer.locator('.chest-take-btn').click();
    await expect(leader.locator('#chest-body')).toContainText('empty');
    await peer.request.post('/__test__/fixture', { data: { action: 'delete', target_id: 'fixture-chosen' } });
    await expect(leader.locator('#chest-modal')).toBeHidden();
    await expect(peer.locator('#chest-modal')).toBeHidden();
    expect([...leaderErrors, ...peerErrors]).toEqual([]);
  } finally { await context.close(); }
});

test('actual delayed socket replies cannot reopen closed targets or overwrite newer co-op solved snapshots', async ({ browser }) => {
  const context = await browser.newContext();
  const leader = await context.newPage();
  const peer = await context.newPage();
  let release;
  let delayed;
  await leader.routeWebSocket('**/ws', route => {
    const server = route.connectToServer();
    server.onMessage(message => {
      const payload = JSON.parse(message);
      if (payload.type === 'interact_result' && payload.target_id === delayed) {
        release = () => route.send(message);
      } else route.send(message);
    });
  });
  try {
    await join(leader); await join(peer, 'tech'); await install(leader);
    delayed = 'fixture-chosen';
    await clickTarget(leader, delayed);
    await expect.poll(() => Boolean(release)).toBe(true);
    await leader.keyboard.press('Escape');
    release(); release = null;
    await expect(leader.locator('#chest-modal')).toBeHidden();
    delayed = null;
    await clickTarget(leader, 'fixture-locked');
    await expect(leader.locator('#puzzle-title')).toHaveText('Crate Lock');
    await leader.keyboard.press('Escape');

    delayed = 'fixture-terminal';
    await clickTarget(leader, delayed);
    await expect.poll(() => Boolean(release)).toBe(true);
    await clickTarget(peer, 'fixture-terminal');
    await peer.locator('.tech-bypass-btn').click();
    await leader.waitForFunction(() => window.__game.state.map.puzzles[0].solved);
    release(); release = null;
    await expect(leader.locator('#puzzle-modal')).toBeHidden();

    delayed = 'fixture-chosen';
    await clickTarget(leader, delayed);
    await expect.poll(() => Boolean(release)).toBe(true);
    await leader.evaluate(() => window.__game.send({ type: 'return_lobby' }));
    await leader.waitForFunction(() => window.__game.state.phase === 'lobby');
    release(); release = null;
    await expect(leader.locator('#chest-modal')).toBeHidden();
  } finally { await context.close(); }
});

test('blur alt-tab visibility pointer cancel neutralize actual movement/fire snapshots, modal and editing block hotkeys', async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  const peer = await context.newPage();
  const sent = [];
  await page.routeWebSocket('**/ws', route => {
    const server = route.connectToServer();
    route.onMessage(message => { sent.push(JSON.parse(message)); server.send(message); });
  });
  try {
    await join(page); await join(peer); await install(page);
    await page.bringToFront();
    await page.mouse.move(900, 500);
    await page.keyboard.down('w');
    await page.mouse.down();
    await page.waitForFunction(() => {
      const p = window.__game.state.players.find(p => p.id === window.__game.localPlayerId);
      return p.input.firing && Math.hypot(p.vx, p.vy) > 0;
    });
    await peer.bringToFront();
    // Headless Chrome may not emit OS focus events on tab activation; explicit blur
    // exercises the exact browser focus listener without releasing held inputs.
    await page.evaluate(() => window.dispatchEvent(new Event('blur')));
    await peer.waitForFunction(id => {
      const p = window.__game.state.players.find(p => p.id === id);
      return p && p.input.dx === 0 && p.input.dy === 0 && !p.input.firing && !p.input.running && p.vx === 0 && p.vy === 0;
    }, await page.evaluate(() => window.__game.localPlayerId));
    expect(sent.at(-1).firing).toBe(false);
    await page.mouse.up(); await page.keyboard.up('w');
    await page.bringToFront();
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));

    for (const event of ['pointercancel', 'visibilitychange']) {
      await page.keyboard.down('w'); await page.mouse.down();
      await page.evaluate(name => {
        if (name === 'visibilitychange') {
          Object.defineProperty(document, 'hidden', { configurable: true, value: true });
          document.dispatchEvent(new Event(name));
        } else window.dispatchEvent(new Event(name));
      }, event);
      await expect.poll(() => sent.some(m => m.type === 'input' && m.dx === 0 && !m.firing)).toBe(true);
      expect(await page.evaluate(() => window.__game.keys.up || window.__game.firing)).toBe(false);
      await page.keyboard.up('w'); await page.mouse.up();
      await page.evaluate(() => {
        Object.defineProperty(document, 'hidden', { configurable: true, value: false });
        document.dispatchEvent(new Event('visibilitychange'));
      });
    }
    await install(page);
    await clickTarget(page, 'fixture-terminal');
    await expect(page.locator('#puzzle-modal')).toBeVisible();
    const before = sent.length;
    for (const key of ['r', 'f', '1', '2', 'Space', 'q', 'w']) await page.keyboard.press(key);
    expect(sent.slice(before).filter(m => ['reload', 'skill', 'inventory_action'].includes(m.type))).toEqual([]);
    await page.keyboard.press('Escape');

    await page.evaluate(() => {
      const editor = document.createElement('input');
      editor.id = 'fixture-editor'; editor.style = 'position:fixed;left:10px;top:10px;z-index:300';
      document.body.append(editor); editor.focus();
    });
    const editingBefore = sent.length;
    await page.keyboard.type('rf12qwe'); await page.keyboard.press('Space');
    await expect(page.locator('#fixture-editor')).toHaveValue('rf12qwe ');
    expect(sent.slice(editingBefore).filter(m => m.type !== 'input')).toEqual([]);
    await page.locator('#fixture-editor').evaluate(el => el.remove());
    const repeatBefore = sent.filter(m => m.type === 'skill').length;
    await page.keyboard.down('q'); await page.keyboard.down('q'); await page.keyboard.up('q');
    await expect.poll(() => sent.filter(m => m.type === 'skill').length).toBe(repeatBefore + 1);
    const shiftStart = sent.length;
    await page.mouse.move(900, 500);
    await page.keyboard.down('Shift'); await page.mouse.down();
    await expect.poll(() => sent.slice(shiftStart).some(m => m.type === 'input' && m.running && m.firing && m.dx === 0)).toBe(true);
    await page.keyboard.down('w');
    await expect.poll(() => sent.slice(shiftStart).some(m => m.type === 'input' && m.running && m.firing && m.dx < 0)).toBe(true);
    await page.mouse.up(); await page.keyboard.up('w'); await page.keyboard.up('Shift');
  } finally { await context.close(); }
});

test('chosen click returns explicit server range and LOS feedback rather than selecting nearby crate', async ({ page }) => {
  await join(page); await install(page);
  await clickTarget(page, 'fixture-chosen');
  await expect(page.locator('#chest-title')).toHaveText('Chosen Crate');
  await page.keyboard.press('Escape');
  await page.request.post('/__test__/fixture', { data: { action: 'move', player_id: await page.evaluate(() => window.__game.localPlayerId), x: 10, y: 8.9 } });
  await page.waitForFunction(() => window.__game.state.players.find(p => p.id === window.__game.localPlayerId).y === 8.9);
  await clickTarget(page, 'fixture-chosen');
  await expect(page.locator('#command-feedback')).toContainText('out of range');
  await expect(page.locator('#chest-modal')).toBeHidden();
  await install(page);
  await page.request.post('/__test__/fixture', { data: { action: 'barrier' } });
  await page.waitForFunction(() => window.__game.state.map.walls.length === 1);
  await clickTarget(page, 'fixture-terminal');
  await expect(page.locator('#command-feedback')).toContainText('line of sight');
  await expect(page.locator('#puzzle-modal')).toBeHidden();
});

async function bounds(page, selector) {
  const outside = await page.locator(selector).evaluateAll(elements => elements.filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && (r.left < -1 || r.right > innerWidth + 1);
  }).map(el => ({ tag: el.tagName, text: el.textContent.slice(0, 40), width: el.getBoundingClientRect().width })));
  expect(outside).toEqual([]);
}

test('375px high-DPI lobby preparation and puzzle/chest modal controls stay reachable and canvas aim matches CSS pixels', async ({ browser }, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 375, height: 667 }, deviceScaleFactor: 2 });
  const page = await context.newPage();
  try {
    await page.goto('/');
    await expect(page.locator('#class-modal')).toBeVisible();
    await bounds(page, '#class-modal button, #class-modal .modal-card');
    await page.screenshot({ path: testInfo.outputPath('stage09-375-dpr2-class.png'), fullPage: true });
    await page.locator('.class-card').first().click();
    await expect(page.locator('#class-modal')).toBeHidden();
    await bounds(page, '#lobby-screen button, #lobby-screen input, #lobby-screen .panel');
    await page.locator('#launch-btn').click();
    await expect(page.locator('#prep-screen')).toBeVisible();
    await bounds(page, '#prep-screen button, #prep-screen .prep-player-card');
    await page.locator('button.prep-ready-btn').scrollIntoViewIfNeeded();
    await expect(page.locator('button.prep-ready-btn')).toBeInViewport();
    await page.locator('#prep-back-btn').scrollIntoViewIfNeeded();
    await page.locator('#prep-back-btn').click();
    await install(page);
    await clickTarget(page, 'fixture-terminal');
    await expect(page.locator('#puzzle-modal')).toBeVisible();
    await bounds(page, '#puzzle-modal button, #puzzle-modal .modal-card');
    await expect(page.locator('#close-puzzle-btn')).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath('stage09-375-dpr2-puzzle.png') });
    await page.keyboard.press('Escape');
    await page.locator('#inventory-toggle').click();
    await expect(page.locator('#inventory-toggle')).toHaveAttribute('aria-expanded', 'true');
    await bounds(page, '#hud-inventory-sidebar button');
    await page.locator('#inventory-toggle').click();
    await clickTarget(page, 'fixture-chosen');
    await expect(page.locator('#chest-modal')).toBeVisible();
    await bounds(page, '#chest-modal button, #chest-modal .modal-card');
    await expect(page.locator('.chest-take-btn')).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath('stage09-375-dpr2-chest.png') });
    await page.keyboard.press('Escape');
    await page.mouse.move(180, 320);
    const dpi = await page.evaluate(async () => {
      const game = window.__game;
      const canvas = document.getElementById('game-canvas');
      const { isoToWorld } = await import('/static/js/iso.js');
      return { width: canvas.width, height: canvas.height, screenW: game.camera.screenW, screenH: game.camera.screenH,
        transform: canvas.getContext('2d').getTransform().a,
        expected: isoToWorld(180, 320, game.camera), actual: game.state.players.find(p => p.id === game.localPlayerId).input };
    });
    expect(dpi.width).toBe(750); expect(dpi.height).toBe(1334); expect(dpi.transform).toBe(2);
    await expect.poll(async () => page.evaluate(() => window.__game.state.players.find(p => p.id === window.__game.localPlayerId).input.aim_x)).toBeCloseTo(dpi.expected.x, 1);
    await page.setViewportSize({ width: 320, height: 500 });
    await page.waitForFunction(() => window.__game.camera.screenW === 320 && window.__game.camera.screenH === 500);
    await clickTarget(page, 'fixture-terminal');
    await expect(page.locator('#puzzle-modal')).toBeVisible();
    await bounds(page, '#puzzle-modal button, #puzzle-modal .modal-card');
    await page.keyboard.press('Escape');
    await page.evaluate(() => window.__game.send({ type: 'return_lobby' }));
    await expect(page.locator('#lobby-screen')).toBeVisible();
    await page.evaluate(() => { document.documentElement.style.fontSize = '32px'; });
    await bounds(page, '#lobby-screen button, #lobby-screen input, #lobby-screen .panel');
    await page.locator('#launch-btn').click();
    await expect(page.locator('#prep-screen')).toBeVisible();
    await bounds(page, '#prep-screen button, #prep-screen .prep-player-card');
  } finally { await context.close(); }
});

test('all data-driven HTML and attribute sinks render malicious values as text and inert IDs', async ({ page }) => {
  const attack = '<img src=x onerror="window.__injected=1">';
  const attribute = '" onmouseover="window.__injected=1';
  await page.routeWebSocket('**/ws', route => {
    const server = route.connectToServer();
    server.onMessage(message => {
      const payload = JSON.parse(message);
      if (payload.type === 'welcome') {
        payload.catalog.classes.vanguard.name = attack;
        payload.catalog.classes.vanguard.title = attack;
        payload.catalog.classes.vanguard.weapon.name = attack;
        payload.catalog.classes.vanguard.skill.description = attack;
        payload.catalog.classes.vanguard.color = '#fff; background:url(https://evil)';
        payload.catalog.missions.outpost.name = attack;
        payload.catalog.missions.outpost.description = attack;
      }
      const state = payload.type === 'welcome' ? payload.state : payload.type === 'state' ? payload : null;
      if (state) {
        state.shared_stash = [{ template_id: attribute, name: attack, weight: 1, count: 1, category: 'loot' }];
        state.levels[0].name = attack;
        state.levels[0].lock_reason = attribute;
        state.events = [attack];
        for (const p of state.players) {
          const item = p.inventory.equipped.primary_weapon;
          if (item) { item.name = attack; item.id = attribute; }
        }
        for (const crate of state.map?.crates || []) for (const item of crate.items) {
          item.name = attack; item.description = attack; item.id = attribute;
        }
      }
      route.send(JSON.stringify(payload));
    });
  });
  await page.goto('/');
  await expect(page.locator('#class-modal')).toContainText(attack);
  await page.locator('.class-card').first().click();
  await expect(page.locator('#lobby-shared-stash')).toContainText(attack);
  await page.locator('#launch-btn').click();
  await expect(page.locator('#prep-screen')).toContainText(attack);
  expect(await page.locator('.prep-crate-item').first().getAttribute('data-template-id')).toBe(attribute);
  await install(page);
  await clickTarget(page, 'fixture-chosen');
  await expect(page.locator('#chest-body')).toContainText(attack);
  expect(await page.locator('.chest-item-card').getAttribute('data-item-id')).toBe(attribute);
  expect(await page.locator('img, [onerror], [onmouseover], svg[onload]').count()).toBe(0);
  expect(await page.evaluate(() => window.__injected)).toBeUndefined();
});

test('actual disconnect clears held input and UI and rejoin does not replay late old-session modal', async ({ page }) => {
  let disconnect;
  await page.routeWebSocket('**/ws', route => {
    const server = route.connectToServer();
    disconnect = () => { route.close(); server.close(); };
  });
  await join(page); await install(page);
  const oldId = await page.evaluate(() => window.__game.localPlayerId);
  await clickTarget(page, 'fixture-terminal');
  await expect(page.locator('#puzzle-modal')).toBeVisible();
  disconnect();
  await page.waitForFunction(() => window.__game.state === null && window.__game.localPlayerId === null);
  expect(await page.evaluate(() => Object.values(window.__game.keys).some(Boolean) || window.__game.firing)).toBe(false);
  await expect(page.locator('#puzzle-modal')).toBeHidden();
  await page.waitForFunction(id => window.__game.localPlayerId && window.__game.localPlayerId !== id, oldId);
  await expect(page.locator('#class-modal')).toBeVisible();
  await expect(page.locator('#puzzle-modal')).toBeHidden();
});
