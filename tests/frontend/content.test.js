import assert from 'node:assert/strict';
import test from 'node:test';
import { audioAssets, canFireWhileRunning, playerSightRange } from '../../frontend/src/js/content.js';

test('explicit catalog capabilities ignore deceptive item names, slots and weights', () => {
  const items = { heavy: { weapon: { can_fire_while_running: false } } };
  const player = { inventory: { active_weapon_slot: 'secondary_weapon', equipped: { secondary_weapon: { template_id: 'heavy', name: 'Pistol', weight: 1 } } } };
  assert.equal(canFireWhileRunning(player, items), false);
  items.heavy.weapon.can_fire_while_running = true;
  assert.equal(canFireWhileRunning(player, items), true);
  assert.equal(canFireWhileRunning(null, items), false);
  player.inventory.equipped.secondary_weapon = null;
  assert.equal(canFireWhileRunning(player, items), false);
});

test('sight effect and audio paths use the catalog, not guessed defaults', () => {
  const items = { lamp: { effect: { behavior: 'sight_boost', sight_boost: 7 } } };
  const player = { class_id: 'recon', inventory: { equipped: {}, storage: [{ template_id: 'lamp' }] } };
  assert.equal(playerSightRange(player, { recon: { sight_range: 14 } }, items), 21);
  assert.equal(playerSightRange(player, {}, items), null);
  player.inventory.storage[0].value = { sight_boost: 8 };
  assert.equal(playerSightRange(player, { recon: { sight_range: 14 } }, items), 22);
  const catalog = { sounds: { shot: { asset: 'audio/custom.mp3' } }, music: { tracks: { explore: 'audio/explore.mp3' } } };
  assert.deepEqual(audioAssets(catalog), { shot: 'audio/custom.mp3', music_explore: 'audio/explore.mp3' });
});

test('actual audio loader fetches authored asset paths', async () => {
  const oldWindow = globalThis.window;
  globalThis.window = { addEventListener() {} };
  try {
    const { audioManager } = await import('../../frontend/src/js/audio.js');
    audioManager.setCatalog({ sounds: { shot: { asset: 'audio/changed.mp3' } }, music: { bpm: 120, tracks: { explore: 'audio/explore.mp3', combat: 'audio/combat.mp3' } } });
    audioManager.ensureContext = () => ({});
    const urls = [];
    audioManager._loadAndDecode = async url => { urls.push(url); return {}; };
    await audioManager.init();
    assert.deepEqual(urls, ['/assets/audio/changed.mp3', '/assets/audio/explore.mp3', '/assets/audio/combat.mp3']);
    assert.equal(audioManager.musicMeta.bpm, 120);
  } finally {
    globalThis.window = oldWindow;
  }
});

test('authoritative snapshot sight and capabilities override stale client content', () => {
  const player = { sight_range: 22, capabilities: { can_fire_while_running: true } };
  assert.equal(playerSightRange(player, {}, {}), 22);
  assert.equal(canFireWhileRunning(player, {}), true);
  player.capabilities.can_fire_while_running = false;
  assert.equal(canFireWhileRunning(player, {}), false);
});
