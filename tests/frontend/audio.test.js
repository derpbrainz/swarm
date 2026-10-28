import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const handlers = new Map();
globalThis.window = { addEventListener(type, fn) { handlers.set(fn, type); }, removeEventListener(type, fn) { handlers.delete(fn); } };
const { AudioManager, projectedPan } = await import('../../frontend/src/js/audio.js');
const catalog = JSON.parse(readFileSync(new URL('../../backend/app/content.json', import.meta.url)));

function context(state = 'running') {
  const nodes = [];
  const param = () => ({ value: 0, setTargetAtTime(v) { this.value = v; }, setValueAtTime(v) { this.value = v; }, cancelScheduledValues() {}, linearRampToValueAtTime(v) { this.value = v; } });
  const node = () => { const n = { gain: param(), pan: param(), frequency: param(), connect() {}, disconnected: false, disconnect() { this.disconnected = true; } }; nodes.push(n); return n; };
  return { state, currentTime: 0, destination: {}, nodes, failStart: false,
    createGain: node, createStereoPanner: node, createBiquadFilter: node,
    createBufferSource() { const n = node(); n.started = 0; n.stopped = 0; n.start = () => { if (this.failStart) throw new Error('start failed'); n.started++; }; n.stop = () => { n.stopped++; }; return n; },
    async resume() { if (this.failResume) throw new Error('gesture denied'); this.state = 'running'; },
    async close() { this.state = 'closed'; },
  };
}

function manager(state = 'running') {
  const ctx = context(state);
  window.AudioContext = class { constructor() { return ctx; } };
  const m = new AudioManager();
  m.setCatalog(catalog);
  m.ensureContext();
  m._loadAndDecode = async () => ({ duration: 10 });
  return { m, ctx };
}

function snapshot(world = 'w', sequence = 1, music = 'explore') {
  return { world_id: world, simulation_time: 1, event_sequence: sequence,
    music: { state: music, bpm: 118, start_t: 0, simulation_time: 1 }, ambience_emitters: [],
    audio_events: [{ event_id: `${world}:${sequence}`, world_id: world, sequence, timestamp: 1, expires_at: 1.75,
      sound_type: 'weapon_fire_pistol', source_id: 'p', x: 0, y: 0 }] };
}

test('projected isometric pan follows screen X for one-shots and ambience', () => {
  assert.ok(projectedPan(10, 20, 50) < 0);
  assert.ok(projectedPan(20, 10, 50) > 0);
  assert.equal(projectedPan(10, 10, 50), 0);
});

test('early desired music and fresh one-shot wait for asynchronous asset readiness', async () => {
  const { m } = manager();
  const resolves = [];
  m._loadAndDecode = () => new Promise(resolve => resolves.push(resolve));
  m.consumeSnapshot(snapshot(), { x: 0, y: 0 });
  assert.equal(m.currentMusicState, null);
  assert.equal(m.pendingEvents.size, 1);
  const loading = m.initPromise;
  resolves.forEach(resolve => resolve({ duration: 10 }));
  await loading;
  await Promise.resolve();
  assert.equal(m.currentMusicState, 'explore');
  assert.equal(m.activeVoices.size, 1);
  assert.equal(m.pendingEvents.size, 0);
  m.dispose();
});

test('gesture denial does not latch music; next gesture retries and applies desired state', async () => {
  const { m, ctx } = manager('suspended');
  m.consumeSnapshot(snapshot(), { x: 0, y: 0 });
  await m.initPromise;
  assert.equal(m.currentMusicState, null);
  ctx.failResume = true;
  await m._unlockHandler();
  assert.equal(m.currentMusicState, null);
  ctx.failResume = false;
  await m._unlockHandler();
  assert.equal(m.currentMusicState, 'explore');
  assert.equal(m.activeVoices.size, 1);
  m.dispose();
});

test('repeat broadcasts terminal expiry previous worlds and teardown never replay', async () => {
  const { m, ctx } = manager();
  await m.init();
  const msg = snapshot();
  m.consumeSnapshot(msg, { x: 0, y: 0 });
  const voice = [...m.activeVoices.values()][0];
  for (let i = 0; i < 5; i++) m.consumeSnapshot(msg, { x: 0, y: 0 });
  assert.equal([...m.activeVoices.values()][0], voice);
  m.consumeSnapshot({ ...snapshot('w', 2, 'lobby'), simulation_time: 2 }, { x: 0, y: 0 });
  assert.equal(m.activeVoices.size, 1); // Expired event never starts.
  assert.equal(m.currentMusicState, 'lobby');
  assert.deepEqual(m.musicLayers, { explore: null, combat: null });
  const next = snapshot('next', 3);
  next.audio_events.unshift(msg.audio_events[0]);
  m.consumeSnapshot(next, { x: 0, y: 0 });
  assert.equal(voice.source.stopped, 1);
  assert.equal(m.highWater, 3);
  assert.equal(m.activeVoices.size, 1);
  m.resetWorld();
  assert.equal(m.highWater, 0);
  assert.equal(m.activeVoices.size, 0);
  assert.equal(m.desiredMusic, null);
  m.dispose();
  assert.equal(ctx.state, 'closed');
  assert.equal(m.buffers.size, 0);
  assert.ok(!handlers.has(m._unlockHandler));
});

test('failed assets preserve fallback and retry on gesture without replaying consumed events', async () => {
  const { m } = manager();
  let fail = true, fetches = 0;
  m._loadAndDecode = async url => {
    fetches++;
    if (fail && url.includes('music_combat')) throw new Error('HTTP 503');
    return { duration: 10 };
  };
  await m.init();
  assert.equal(m.initialized, false);
  m.consumeSnapshot(snapshot('w', 1, 'combat'), { x: 0, y: 0 });
  assert.equal(m.desiredMusic.state, 'combat');
  assert.equal(m.currentMusicState, 'explore');
  const voice = [...m.activeVoices.values()][0];
  const before = fetches;
  await m.init();
  assert.equal(fetches, before); // Bounded retry cooldown, not every snapshot.
  fail = false;
  await m._unlockHandler();
  assert.equal(m.currentMusicState, 'combat');
  assert.equal(m.initialized, true);
  assert.equal([...m.activeVoices.values()][0], voice);
  m.dispose();
});

test('failed starts clean graph and retain retryable desired music/fresh event', async () => {
  const { m, ctx } = manager();
  await m.init();
  ctx.failStart = true;
  m.consumeSnapshot(snapshot(), { x: 0, y: 0 });
  assert.equal(m.currentMusicState, null);
  assert.equal(m.activeVoices.size, 0);
  assert.equal(m.pendingEvents.size, 1);
  assert.ok(ctx.nodes.filter(n => n.start).every(n => n.disconnected));
  ctx.failStart = false;
  m._reconcile();
  assert.equal(m.currentMusicState, 'explore');
  assert.equal(m.activeVoices.size, 1);
  m.dispose();
});

test('authored ambience waits for load, updates occlusion/pan, culls and tears down', async () => {
  const { m } = manager();
  await m.init();
  const msg = snapshot();
  msg.audio_events = [];
  const emitter = { id: 'vent', world_id: 'w', active: true, sound_type: 'ambient_vent', x: 1, y: 2, occl_gain: 0.22, lpf_cutoff: 900 };
  msg.ambience_emitters = [emitter];
  m.consumeSnapshot(msg, { x: 0, y: 0 });
  const node = m.activeAmbiences.get('vent');
  assert.ok(node.panner.pan.value < 0);
  assert.equal(node.filter.frequency.value, 900);
  emitter.occl_gain = 1; emitter.lpf_cutoff = 20000;
  m.consumeSnapshot(msg, { x: 0, y: 0 });
  assert.equal(m.activeAmbiences.get('vent'), node);
  assert.equal(node.filter.frequency.value, 20000);
  assert.ok(node.gain.gain.value > 0.22);
  m.consumeSnapshot(msg, { x: 100, y: 0 });
  assert.equal(m.activeAmbiences.size, 0);
  assert.equal(node.source.stopped, 1);
  m.consumeSnapshot(msg, { x: 0, y: 0 });
  assert.equal(m.activeAmbiences.size, 1);
  m.resetWorld();
  assert.equal(m.activeAmbiences.size, 0);
  m.dispose();
});

test('dispose during load cannot repopulate buffers or start obsolete audio', async () => {
  const { m, ctx } = manager();
  const resolves = [];
  m._loadAndDecode = () => new Promise(resolve => resolves.push(resolve));
  const loading = m.init();
  m.consumeSnapshot(snapshot(), { x: 0, y: 0 });
  m.dispose();
  resolves.forEach(resolve => resolve({ duration: 10 }));
  await loading;
  assert.equal(m.buffers.size, 0);
  assert.equal(m.currentMusicState, null);
  assert.equal(ctx.nodes.filter(n => n.started).length, 0);
});

test('pending transient expiry and limit are enforced while context is locked', async () => {
  const { m } = manager('suspended');
  await m.init();
  const msg = snapshot();
  msg.audio_events = Array.from({ length: catalog.rules.audio.history_limit + 20 }, (_, i) => ({ ...msg.audio_events[0], event_id: `w:${i + 1}`, sequence: i + 1 }));
  m.consumeSnapshot(msg, { x: 0, y: 0 });
  assert.equal(m.pendingEvents.size, catalog.rules.audio.history_limit);
  for (const pending of m.pendingEvents.values()) pending.deadline = 0;
  await m._unlockHandler();
  assert.equal(m.pendingEvents.size, 0);
  assert.equal(m.activeVoices.size, 0);
  m.dispose();
});

test('world reset while loading starts only latest desired music, not obsolete voices', async () => {
  const { m } = manager();
  const resolves = [];
  m._loadAndDecode = () => new Promise(resolve => resolves.push(resolve));
  m.consumeSnapshot(snapshot(), { x: 0, y: 0 });
  const loading = m.initPromise;
  m.consumeSnapshot({ world_id: null, simulation_time: 2, audio_events: [], ambience_emitters: [], music: { state: 'lobby' } }, null);
  resolves.forEach(resolve => resolve({ duration: 10 }));
  await loading;
  assert.equal(m.currentMusicState, 'lobby');
  assert.equal(m.activeVoices.size, 0);
  assert.equal(m.pendingEvents.size, 0);
  assert.deepEqual(m.musicLayers, { explore: null, combat: null });
  m.dispose();
});

test('graph failure cleans partial nodes and missing StereoPanner uses mono fallback', async () => {
  const { m, ctx } = manager();
  await m.init();
  ctx.createStereoPanner = undefined;
  const original = ctx.createBiquadFilter;
  ctx.createBiquadFilter = () => { throw new Error('graph failed'); };
  m.consumeSnapshot(snapshot(), { x: 0, y: 0 });
  assert.equal(m.activeVoices.size, 0);
  assert.equal(m.pendingEvents.size, 1);
  assert.ok(ctx.nodes.filter(n => n.start && !n.started).every(n => n.disconnected));
  ctx.createBiquadFilter = original;
  m._reconcile();
  assert.equal(m.activeVoices.size, 1);
  m.dispose();
});

test('ambience load and start failures stay retryable then source replacement ends old loop', async () => {
  const { m, ctx } = manager();
  await m.init();
  const msg = snapshot();
  msg.audio_events = [];
  msg.ambience_emitters = [{ id: 'vent', world_id: 'w', active: true, sound_type: 'ambient_vent', x: 0, y: 0 }];
  const buffer = m.buffers.get('ambient_vent');
  m.buffers.delete('ambient_vent');
  m.consumeSnapshot(msg, { x: 0, y: 0 });
  assert.equal(m.activeAmbiences.size, 0);
  ctx.failStart = true;
  m.buffers.set('ambient_vent', buffer);
  m._reconcile();
  assert.equal(m.activeAmbiences.size, 0);
  ctx.failStart = false;
  m._reconcile();
  const old = m.activeAmbiences.get('vent');
  assert.ok(old);
  msg.ambience_emitters = [];
  m.consumeSnapshot(msg, { x: 0, y: 0 });
  assert.equal(old.source.stopped, 1);
  assert.ok(old.source.disconnected && old.filter.disconnected);
  m.dispose();
});

test('unexpected loop endings clear applied state and reconcile retries, no Web Audio stays usable', async () => {
  const { m } = manager();
  await m.init();
  const msg = snapshot();
  msg.audio_events = [];
  msg.ambience_emitters = [{ id: 'vent', world_id: 'w', active: true, sound_type: 'ambient_vent', x: 0, y: 0 }];
  m.consumeSnapshot(msg, { x: 0, y: 0 });
  const bed = m.musicLayers.explore;
  const vent = m.activeAmbiences.get('vent');
  bed.source.onended(); vent.source.onended();
  assert.equal(m.currentMusicState, null);
  assert.equal(m.activeAmbiences.size, 0);
  m._reconcile();
  assert.notEqual(m.musicLayers.explore, bed);
  assert.notEqual(m.activeAmbiences.get('vent'), vent);
  m.dispose();
  window.AudioContext = undefined;
  const unavailable = new AudioManager();
  unavailable.setCatalog(catalog);
  unavailable.consumeSnapshot(snapshot(), { x: 0, y: 0 });
  await unavailable.init();
  assert.equal(unavailable.audioContext, null);
  unavailable.dispose();
});
