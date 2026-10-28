import { audioAssets } from './content.js';

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
export const projectedPan = (dx, dy, radius) => clamp((dx - dy) / (Math.SQRT2 * radius * 0.75), -1, 1);

export class AudioManager {
  constructor() {
    this.audioContext = null;
    this.buffers = new Map();
    this.assets = {};
    this.soundDefinitions = {};
    this.musicTrackKeys = {};
    this.rules = null;
    this.masterVolume = 1;
    this.busVolumes = { sfx: 1, ambience: 1, music: 1 };
    this.activeVoices = new Map();
    this.activeAmbiences = new Map();
    this.musicLayers = { explore: null, combat: null };
    this.currentMusicState = null;
    this.desiredMusic = null;
    this.musicMeta = { bpm: null, start_t: 0 };
    this.worldId = undefined;
    this.highWater = 0;
    this.pendingEvents = new Map();
    this.ambienceEmitters = [];
    this.listenerPos = null;
    this.initialized = false;
    this.initPromise = null;
    this.failures = new Map();
    this.generation = 0;
    this.disposed = false;
    this._unlockHandler = this._unlockHandler.bind(this);
    for (const type of ['click', 'pointerdown', 'keydown']) window.addEventListener(type, this._unlockHandler);
  }

  setCatalog(catalog) {
    const assets = audioAssets(catalog);
    if (JSON.stringify(assets) !== JSON.stringify(this.assets)) {
      this.resetWorld();
      this.generation++;
      this.buffers.clear();
      this.failures.clear();
      this.initPromise = null;
    }
    this.assets = assets;
    this.soundDefinitions = catalog.sounds;
    this.musicTrackKeys = Object.fromEntries(Object.keys(catalog.music.tracks).map(state => [state, `music_${state}`]));
    this.musicMeta.bpm = catalog.music.bpm;
    this.rules = catalog.rules?.audio;
    this.initialized = Object.keys(this.assets).every(key => this.buffers.has(key));
  }

  ensureContext() {
    if (this.disposed) return null;
    if (this.audioContext) return this.audioContext;
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return null; // Gameplay remains available without Web Audio.
    const ctx = this.audioContext = new Context();
    this.masterGain = ctx.createGain();
    this.masterGain.gain.value = this.masterVolume;
    this.masterGain.connect(ctx.destination);
    for (const bus of ['sfx', 'ambience', 'music']) {
      const node = this[`${bus}Bus`] = ctx.createGain();
      node.gain.value = this.busVolumes[bus];
      node.connect(this.masterGain);
    }
    ctx.onstatechange = () => this._reconcile();
    return ctx;
  }

  async _unlockHandler() {
    if (this.disposed) return;
    try {
      const ctx = this.ensureContext();
      if (ctx && ctx.state !== 'running') await ctx.resume();
      await this.init(true);
      this._reconcile();
    } catch (err) {
      console.warn('[AudioManager] Gesture unlock failed; next gesture can retry:', err);
    }
  }

  async init(forceRetry = false) {
    if (this.disposed || this.initPromise) return this.initPromise;
    const ctx = this.ensureContext();
    if (!ctx) return;
    const generation = this.generation;
    const now = performance.now() / 1000;
    const entries = Object.entries(this.assets).filter(([key]) => !this.buffers.has(key) &&
      (forceRetry || !this.failures.has(key) || now - this.failures.get(key) >= this.rules.asset_retry_interval));
    if (!entries.length) return;
    this.initialized = false;
    this.initPromise = Promise.all(entries.map(async ([key, filename]) => {
      try {
        const buffer = await this._loadAndDecode(`/assets/${filename}`, ctx);
        if (generation !== this.generation || this.disposed) return;
        this.buffers.set(key, buffer);
        this.failures.delete(key);
        this._reconcile();
      } catch (err) {
        if (generation !== this.generation || this.disposed) return;
        this.failures.set(key, performance.now() / 1000);
        console.warn(`[AudioManager] Failed asset ${key}; playback remains retryable:`, err);
      }
    }));
    const promise = this.initPromise;
    await promise;
    if (generation === this.generation) {
      this.initialized = Object.keys(this.assets).every(key => this.buffers.has(key));
      this.initPromise = null;
      this._reconcile();
    }
  }

  async _loadAndDecode(url, ctx = this.audioContext) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status} fetching ${url}`);
    return ctx.decodeAudioData(await response.arrayBuffer());
  }

  setMasterVolume(value) {
    this.masterVolume = clamp(value, 0, 1);
    if (this.masterGain) this.masterGain.gain.setTargetAtTime(this.masterVolume, this.audioContext.currentTime, 0.01);
  }

  setBusVolume(bus, value) {
    this.busVolumes[bus] = clamp(value, 0, 1);
    if (this[`${bus}Bus`]) this[`${bus}Bus`].gain.setTargetAtTime(this.busVolumes[bus], this.audioContext.currentTime, 0.01);
  }

  consumeSnapshot(snapshot, listenerPos) {
    if (this.disposed || !this.rules) return;
    if (snapshot.world_id !== this.worldId) {
      this.resetWorld();
      this.worldId = snapshot.world_id;
    }
    this.listenerPos = listenerPos;
    const now = performance.now() / 1000;
    for (const event of snapshot.audio_events || []) {
      if (event.world_id !== this.worldId || event.sequence <= this.highWater) continue;
      this.highWater = event.sequence;
      if (!event.event_id || event.expires_at <= snapshot.simulation_time) continue;
      this.pendingEvents.set(event.event_id, { event, deadline: now + Math.min(this.rules.history_duration, event.expires_at - snapshot.simulation_time) });
    }
    while (this.pendingEvents.size > this.rules.history_limit) this.pendingEvents.delete(this.pendingEvents.keys().next().value);
    this.ambienceEmitters = snapshot.ambience_emitters || [];
    this.updateMusic(snapshot.music);
    this.init().catch(err => console.warn('[AudioManager] Audio init failed:', err));
    this._reconcile();
  }

  _reconcile() {
    const now = performance.now() / 1000;
    for (const [id, pending] of this.pendingEvents) if (pending.deadline <= now) this.pendingEvents.delete(id);
    if (!this.audioContext || this.audioContext.state !== 'running' || this.disposed) return;
    for (const [id, pending] of this.pendingEvents) {
      if (!this.listenerPos) continue;
      if (this.playSpatialSound(pending.event, this.listenerPos)) this.pendingEvents.delete(id);
    }
    this.updateAmbience(this.ambienceEmitters, this.listenerPos);
    this._applyMusic();
  }

  _mix(event, listener) {
    const definition = this.soundDefinitions[event.sound_type];
    if (!definition || !listener) return null;
    const min = event.radius_min ?? definition.radius_min;
    const max = event.radius_max ?? definition.radius_max;
    const dx = event.x - listener.x, dy = event.y - listener.y;
    const distance = Math.hypot(dx, dy);
    const attenuation = distance >= max ? 0 : distance <= min ? 1 : ((max - distance) / (max - min)) ** 2;
    return { gain: (event.base_gain ?? definition.base_gain) * attenuation * (event.occl_gain ?? 1),
      pan: projectedPan(dx, dy, max), cutoff: event.lpf_cutoff ?? 20000 };
  }

  _voice(buffer, bus, mix, loop = false) {
    const ctx = this.audioContext;
    const node = {};
    try {
      node.source = ctx.createBufferSource();
      node.panner = ctx.createStereoPanner ? ctx.createStereoPanner() : ctx.createGain();
      node.filter = ctx.createBiquadFilter();
      node.gain = ctx.createGain();
      node.source.buffer = buffer;
      node.source.loop = loop;
      if (node.panner.pan) node.panner.pan.value = mix.pan;
      node.filter.type = 'lowpass';
      node.filter.frequency.value = mix.cutoff;
      node.gain.gain.value = mix.gain;
      node.source.connect(node.panner); node.panner.connect(node.filter); node.filter.connect(node.gain); node.gain.connect(bus);
      node.source.onended = () => this._disconnect(node);
    } catch (err) {
      this._disconnect(node);
      console.warn('[AudioManager] Spatial graph failed; retryable:', err);
      return null;
    }
    return node;
  }

  _disconnect(node) {
    for (const name of ['source', 'panner', 'filter', 'gain']) node[name]?.disconnect();
  }

  _stop(node) {
    try { node.source.stop(); } catch (_) { /* Already ended or never started. */ }
    this._disconnect(node);
  }

  playSpatialSound(event, listenerPos) {
    const mix = this._mix(event, listenerPos);
    if (!mix || mix.gain <= 0.0005) return true;
    const buffer = this.buffers.get(event.sound_type);
    if (!buffer || this.audioContext?.state !== 'running') return false;
    const key = `${event.source_id || event.event_id}:${event.sound_type}`;
    const prev = this.activeVoices.get(key);
    if (prev) this._stop(prev);
    const node = this._voice(buffer, this.sfxBus, mix);
    if (!node) { this.activeVoices.delete(key); return false; }
    node.source.onended = () => {
      this._disconnect(node);
      if (this.activeVoices.get(key) === node) this.activeVoices.delete(key);
    };
    try {
      node.source.start();
      this.activeVoices.set(key, node);
      return true;
    } catch (err) {
      this._stop(node);
      this.activeVoices.delete(key);
      console.warn('[AudioManager] Voice failed; fresh event can retry:', err);
      return false;
    }
  }

  updateAmbience(emitters, listener) {
    if (this.audioContext?.state !== 'running') return;
    const live = new Set();
    for (const emitter of emitters) {
      const mix = this._mix(emitter, listener);
      const buffer = this.buffers.get(emitter.sound_type);
      if (!emitter.active || emitter.world_id !== this.worldId || !mix || !buffer || mix.gain <= 0.0005) continue;
      live.add(emitter.id);
      let node = this.activeAmbiences.get(emitter.id);
      if (node && node.soundType !== emitter.sound_type) {
        this._stop(node); this.activeAmbiences.delete(emitter.id); node = null;
      }
      if (!node) {
        node = this._voice(buffer, this.ambienceBus, mix, true);
        if (!node) continue;
        node.soundType = emitter.sound_type;
        const started = node;
        node.source.onended = () => {
          this._disconnect(started);
          if (this.activeAmbiences.get(emitter.id) === started) this.activeAmbiences.delete(emitter.id);
        };
        try { node.source.start(); this.activeAmbiences.set(emitter.id, node); }
        catch (err) { this._stop(node); console.warn('[AudioManager] Ambience start failed:', err); continue; }
      }
      const now = this.audioContext.currentTime;
      node.gain.gain.setTargetAtTime(mix.gain, now, 0.15);
      node.filter.frequency.setTargetAtTime(mix.cutoff, now, 0.15);
      if (node.panner.pan) node.panner.pan.setTargetAtTime(mix.pan, now, 0.15);
    }
    for (const [id, node] of this.activeAmbiences) if (!live.has(id)) { this._stop(node); this.activeAmbiences.delete(id); }
  }

  updateMusic(payload) {
    this.desiredMusic = payload || { state: 'lobby' };
    this.musicMeta = { bpm: payload?.bpm ?? this.musicMeta.bpm, start_t: payload?.start_t ?? 0 };
    if (this.desiredMusic.state === 'lobby' || this.desiredMusic.state === 'extraction') {
      this._clearMusic();
      this.currentMusicState = this.desiredMusic.state;
    } else this._applyMusic();
  }

  _applyMusic() {
    if (!this.desiredMusic || this.audioContext?.state !== 'running') return;
    const desired = this.desiredMusic.state;
    if (!this.musicTrackKeys[desired]) return;
    // A missing combat asset keeps the available exploration bed, never latches success.
    const state = this.buffers.has(this.musicTrackKeys[desired]) ? desired : 'explore';
    const buffer = this.buffers.get(this.musicTrackKeys[state]);
    if (!buffer || state === this.currentMusicState) return;
    const ctx = this.audioContext;
    let layer = this.musicLayers[state];
    if (!layer) {
      layer = {};
      try {
        layer.source = ctx.createBufferSource(); layer.gain = ctx.createGain();
        layer.source.buffer = buffer; layer.source.loop = true; layer.gain.gain.value = 0;
        layer.source.connect(layer.gain); layer.gain.connect(this.musicBus);
        const started = layer;
        layer.source.onended = () => {
          this._disconnect(started);
          if (this.musicLayers[state] === started) { this.musicLayers[state] = null; this.currentMusicState = null; }
        };
        const elapsed = Math.max(0, (this.desiredMusic.simulation_time ?? 0) - this.musicMeta.start_t);
        layer.source.start(ctx.currentTime, buffer.duration > 0 ? elapsed % buffer.duration : 0);
      } catch (err) { this._stop(layer); console.warn('[AudioManager] Music start failed; retryable:', err); return; }
      this.musicLayers[state] = layer;
    }
    const elapsed = Math.max(0, (this.desiredMusic.simulation_time ?? 0) - this.musicMeta.start_t);
    const barDuration = 4 * 60 / this.musicMeta.bpm;
    const nextBar = ctx.currentTime + (barDuration - elapsed % barDuration);
    for (const [name, node] of Object.entries(this.musicLayers)) {
      if (!node) continue;
      node.gain.gain.cancelScheduledValues(ctx.currentTime);
      node.gain.gain.setValueAtTime(node.gain.gain.value, ctx.currentTime);
      node.gain.gain.setValueAtTime(node.gain.gain.value, nextBar);
      node.gain.gain.linearRampToValueAtTime(name === state ? 1 : 0, nextBar + this.rules.music_crossfade_duration);
    }
    this.currentMusicState = state;
  }

  _clearMusic() {
    for (const layer of Object.values(this.musicLayers)) if (layer) this._stop(layer);
    this.musicLayers = { explore: null, combat: null };
    this.currentMusicState = null;
  }

  resetWorld() {
    for (const node of this.activeVoices.values()) this._stop(node);
    for (const node of this.activeAmbiences.values()) this._stop(node);
    this.activeVoices.clear(); this.activeAmbiences.clear();
    this._clearMusic();
    this.pendingEvents.clear(); this.highWater = 0; this.worldId = undefined;
    this.ambienceEmitters = []; this.listenerPos = null; this.desiredMusic = null;
  }

  dispose() {
    this.disposed = true; this.generation++;
    for (const type of ['click', 'pointerdown', 'keydown']) window.removeEventListener(type, this._unlockHandler);
    this.resetWorld();
    if (this.audioContext) {
      this.audioContext.onstatechange = null;
      this.audioContext.close().catch(() => {});
    }
    this.audioContext = null;
    this.buffers.clear(); this.failures.clear(); this.initPromise = null; this.initialized = false;
    this.masterGain = this.sfxBus = this.ambienceBus = this.musicBus = null;
  }
}

export const audioManager = new AudioManager();
export default audioManager;
