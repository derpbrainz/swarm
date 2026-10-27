// frontend/src/js/audio.js
// Modern Web Audio engine for Alien Swarm.

const ASSET_BASE = '/assets/audio/';

const SOUND_MANIFEST = {
  weapon_fire_shotgun: 'shotgun_fire.mp3',
  weapon_reload_shotgun: 'shotgun_reload.mp3',
  weapon_fire_pistol: 'pistol_fire.mp3',
  weapon_reload_pistol: 'pistol_reload.mp3',
  weapon_fire_machinegun: 'machinegun_fire.mp3',
  weapon_reload_machinegun: 'machinegun_reload.mp3',
  weapon_fire_plasma: 'plasma_gun_fire.mp3',
  weapon_reload_plasma: 'plasma_gun_reload.mp3',
  door_operate: 'door_operate.mp3',
  alien_attack: 'alien_attack.mp3',
  alien_death: 'alien_death.mp3',
  player_footstep: 'player_footstep.mp3',
  ambient_vent: 'ambient_vent.mp3',
  extraction_countdown: 'extraction_countdown.mp3',
  music_explore: 'music_explore.mp3',
  music_combat: 'music_combat.mp3',
};

const MUSIC_TRACK_KEYS = {
  explore: 'music_explore',
  combat: 'music_combat',
};

const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

class AudioManager {
  constructor() {
    /** @type {AudioContext|null} */
    this.audioContext = null;

    /** @type {Map<string, AudioBuffer>} */
    this.buffers = new Map();

    this.masterGain = null;
    this.sfxBus = null;
    this.ambienceBus = null;
    this.musicBus = null;
    this.musicCompressor = null;

    this.masterVolume = 1.0;
    this.busVolumes = { sfx: 1.0, ambience: 1.0, music: 1.0 };

    // state
    this.initialized = false;
    this.initPromise = null;

    // Ambience emitters currently looping: id -> { gain, panner, filter, source, def }
    this.activeAmbiences = new Map();

    // Music playback state
    this.musicLayers = {
      explore: null, // { source, gain }
      combat: null,
    };
    this.currentMusicState = null;
    this.musicMeta = { bpm: 118, start_t: 0 };
    this._musicCrossfadeScheduled = false;

    // Track active voice playback start times per source (player/entity) and sound_type:
    // key: `${sourceId}_${sound_type}` -> { startTime: number, duration: number }
    this.activeVoices = new Map();

    // Bind for use as event listeners
    this._unlockHandler = this._unlockHandler.bind(this);
    window.addEventListener('click', this._unlockHandler, { once: false });
  }

  // ---------------------------------------------------------------------
  // Context & graph bootstrap
  // ---------------------------------------------------------------------

  _unlockHandler() {
    if (!this.audioContext) {
      this.ensureContext();
    } else if (this.audioContext.state === 'suspended') {
      this.audioContext.resume().catch(() => {});
    }
  }

  /**
   * Lazily creates the AudioContext + static bus graph.
   * Safe to call multiple times.
   */
  ensureContext() {
    if (this.audioContext) {
      if (this.audioContext.state === 'suspended') {
        this.audioContext.resume().catch(() => {});
      }
      return this.audioContext;
    }

    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.audioContext = new Ctx();

    this.masterGain = this.audioContext.createGain();
    this.masterGain.gain.value = this.masterVolume;
    this.masterGain.connect(this.audioContext.destination);

    this.sfxBus = this.audioContext.createGain();
    this.sfxBus.gain.value = this.busVolumes.sfx;
    this.sfxBus.connect(this.masterGain);

    this.ambienceBus = this.audioContext.createGain();
    this.ambienceBus.gain.value = this.busVolumes.ambience;
    this.ambienceBus.connect(this.masterGain);

    this.musicBus = this.audioContext.createGain();
    this.musicBus.gain.value = this.busVolumes.music;

    // Subtle compressor on the music bus to glue cross-faded layers.
    this.musicCompressor = this.audioContext.createDynamicsCompressor();
    this.musicCompressor.threshold.value = -18;
    this.musicCompressor.knee.value = 24;
    this.musicCompressor.ratio.value = 3;
    this.musicCompressor.attack.value = 0.01;
    this.musicCompressor.release.value = 0.25;

    this.musicBus.connect(this.musicCompressor);
    this.musicCompressor.connect(this.masterGain);

    return this.audioContext;
  }

  /**
   * Pre-fetch & decode all audio assets. Call once at app startup
   * (ideally gated behind the first user click or first WS message).
   */
  async init() {
    if (this.initPromise) return this.initPromise;

    this.initPromise = (async () => {
      this.ensureContext();

      const entries = Object.entries(SOUND_MANIFEST);
      await Promise.all(
        entries.map(async ([key, filename]) => {
          try {
            const buffer = await this._loadAndDecode(`${ASSET_BASE}${filename}`);
            this.buffers.set(key, buffer);
          } catch (err) {
            console.error(`[AudioManager] Failed to load "${key}" (${filename}):`, err);
          }
        })
      );

      this.initialized = true;
    })();

    return this.initPromise;
  }

  async _loadAndDecode(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status} fetching ${url}`);
    const arrayBuffer = await res.arrayBuffer();
    return this.audioContext.decodeAudioData(arrayBuffer);
  }

  // ---------------------------------------------------------------------
  // Volume controls
  // ---------------------------------------------------------------------

  setMasterVolume(v) {
    this.masterVolume = clamp(v, 0, 1);
    if (this.masterGain) {
      this.masterGain.gain.setTargetAtTime(this.masterVolume, this.audioContext.currentTime, 0.01);
    }
  }

  setBusVolume(bus, v) {
    const val = clamp(v, 0, 1);
    this.busVolumes[bus] = val;
    const node = { sfx: this.sfxBus, ambience: this.ambienceBus, music: this.musicBus }[bus];
    if (node) {
      node.gain.setTargetAtTime(val, this.audioContext.currentTime, 0.01);
    }
  }

  // ---------------------------------------------------------------------
  // One-shot spatial SFX
  // ---------------------------------------------------------------------

  /**
   * Plays a one-shot spatialized sound effect relative to the listener.
   * @param {object} event { sound_type, x, y, radius_min, radius_max, base_gain, occl_gain, lpf_cutoff }
   * @param {object} listenerPos { x, y }
   */
  playSpatialSound(event, listenerPos) {
    if (!this.audioContext) this.ensureContext();
    if (this.audioContext.state === 'suspended') {
      this.audioContext.resume().catch(() => {});
    }

    const buffer = this.buffers.get(event.sound_type);
    if (!buffer) {
      console.warn(`[AudioManager] Unknown or unloaded sound_type "${event.sound_type}"`);
      return;
    }

    const radiusMin = event.radius_min ?? 0;
    const radiusMax = event.radius_max ?? 1000;

    const dx = event.x - listenerPos.x;
    const dy = event.y - listenerPos.y;
    const dist = Math.hypot(dx, dy);

    if (dist >= radiusMax) return; // culled, outside hearing range

    let distFactor;
    if (dist <= radiusMin) {
      distFactor = 1.0;
    } else {
      const t = (radiusMax - dist) / (radiusMax - radiusMin);
      distFactor = Math.pow(t, 2.0);
    }

    const baseGain = event.base_gain ?? 1.0;
    const occlGain = event.occl_gain ?? 1.0;
    const totalGain = baseGain * distFactor * occlGain;

    if (totalGain <= 0.0005) return; // inaudible, skip graph creation entirely

    const pan = clamp(dx / (radiusMax * 0.75), -1.0, 1.0);
    const lpfCutoff = event.lpf_cutoff || 20000;

    const ctx = this.audioContext;
    const now = ctx.currentTime;

    // Per-player voice cut & retrigger:
    // When a player fires rapidly, immediately stop their previous sound instance
    // (with a tiny 2ms fade-out to prevent speaker click/pop) and play the new sound
    // cleanly from the start (offset 0).
    const sourceId = event.source_id || 'default';
    const voiceKey = `${sourceId}_${event.sound_type}`;

    const prevVoice = this.activeVoices.get(voiceKey);
    if (prevVoice) {
      try {
        // Micro ramp down to avoid audio click on abrupt cutoff
        if (prevVoice.gainNode) {
          prevVoice.gainNode.gain.cancelScheduledValues(now);
          prevVoice.gainNode.gain.setValueAtTime(prevVoice.gainNode.gain.value, now);
          prevVoice.gainNode.gain.linearRampToValueAtTime(0.0001, now + 0.003);
        }
        if (prevVoice.source) {
          prevVoice.source.stop(now + 0.003);
        }
      } catch (_) {
        // already stopped
      }
      this.activeVoices.delete(voiceKey);
    }

    // --- Build disposable node chain: Source -> Panner -> LPF -> Gain -> sfxBus
    const source = ctx.createBufferSource();
    source.buffer = buffer;

    let pannerNode;
    if (typeof ctx.createStereoPanner === 'function') {
      pannerNode = ctx.createStereoPanner();
      pannerNode.pan.value = pan;
    } else {
      // Fallback: emulate pan with a simple gain (mono-safe no-op passthrough)
      pannerNode = ctx.createGain();
      pannerNode.gain.value = 1.0;
    }

    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = lpfCutoff;
    filter.Q.value = 0.0001;

    const gainNode = ctx.createGain();
    gainNode.gain.value = totalGain;

    source.connect(pannerNode);
    pannerNode.connect(filter);
    filter.connect(gainNode);
    gainNode.connect(this.sfxBus);

    // Track active voice node references so the next shot from this player can stop it
    this.activeVoices.set(voiceKey, { source, gainNode, startTime: now, duration: buffer.duration });

    source.onended = () => {
      source.disconnect();
      pannerNode.disconnect();
      filter.disconnect();
      gainNode.disconnect();
      const cur = this.activeVoices.get(voiceKey);
      if (cur && cur.source === source) {
        this.activeVoices.delete(voiceKey);
      }
    };

    // Always start the second sound cleanly from the start of the track
    source.start(now, 0);
  }

  // ---------------------------------------------------------------------
  // Ambience loop management
  // ---------------------------------------------------------------------

  /**
   * Call every tick with the list of active ambience emitters near the
   * player and the listener position. Emitters not present in `emitters`
   * are stopped & removed; new ones are spun up; existing ones are
   * re-panned / re-gained in place.
   *
   * @param {Array<{id:string, sound_type:string, x:number, y:number, radius_min:number, radius_max:number, base_gain?:number}>} emitters
   * @param {{x:number,y:number}} listenerPos
   */
  updateAmbience(emitters, listenerPos) {
    if (!this.audioContext) return;
    const ctx = this.audioContext;
    const liveIds = new Set(emitters.map((e) => e.id));

    // Remove stale emitters
    for (const [id, node] of this.activeAmbiences.entries()) {
      if (!liveIds.has(id)) {
        this._stopAmbienceNode(node);
        this.activeAmbiences.delete(id);
      }
    }

    for (const emitter of emitters) {
      const radiusMin = emitter.radius_min ?? 0;
      const radiusMax = emitter.radius_max ?? 800;
      const dx = emitter.x - listenerPos.x;
      const dy = emitter.y - listenerPos.y;
      const dist = Math.hypot(dx, dy);

      const culled = dist >= radiusMax;
      let distFactor = 0;
      if (!culled) {
        distFactor = dist <= radiusMin
          ? 1.0
          : Math.pow((radiusMax - dist) / (radiusMax - radiusMin), 2.0);
      }
      const baseGain = emitter.base_gain ?? 1.0;
      const targetGain = culled ? 0 : baseGain * distFactor;
      const pan = clamp(dx / (radiusMax * 0.75), -1.0, 1.0);

      let node = this.activeAmbiences.get(emitter.id);

      if (!node) {
        const buffer = this.buffers.get(emitter.sound_type);
        if (!buffer) continue;

        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.loop = true;

        let pannerNode;
        if (typeof ctx.createStereoPanner === 'function') {
          pannerNode = ctx.createStereoPanner();
          pannerNode.pan.value = pan;
        } else {
          pannerNode = ctx.createGain();
        }

        const filter = ctx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.value = emitter.lpf_cutoff || 20000;

        const gainNode = ctx.createGain();
        gainNode.gain.value = 0; // fade in

        source.connect(pannerNode);
        pannerNode.connect(filter);
        filter.connect(gainNode);
        gainNode.connect(this.ambienceBus);
        source.start();

        node = { source, panner: pannerNode, filter, gain: gainNode };
        this.activeAmbiences.set(emitter.id, node);
      }

      const now = ctx.currentTime;
      node.gain.gain.setTargetAtTime(targetGain, now, 0.15);
      if (node.panner.pan) {
        node.panner.pan.setTargetAtTime(pan, now, 0.15);
      }
    }
  }

  _stopAmbienceNode(node) {
    const now = this.audioContext.currentTime;
    try {
      node.gain.gain.setTargetAtTime(0, now, 0.1);
      node.source.stop(now + 0.4);
    } catch (_) { /* already stopped */ }
    node.source.onended = () => {
      node.source.disconnect();
      node.panner.disconnect();
      node.filter.disconnect();
      node.gain.disconnect();
    };
  }

  // ---------------------------------------------------------------------
  // Music: quantized beat-synced cross-fade
  // ---------------------------------------------------------------------

  /**
   * @param {{state:'explore'|'combat'|'extraction'|'lobby', bpm:number, start_t:number}} musicPayload
   */
  updateMusic(musicPayload) {
    if (!this.audioContext) this.ensureContext();
    const { state, bpm = 118, start_t = 0 } = musicPayload;

    this.musicMeta = { bpm, start_t };

    if (state === this.currentMusicState) return;

    // "extraction" / "lobby" simply duck music to silence (or could map
    // to dedicated stingers later); for now treat as "no bed music".
    if (state === 'extraction' || state === 'lobby') {
      this._fadeAllMusicTo(0);
      this.currentMusicState = state;
      return;
    }

    const trackKey = MUSIC_TRACK_KEYS[state];
    if (!trackKey) {
      console.warn(`[AudioManager] Unknown music state "${state}"`);
      return;
    }

    this._crossfadeToState(state, bpm, start_t);
    this.currentMusicState = state;
  }

  /**
   * Ensures a given layer ('explore' | 'combat') has a running, looping
   * BufferSource aligned to the server-authoritative start_t, returning it.
   */
  _ensureMusicLayer(layerName, bpm, startT) {
    let layer = this.musicLayers[layerName];
    if (layer) return layer;

    const buffer = this.buffers.get(MUSIC_TRACK_KEYS[layerName]);
    if (!buffer) return null;

    const ctx = this.audioContext;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.loop = true;

    const gainNode = ctx.createGain();
    gainNode.gain.value = 0;

    source.connect(gainNode);
    gainNode.connect(this.musicBus);

    // Align playback phase to the server's start_t so both explore/combat
    // beds stay bar-locked to one another, enabling click-free swaps.
    const now = ctx.currentTime;
    const elapsedSinceStart = Math.max(0, (Date.now() / 1000) - startT);
    const loopOffset = buffer.duration > 0 ? elapsedSinceStart % buffer.duration : 0;

    source.start(now, loopOffset);

    layer = { source, gain: gainNode };
    this.musicLayers[layerName] = layer;
    return layer;
  }

  /**
   * Computes the next beat-quantized AudioContext time (next downbeat)
   * given bpm + a server-provided epoch start_t (seconds).
   */
  _nextQuantizedTime(bpm, startT, barLength = 4) {
    const ctx = this.audioContext;
    const beatDur = 60 / bpm;
    const barDur = beatDur * barLength;

    const nowEpoch = Date.now() / 1000;
    const elapsed = nowEpoch - startT;
    const barsElapsed = Math.floor(elapsed / barDur) + 1; // next upcoming bar
    const nextBarEpoch = startT + barsElapsed * barDur;

    const deltaToNextBar = nextBarEpoch - nowEpoch;
    return ctx.currentTime + Math.max(0, deltaToNextBar);
  }

  _crossfadeToState(targetState, bpm, startT) {
    const ctx = this.audioContext;

    const incoming = this._ensureMusicLayer(targetState, bpm, startT);
    if (!incoming) return;

    const fadeTime = this._nextQuantizedTime(bpm, startT);
    const rampDur = 1.2; // seconds, musical crossfade length

    // Fade target layer in
    incoming.gain.gain.cancelScheduledValues(ctx.currentTime);
    incoming.gain.gain.setValueAtTime(incoming.gain.gain.value, ctx.currentTime);
    incoming.gain.gain.linearRampToValueAtTime(1.0, fadeTime + rampDur);
    incoming.gain.gain.setValueAtTime(incoming.gain.gain.value, fadeTime);
    incoming.gain.gain.linearRampToValueAtTime(1.0, fadeTime + rampDur);

    // Fade all other layers out
    for (const [name, layer] of Object.entries(this.musicLayers)) {
      if (name === targetState || !layer) continue;
      layer.gain.gain.cancelScheduledValues(ctx.currentTime);
      layer.gain.gain.setValueAtTime(layer.gain.gain.value, ctx.currentTime);
      layer.gain.gain.linearRampToValueAtTime(0.0, fadeTime + rampDur);
    }
  }

  _fadeAllMusicTo(targetGain, rampDur = 1.0) {
    const ctx = this.audioContext;
    const now = ctx.currentTime;
    for (const layer of Object.values(this.musicLayers)) {
      if (!layer) continue;
      layer.gain.gain.cancelScheduledValues(now);
      layer.gain.gain.setValueAtTime(layer.gain.gain.value, now);
      layer.gain.gain.linearRampToValueAtTime(targetGain, now + rampDur);
    }
  }

  // ---------------------------------------------------------------------
  // Teardown
  // ---------------------------------------------------------------------

  dispose() {
    window.removeEventListener('click', this._unlockHandler);

    for (const node of this.activeAmbiences.values()) {
      this._stopAmbienceNode(node);
    }
    this.activeAmbiences.clear();

    for (const layer of Object.values(this.musicLayers)) {
      if (!layer) continue;
      try {
        layer.source.stop();
      } catch (_) { /* no-op */ }
      layer.source.disconnect();
      layer.gain.disconnect();
    }
    this.musicLayers = { explore: null, combat: null };

    if (this.audioContext) {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }

    this.initialized = false;
    this.initPromise = null;
  }
}

export const audioManager = new AudioManager();
export default audioManager;
