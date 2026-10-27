import { audioManager } from './audio.js';
/**
 * Main client controller: WebSocket networking, Lobby & Leader Mission selector,
 * HUD, Interactive Puzzle Modals, and real-time WASD + Mouse Aim/Fire input loop.
 */

import { isoToWorld, screenInputToWorldDelta } from './iso.js';
import { IsometricRenderer } from './renderer.js';

const DEFAULT_CLASSES = {
  vanguard: { id: 'vanguard', name: 'Vanguard', title: 'Heavy Assault & Pointman', color: '#f87171', max_hp: 120, speed: 4.8, sight_range: 11.0, weapon: { name: 'Heavy Breacher Shotgun', damage: 14, fire_rate: 1.2, mag_size: 6, pellets: 5 }, skill: { name: 'Shockwave Shield', cooldown: 18.0, description: 'Deploys a kinetic blast pushing back enemies and blocking incoming projectiles.' }, passive: { name: 'Heavy Plating', description: 'Carries up to 24 WT and absorbs 20% reduced kinetic damage.' } },
  tech: { id: 'tech', name: 'Tech Specialist', title: 'Combat Engineer & Hacker', color: '#38bdf8', max_hp: 90, speed: 5.2, sight_range: 13.0, weapon: { name: 'Silenced Tactical SMG', damage: 8, fire_rate: 4.0, mag_size: 30, pellets: 1 }, skill: { name: 'Deployable Sentry', cooldown: 22.0, description: 'Deploys an autonomous turret that tracks and suppresses alien targets.' }, passive: { name: 'Hardware Bypass', description: 'Instantly solves security terminals without manual node alignment.' } },
  medic: { id: 'medic', name: 'Field Medic', title: 'Support & Bioregen Operator', color: '#4ade80', max_hp: 100, speed: 5.0, sight_range: 12.0, weapon: { name: 'Burst Bio-Carbine', damage: 10, fire_rate: 3.0, mag_size: 20, pellets: 1 }, skill: { name: 'Nanite Healing Field', cooldown: 20.0, description: 'Deploys a lingering regenerative nanite cloud restoring operative HP.' }, passive: { name: 'Combat Stimulants', description: 'Revives downed teammates 50% faster with bonus initial HP.' } },
  recon: { id: 'recon', name: 'Recon Scout', title: 'Long-Range Infiltrator', color: '#c084fc', max_hp: 85, speed: 5.6, sight_range: 16.0, weapon: { name: 'Suppressed Marksman Rifle', damage: 18, fire_rate: 1.5, mag_size: 10, pellets: 1 }, skill: { name: 'Tactical Sonar Pulse', cooldown: 16.0, description: 'Pings the sector revealing all enemy locations through walls.' }, passive: { name: 'Silent Footsteps', description: 'Movement generates 60% less sound radius on enemy hearing radar.' } },
};

let ws = null;
let localPlayerId = null;
let classesConfig = Object.assign({}, DEFAULT_CLASSES);
let missionsConfig = {};
let state = null;
let activePuzzle = null;
let activeChest = null;
let lastHudInvSig = '';

const keys = {
  up: false,
  down: false,
  left: false,
  right: false,
  shift: false,
};

function isLocalPlayerLightWeapon(p) {
  if (!p || !p.inventory) return true;
  const slot = p.inventory.active_weapon_slot || 'primary_weapon';
  if (slot === 'secondary_weapon') return true;
  const item = p.inventory.equipped?.[slot];
  if (!item) return false;
  const name = (item.name || '').toLowerCase();
  const tid = (item.template_id || '').toLowerCase();
  return name.includes('pistol') || name.includes('revolver') || tid.includes('pistol') || tid.includes('revolver') || (item.weight && item.weight <= 3);
}

const mouseScreen = { x: window.innerWidth * 0.5, y: window.innerHeight * 0.5 };
let mouseWorld = { x: 8, y: 8 };

// Expose state and debug helpers on window for tests and console
if (typeof window !== 'undefined') {
  window.__game = {
    get state() { return state; },
    get activeChest() { return activeChest; },
    get localPlayerId() { return localPlayerId; },
    keys,
    mouseScreen,
    send: (payload) => send(payload),
  };
}
let firing = false;

const camera = {
  x: 6.0,
  y: 8.0,
  screenW: window.innerWidth,
  screenH: window.innerHeight,
};

const canvas = document.getElementById('game-canvas');
const renderer = new IsometricRenderer(canvas, classesConfig);

// DOM Elements
const lobbyScreen = document.getElementById('lobby-screen');
const gameScreen = document.getElementById('game-screen');
const callsignInput = document.getElementById('callsign-input');
const saveNameBtn = document.getElementById('save-name-btn');
const playerListEl = document.getElementById('player-list');
const classGridEl = document.getElementById('class-grid');
const missionGridEl = document.getElementById('mission-grid');
const leaderStatusBanner = document.getElementById('leader-status-banner');
const launchBtn = document.getElementById('launch-btn');
const changeClassBtn = document.getElementById('change-class-btn');
const lobbySharedStash = document.getElementById('lobby-shared-stash');

// Class Selection Overlay
const classModal = document.getElementById('class-modal');
const closeClassBtn = document.getElementById('close-class-btn');
let classModalForced = false;

// Pre-Mission Preparation Screen
const prepScreen = document.getElementById('prep-screen');
const prepTitle = document.getElementById('prep-title');
const prepCrateList = document.getElementById('prep-crate-list');
const prepCountdown = document.getElementById('prep-countdown');
const prepBackBtn = document.getElementById('prep-back-btn');
const prepSlots = [0, 1, 2, 3].map((i) => document.getElementById(`prep-slot-${i}`));
let lastPrepSig = '';

// HUD & Chest Elements
const hudInventorySidebar = document.getElementById('hud-inventory-sidebar');
const hudInvWeight = document.getElementById('hud-inv-weight');
const hudInvWeightFill = document.getElementById('hud-inv-weight-fill');
const hudInvEquipped = document.getElementById('hud-equipped-slots');
const hudInvStorage = document.getElementById('hud-storage-list');
const chestModal = document.getElementById('chest-modal');
const chestTitle = document.getElementById('chest-title');
const chestBody = document.getElementById('chest-body');
const closeChestBtn = document.getElementById('close-chest-btn');

// HUD Elements
const hudMissionTitle = document.getElementById('hud-mission-title');
const hudObjectiveText = document.getElementById('hud-objective-text');
const hudHpFill = document.getElementById('hud-hp-fill');
const hudHpText = document.getElementById('hud-hp-text');
const hudAmmoText = document.getElementById('hud-ammo-text');
const hudSkillName = document.getElementById('hud-skill-name');
const hudSkillStatus = document.getElementById('hud-skill-status');
const hudEvents = document.getElementById('hud-events');
const endBanner = document.getElementById('end-banner');
const endTitle = document.getElementById('end-title');
const endSub = document.getElementById('end-sub');
const returnLobbyBtn = document.getElementById('return-lobby-btn');

// Puzzle Modal Elements
const puzzleModal = document.getElementById('puzzle-modal');
const puzzleTitle = document.getElementById('puzzle-title');
const puzzleBody = document.getElementById('puzzle-body');
const closePuzzleBtn = document.getElementById('close-puzzle-btn');

function resizeCanvas() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  camera.screenW = canvas.width;
  camera.screenH = canvas.height;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

function send(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  }
}

function getLocalPlayer() {
  if (!state || !state.players) return null;
  return state.players.find((p) => p.id === localPlayerId) || null;
}

function connectWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'welcome') {
      localPlayerId = msg.player_id;
      classesConfig = msg.classes || {};
      missionsConfig = msg.missions || {};
      renderer.setClassesConfig(classesConfig);
      state = msg.state;
      const me = getLocalPlayer();
      if (me && callsignInput) {
        callsignInput.value = me.name;
      }
      if (state.phase === 'prep') {
        lobbyScreen.classList.add('hidden');
        prepScreen.classList.remove('hidden');
        renderPrepUI();
      } else {
        renderLobbyUI();
      }
      maybeShowClassModal();
    } else if (msg.type === 'state') {
      const prevPhase = state ? state.phase : null;
      state = msg;

      // Handle server-synchronized Web Audio events
      const me = getLocalPlayer();
      if (me && msg.audio_events && msg.audio_events.length > 0) {
        for (const evt of msg.audio_events) {
          audioManager.playSpatialSound(evt, { x: me.x, y: me.y });
        }
      }

      // Handle server-synchronized dynamic music
      if (msg.music) {
        audioManager.updateMusic(msg.music);
      }
      if (state.phase === 'lobby' || state.phase === 'prep') {
        gameScreen.classList.add('hidden');
        puzzleModal.classList.add('hidden');
        if (chestModal) chestModal.classList.add('hidden');
        activePuzzle = null;
        activeChest = null;
        if (state.phase === 'lobby') {
          lobbyScreen.classList.remove('hidden');
          prepScreen.classList.add('hidden');
          renderLobbyUI();
        } else {
          lobbyScreen.classList.add('hidden');
          prepScreen.classList.remove('hidden');
          renderPrepUI();
        }
        maybeShowClassModal();
      } else {
        lobbyScreen.classList.add('hidden');
        prepScreen.classList.add('hidden');
        classModal.classList.add('hidden');
        gameScreen.classList.remove('hidden');
        if (prevPhase === 'lobby' || prevPhase === 'prep') {
          lastHudInvSig = '';
          if (callsignInput) callsignInput.blur();
          renderer.resetExplored();
          const me = getLocalPlayer();
          if (me) {
            camera.x = me.x;
            camera.y = me.y;
          }
        }
        if (activeChest && state.map && state.map.crates) {
          const updatedCrate = state.map.crates.find((c) => c.id === activeChest.id);
          if (updatedCrate) {
            activeChest = updatedCrate;
            renderChestModal();
          }
        }
        updateHUD();
      }
    } else if (msg.type === 'interact_result' || msg.type === 'open_puzzle') {
      if (msg.puzzle) {
        activePuzzle = msg.puzzle;
        keys.up = keys.down = keys.left = keys.right = false;
        firing = false;
        renderPuzzleModal();
      } else if ((msg.action === 'open_chest' || msg.interact_type === 'open_chest') && msg.crate) {
        activeChest = msg.crate;
        keys.up = keys.down = keys.left = keys.right = false;
        firing = false;
        renderChestModal();
      }
    } else if (msg.type === 'chest_update') {
      if (msg.crate) {
        activeChest = msg.crate;
        renderChestModal();
      }
    } else if (msg.type === 'puzzle_update') {
      if (msg.puzzle) {
        activePuzzle = msg.puzzle;
      }
      if (msg.solved) {
        puzzleModal.classList.add('hidden');
        activePuzzle = null;
        if (msg.unlocked_crate) {
          activeChest = msg.unlocked_crate;
          renderChestModal();
        }
      } else if (activePuzzle) {
        renderPuzzleModal();
      }
    }
  });

  ws.addEventListener('close', () => {
    setTimeout(connectWebSocket, 1500);
  });
}

let lastLobbySig = '';
let localLockNotice = '';

function getClassPortraitSvg(classId, color) {
  const visorColor = '#38bdf8';
  const crest =
    classId === 'vanguard'
      ? '<rect x="18" y="8" width="28" height="8" rx="3" fill="' + color + '"/>'
      : classId === 'tech'
      ? '<circle cx="48" cy="18" r="5" fill="#fde047"/><line x1="48" y1="6" x2="48" y2="14" stroke="#fde047" stroke-width="2"/>'
      : classId === 'medic'
      ? '<path d="M29 9 H35 V13 H39 V19 H35 V23 H29 V19 H25 V13 H29 Z" fill="#22c55e"/>'
      : '<polygon points="32,6 42,16 22,16" fill="' + color + '"/>';
  return `
    <svg width="62" height="62" viewBox="0 0 64 64" style="flex-shrink:0;background:#090e1a;border:2px solid ${color};border-radius:8px;padding:3px;">
      <path d="M12 58 C12 42, 52 42, 52 58 Z" fill="${color}" opacity="0.85"/>
      <rect x="18" y="16" width="28" height="26" rx="9" fill="#1e293b" stroke="${color}" stroke-width="2.5"/>
      <rect x="22" y="23" width="20" height="8" rx="3" fill="${visorColor}"/>
      ${crest}
    </svg>
  `;
}

const CLASS_CAPACITIES = { vanguard: 24, tech: 18, medic: 15, recon: 12 };
const SLOT_DEFS = [
  { key: 'primary_weapon', label: 'Primary' },
  { key: 'secondary_weapon', label: 'Secondary' },
  { key: 'special_ability', label: 'Ability' },
  { key: 'quick_use', label: 'Quick-Use' },
];

function maybeShowClassModal() {
  const me = getLocalPlayer();
  if (!me || !state || (state.phase !== 'lobby' && state.phase !== 'prep')) {
    classModal.classList.add('hidden');
    return;
  }
  if (!me.class_chosen) {
    classModalForced = true;
    if (classModal.classList.contains('hidden')) renderClassModal();
  } else if (classModalForced) {
    classModalForced = false;
    classModal.classList.add('hidden');
  }
}

function renderClassModal() {
  const me = getLocalPlayer();
  closeClassBtn.style.display = me && me.class_chosen ? '' : 'none';
  classGridEl.innerHTML = '';
  const configs = Object.keys(classesConfig).length > 0 ? classesConfig : DEFAULT_CLASSES;
  for (const cls of Object.values(configs)) {
    const selected = me && me.class_chosen && me.class_id === cls.id;
    const cap = CLASS_CAPACITIES[cls.id] || 15;
    const card = document.createElement('div');
    card.className = `card class-card ${selected ? 'selected' : ''}`;
    card.style.borderColor = selected ? cls.color : '';
    card.style.cursor = 'pointer';
    card.innerHTML = `
      <div style="display:flex;gap:10px;align-items:center;">
        ${getClassPortraitSvg(cls.id, cls.color)}
        <div>
          <div class="class-title" style="color:${cls.color}">${cls.name}</div>
          <div class="class-role">${cls.title || ''}</div>
        </div>
      </div>
      <div class="stats-row">
        <span>HP: <b>${cls.max_hp}</b></span>
        <span>SPD: <b>${cls.speed}</b></span>
        <span>CARRY: <b style="color:#38bdf8">${cap} WT</b></span>
      </div>
      <div class="weapon-box"><strong>Weapon:</strong> ${cls.weapon.name}</div>
      <div class="skill-box"><strong>Skill:</strong> ${cls.skill.name} (${cls.skill.cooldown}s)<br/><small>${cls.skill.description}</small></div>
      <div class="passive-box"><strong>Passive:</strong> ${cls.passive.name} — <small>${cls.passive.description}</small></div>
      <button class="btn-primary" style="margin-top:auto;padding:8px;">${selected ? '✓ CURRENT CLASS' : 'SELECT ' + cls.name.toUpperCase()}</button>
    `;
    card.addEventListener('click', () => {
      if (me) {
        me.class_id = cls.id;
        me.class_chosen = true;
      }
      send({ type: 'select_class', class_id: cls.id });
      classModalForced = false;
      classModal.classList.add('hidden');
      classModal.style.display = 'none';
      renderLobbyUI(true);
    });
    classGridEl.appendChild(card);
  }
  classModal.classList.remove('hidden');
  classModal.style.display = 'flex';
}
window.openClassModal = renderClassModal;

function renderLobbyUI(force = false) {
  if (!state) return;
  const sig = JSON.stringify({
    leader: state.leader_id,
    mission: state.selected_mission_id,
    level: state.selected_level_id,
    lock: state.last_lock_reason,
    localLock: localLockNotice,
    stash: state.shared_stash || [],
    players: (state.players || []).map((p) => [p.id, p.name, p.class_id, p.class_chosen]),
    levels: (state.levels || []).map((l) => [l.level_id, l.tested, l.completed, l.unlocked, l.lock_reason]),
  });
  if (!force && sig === lastLobbySig) return;
  lastLobbySig = sig;

  const isLeader = state.leader_id === localPlayerId;

  // 1. Player Roster (class shown per player)
  playerListEl.innerHTML = '';
  for (const p of state.players || []) {
    const cls = classesConfig[p.class_id] || { name: p.class_id, color: '#38bdf8' };
    const li = document.createElement('div');
    li.className = 'player-row';
    const leaderBadge = p.id === state.leader_id ? '<span class="badge-leader">★ LEADER</span>' : '';
    const youBadge = p.id === localPlayerId ? '<span class="badge-you">(YOU)</span>' : '';
    li.innerHTML = `
      <div class="player-info">
        <span class="class-dot" style="background:${cls.color}"></span>
        <strong>${p.name}</strong> ${youBadge} ${leaderBadge}
      </div>
      <div class="player-class-tag" style="color:${cls.color}">${p.class_chosen ? cls.name : 'Choosing...'}</div>
    `;
    playerListEl.appendChild(li);
  }

  // 2. Shared loot list — information only (scrollable, no interaction)
  const stash = state.shared_stash || [];
  lobbySharedStash.innerHTML =
    stash.length === 0
      ? '<div style="color:#64748b;font-size:0.75rem;">Shared loot is empty. Extract loot from missions!</div>'
      : stash
          .map(
            (st) => `
        <div class="stash-item">
          <div>
            <strong>${st.name}</strong>
            <span style="color:#94a3b8;font-size:0.7rem;">(${st.weight} wt • ${(st.category || '').replace('_', ' ')})</span>
          </div>
          <span style="color:#fbbf24;font-weight:800;">x${st.count}</span>
        </div>`
          )
          .join('');

  // 3. Mission & Persisted WFC Level Selection (Leader Controlled)
  const lockMsg = localLockNotice || state.last_lock_reason;
  if (lockMsg) {
    leaderStatusBanner.textContent = `🔒 LEVEL LOCKED: ${lockMsg}`;
    leaderStatusBanner.style.borderColor = '#f59e0b';
    leaderStatusBanner.style.color = '#fde68a';
  } else {
    leaderStatusBanner.style.borderColor = '';
    leaderStatusBanner.style.color = '';
    leaderStatusBanner.textContent = isLeader
      ? 'You are the Squad Leader. Select an unlocked level or generate a new WFC level (harder levels require completing an easier level first).'
      : 'Waiting for the Squad Leader to select a level and launch...';
  }

  const levels = state.levels || [];
  const selectedLevel =
    levels.find((l) => l.level_id === state.selected_level_id) ||
    levels.find((l) => l.mission_id === state.selected_mission_id) ||
    null;

  missionGridEl.innerHTML = '';
  for (const m of Object.values(missionsConfig)) {
    const missionLevels = levels.filter((l) => l.mission_id === m.id);
    const anyMissionUnlocked = missionLevels.some((l) => l.unlocked !== false);
    const missionLockReason =
      !anyMissionUnlocked && missionLevels.length > 0
        ? missionLevels[0].lock_reason
        : m.unlock_requirement;

    const card = document.createElement('div');
    const isMissionSelected = state.selected_mission_id === m.id;
    card.className = `card mission-card ${isMissionSelected ? 'selected' : ''} ${!isLeader || !anyMissionUnlocked ? 'disabled-card' : ''}`;

    const missionLockBanner = !anyMissionUnlocked && missionLockReason
      ? `<div style="margin:6px 0;padding:6px 10px;border-radius:6px;background:rgba(245,158,11,0.15);border:1px solid #f59e0b;color:#fcd34d;font-size:0.78rem;font-weight:600;">
           🔒 LOCKED — ${missionLockReason}
         </div>`
      : '';

    const levelsHtml = missionLevels
      .map((lvl) => {
        const isSel = selectedLevel && selectedLevel.level_id === lvl.level_id;
        const isUnlocked = lvl.unlocked !== false;
        let statusLabel = '';
        let statusColor = '#34d399';
        if (!isUnlocked) {
          statusLabel = '🔒 LOCKED';
          statusColor = '#f87171';
        } else if (lvl.completed) {
          statusLabel = isSel ? '✓ SELECTED • ★ COMPLETED' : '★ COMPLETED';
          statusColor = '#34d399';
        } else if (lvl.tested) {
          statusLabel = isSel ? '✓ SELECTED • COMBAT READY' : '✓ VERIFIED (COMBAT)';
          statusColor = '#38bdf8';
        } else {
          statusLabel = isSel ? '✓ SELECTED • TESTING PHASE' : '⚠ TESTING PHASE';
          statusColor = '#fbbf24';
        }
        const sizes = Array.from(
          new Set((lvl.segments || []).map((s) => `${s.grid_w || 1}x${s.grid_h || 1}`))
        ).join(', ');
        const reqHtml =
          !isUnlocked && lvl.lock_reason
            ? `<div style="color:#fca5a5;font-size:0.72rem;margin-top:3px;font-weight:600;">↳ Requires: ${lvl.lock_reason}</div>`
            : '';
        return `
          <div class="level-item" data-level-id="${lvl.level_id}" data-unlocked="${isUnlocked}" data-lock-reason="${lvl.lock_reason || ''}" style="margin-top:6px;padding:8px 10px;border-radius:6px;border:2px solid ${isSel ? '#38bdf8' : isUnlocked ? '#1e293b' : '#451a1a'};background:${isSel ? 'rgba(56,189,248,0.16)' : isUnlocked ? '#0f172a' : 'rgba(127,29,29,0.18)'};display:flex;justify-content:space-between;align-items:center;font-size:0.78rem;cursor:${isLeader && isUnlocked ? 'pointer' : 'not-allowed'};">
            <div>
              <strong>${lvl.name}</strong> <span style="color:#94a3b8">(Seed ${lvl.seed})</span><br/>
              <span style="color:#94a3b8">${lvl.segment_count} Segments (${sizes}) • ${lvl.connector_count} Connectors</span>
              ${reqHtml}
            </div>
            <span style="color:${statusColor};font-weight:700;font-size:0.72rem;text-align:right;">${statusLabel}</span>
          </div>
        `;
      })
      .join('');

    card.innerHTML = `
      <div class="card-header">
        <span class="mission-title">${anyMissionUnlocked ? '🔓' : '🔒'} ${m.name}</span>
        <span class="difficulty-tag diff-${m.difficulty.toLowerCase()}">${m.difficulty}</span>
      </div>
      <p class="mission-desc">${m.description}</p>
      ${missionLockBanner}
      <div class="segments-preview">${levelsHtml}</div>
      ${
        isLeader && anyMissionUnlocked
          ? `<button class="btn-secondary gen-level-btn" data-mission-id="${m.id}" style="margin-top:8px;width:100%;font-size:0.78rem;border-color:#38bdf8;color:#38bdf8;">+ GENERATE NEW WFC LEVEL (ENTERS TESTING PHASE)</button>`
          : ''
      }
    `;

    if (isLeader) {
      card.querySelectorAll('.level-item').forEach((el) => {
        el.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const unlocked = el.dataset.unlocked === 'true';
          if (!unlocked) {
            localLockNotice = el.dataset.lockReason || 'Complete an easier level in the list first.';
            renderLobbyUI(true);
            return;
          }
          localLockNotice = '';
          const levelId = el.dataset.levelId;
          state.selected_level_id = levelId;
          state.selected_mission_id = m.id;
          send({ type: 'select_mission', level_id: levelId });
          send({ type: 'prepare_mission' });
          renderLobbyUI(true);
        });
      });
      const genBtn = card.querySelector('.gen-level-btn');
      if (genBtn) {
        genBtn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          localLockNotice = '';
          send({ type: 'generate_level', mission_id: m.id });
        });
      }
      card.addEventListener('click', () => {
        if (!anyMissionUnlocked) {
          localLockNotice = missionLockReason || 'Complete an easier mission first to unlock.';
          renderLobbyUI(true);
          return;
        }
        localLockNotice = '';
        const firstUnlocked = missionLevels.find((l) => l.unlocked !== false);
        if (firstUnlocked) {
          state.selected_level_id = firstUnlocked.level_id;
          state.selected_mission_id = m.id;
          send({ type: 'select_mission', level_id: firstUnlocked.level_id });
          send({ type: 'prepare_mission' });
        } else {
          send({ type: 'select_mission', mission_id: m.id });
          send({ type: 'prepare_mission' });
        }
        renderLobbyUI(true);
      });
    }
    missionGridEl.appendChild(card);
  }

  launchBtn.disabled = !isLeader || (selectedLevel && selectedLevel.unlocked === false);
  if (!isLeader) {
    launchBtn.textContent = 'WAITING FOR LEADER TO PREPARE...';
  } else if (selectedLevel && !selectedLevel.tested) {
    launchBtn.textContent = `PREPARE TO LAUNCH: ${selectedLevel.name.toUpperCase()} (TESTING PHASE)`;
  } else if (selectedLevel) {
    launchBtn.textContent = `PREPARE TO LAUNCH: ${selectedLevel.name.toUpperCase()}`;
  } else {
    launchBtn.textContent = 'PREPARE TO LAUNCH';
  }
}

function itemChipHtml(item, ownerId, canEdit) {
  if (!item) return '<span style="color:#475569">— empty —</span>';
  const back =
    canEdit && item.category !== 'special_ability'
      ? `<button class="mini-btn prep-return-btn" data-item-id="${item.id}" data-owner="${ownerId}" title="Return to crate">↩</button>`
      : '';
  return `<span><strong>${item.name}</strong> <span style="color:#38bdf8">${item.weight || 1}wt</span></span>${back}`;
}

function renderPrepUI(force = false) {
  if (!state) return;
  const timer = Number(state.prep_timer || 0);
  if (timer > 0) {
    prepCountdown.classList.remove('hidden');
    prepCountdown.textContent = String(Math.ceil(timer));
  } else {
    prepCountdown.classList.add('hidden');
  }

  const sig = JSON.stringify({
    stash: state.shared_stash || [],
    leader: state.leader_id,
    players: (state.players || []).map((p) => [p.id, p.name, p.class_id, p.ready, p.inventory]),
  });
  if (!force && sig === lastPrepSig) return;
  lastPrepSig = sig;

  const isLeader = state.leader_id === localPlayerId;
  prepBackBtn.style.display = isLeader ? '' : 'none';
  const lvl = (state.levels || []).find((l) => l.level_id === state.selected_level_id);
  prepTitle.textContent = `PREPARE TO LAUNCH — ${lvl ? lvl.name.toUpperCase() : 'MISSION'}`;

  // Center: combined squad loot crate
  const stash = state.shared_stash || [];
  prepCrateList.innerHTML =
    stash.length === 0
      ? '<div style="color:#64748b;font-size:0.8rem;">The squad loot crate is empty.</div>'
      : stash
          .map(
            (st) => `
        <div class="prep-crate-item" draggable="true" data-template-id="${st.template_id}">
          <div style="display:flex;justify-content:space-between;">
            <strong>${st.name}</strong><span style="color:#fbbf24;font-weight:800;">x${st.count}</span>
          </div>
          <span style="color:#94a3b8;font-size:0.7rem;">${st.weight} wt • ${(st.category || '').replace('_', ' ')}</span>
        </div>`
          )
          .join('');
  prepCrateList.querySelectorAll('.prep-crate-item').forEach((el) => {
    el.addEventListener('click', () => {
      send({ type: 'stash_to_player', template_id: el.dataset.templateId, target_player_id: localPlayerId });
    });
    el.addEventListener('dragstart', (ev) => {
      ev.dataTransfer.setData('text/plain', JSON.stringify({ source: 'prep_crate', template_id: el.dataset.templateId }));
    });
  });
  prepCrateList.ondragover = (ev) => ev.preventDefault();
  prepCrateList.ondrop = (ev) => {
    ev.preventDefault();
    try {
      const d = JSON.parse(ev.dataTransfer.getData('text/plain'));
      if (d.source === 'prep_inv') send({ type: 'player_to_stash', item_id: d.item_id, target_player_id: d.owner });
    } catch (_) {}
  };

  // Edges: one inventory grid per operative
  const players = state.players || [];
  prepSlots.forEach((slotEl, idx) => {
    const p = players[idx];
    if (!p) {
      slotEl.innerHTML = '<div class="prep-empty-slot">Open operative slot</div>';
      slotEl.ondragover = null;
      slotEl.ondrop = null;
      return;
    }
    const isMe = p.id === localPlayerId;
    const cls = classesConfig[p.class_id] || { name: p.class_id, color: '#38bdf8', title: '' };
    const inv = p.inventory || { equipped: {}, storage: [], current_weight: 0, max_weight: 15 };
    const eq = inv.equipped || {};
    const pct = Math.min(100, Math.round(((inv.current_weight || 0) / Math.max(1, inv.max_weight || 15)) * 100));
    const storage = inv.storage || [];

    slotEl.innerHTML = `
      <div class="prep-player-card ${isMe ? 'is-me' : ''} ${p.ready ? 'is-ready' : ''}">
        <div style="display:flex;gap:10px;align-items:center;">
          ${getClassPortraitSvg(p.class_id, cls.color)}
          <div style="flex:1;">
            <div style="font-weight:800;">${p.name} ${isMe ? '<span class="badge-you">(YOU)</span>' : ''}</div>
            <div style="color:${cls.color};font-size:0.8rem;font-weight:700;">${cls.name}</div>
            <div style="font-size:0.72rem;color:#94a3b8;margin-top:3px;">Carry: <b style="color:#38bdf8">${inv.current_weight || 0} / ${inv.max_weight || 15} WT</b></div>
            <div class="inv-weight-bar-wrap" style="margin-top:3px;"><div class="inv-weight-bar-fill" style="width:${pct}%;background:${pct >= 90 ? '#ef4444' : '#38bdf8'}"></div></div>
          </div>
        </div>
        <div class="inv-section-label">EQUIPPED</div>
        <div class="prep-inv-grid">
          ${SLOT_DEFS.map(
            (sd) => `<div class="inv-slot-row" style="flex-direction:column;align-items:flex-start;gap:2px;">
              <span style="color:#94a3b8;font-size:0.64rem;">${sd.label}</span>
              <div style="display:flex;justify-content:space-between;width:100%;gap:4px;">${itemChipHtml(eq[sd.key], p.id, true)}</div>
            </div>`
          ).join('')}
        </div>
        <div class="inv-section-label">STORAGE (${storage.length} items)</div>
        <div class="inv-storage-list">
          ${
            storage.length === 0
              ? '<div style="color:#64748b;font-size:0.72rem;">Drop items here from the crate.</div>'
              : storage
                  .map(
                    (it) => `<div class="inv-storage-item" draggable="true" data-item-id="${it.id}" data-owner="${p.id}">
                      ${itemChipHtml(it, p.id, true)}
                    </div>`
                  )
                  .join('')
          }
        </div>
        ${
          isMe
            ? `<div style="display:flex;gap:6px;margin-top:auto;">
                 <button class="btn-secondary prep-min-btn" style="font-size:0.72rem;">⚡ Min Loadout</button>
                 <button class="btn-secondary prep-class-btn" style="font-size:0.72rem;">⇄ Class</button>
               </div>
               <button class="prep-ready-btn ${p.ready ? 'ready' : ''}">${p.ready ? '✓ READY — CLICK TO CANCEL' : 'READY UP'}</button>`
            : `<div class="prep-ready-btn ${p.ready ? 'ready' : ''}" style="cursor:default;text-align:center;">${p.ready ? '✓ READY' : 'PREPARING...'}</div>`
        }
      </div>
    `;

    const card = slotEl.querySelector('.prep-player-card');
    card.querySelectorAll('.prep-return-btn').forEach((btn) => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        send({ type: 'player_to_stash', item_id: btn.dataset.itemId, target_player_id: btn.dataset.owner });
      });
    });
    card.querySelectorAll('.inv-storage-item').forEach((el) => {
      el.addEventListener('dragstart', (ev) => {
        ev.dataTransfer.setData(
          'text/plain',
          JSON.stringify({ source: 'prep_inv', item_id: el.dataset.itemId, owner: el.dataset.owner })
        );
      });
    });
    card.addEventListener('dragover', (ev) => {
      ev.preventDefault();
      card.classList.add('drag-target');
    });
    card.addEventListener('dragleave', () => card.classList.remove('drag-target'));
    card.addEventListener('drop', (ev) => {
      ev.preventDefault();
      card.classList.remove('drag-target');
      try {
        const d = JSON.parse(ev.dataTransfer.getData('text/plain'));
        if (d.source === 'prep_crate') {
          send({ type: 'stash_to_player', template_id: d.template_id, target_player_id: p.id });
        }
      } catch (_) {}
    });
    const readyEl = card.querySelector('button.prep-ready-btn');
    if (readyEl) readyEl.addEventListener('click', () => send({ type: 'toggle_ready' }));
    const minEl = card.querySelector('.prep-min-btn');
    if (minEl) minEl.addEventListener('click', () => send({ type: 'min_loadout' }));
    const clsEl = card.querySelector('.prep-class-btn');
    if (clsEl) clsEl.addEventListener('click', () => renderClassModal());
  });
}

function updateHUD() {
  if (!state || !state.map) return;
  const me = getLocalPlayer();
  const isTest = Boolean(state.map.test_mode);
  const titlePrefix = isTest ? '[TESTING PHASE — NO ENEMIES/LOOT] ' : '';
  hudMissionTitle.textContent = `${titlePrefix}${state.map.level_name || state.map.mission_name} (${state.map.segments.length} Sectors)`;

  // Objective progress
  const puzzles = state.map.puzzles || [];
  const solvedCount = puzzles.filter((p) => p.solved).length;
  if (state.phase === 'dropship') {
    hudObjectiveText.textContent = `Dropship docking... Ramp opens in ${state.dropship_timer}s`;
  } else if (solvedCount < puzzles.length) {
    const nextPz = puzzles.find((p) => !p.solved);
    hudObjectiveText.textContent = `${isTest ? 'Test ' : ''}Objective (${solvedCount}/${puzzles.length}): ${nextPz ? nextPz.label : 'Complete Terminals'}`;
  } else {
    hudObjectiveText.textContent = isTest
      ? 'TEST OBJECTIVES UNLOCKED — Reach Extraction Pad to Verify Level!'
      : 'PRIMARY COMPLETE — All operatives proceed to Extraction Pad!';
  }

  if (me) {
    const cls = classesConfig[me.class_id];
    const hpPct = Math.max(0, Math.min(100, (me.hp / me.max_hp) * 100));
    hudHpFill.style.width = `${hpPct}%`;
    hudHpText.textContent = me.downed ? 'DOWNED (NEED REVIVE)' : `${me.hp} / ${me.max_hp} HP`;
    hudAmmoText.textContent =
      me.reload_timer > 0
        ? `RELOADING (${me.reload_timer.toFixed(1)}s)`
        : `${me.ammo} / ${me.max_ammo}${me.running ? (isLocalPlayerLightWeapon(me) ? ' [SPRINTING]' : ' [SPRINT: LIGHT WEAPONS ONLY]') : ''}`;

    if (cls) {
      hudSkillName.textContent = `[SPACE] ${cls.skill.name}`;
      if (me.skill_timer > 0) {
        hudSkillStatus.textContent = `ACTIVE (${me.skill_timer.toFixed(1)}s)`;
        hudSkillStatus.className = 'skill-ready';
      } else if (me.skill_cooldown > 0) {
        hudSkillStatus.textContent = `COOLDOWN (${me.skill_cooldown.toFixed(1)}s)`;
        hudSkillStatus.className = 'skill-cd';
      } else {
        hudSkillStatus.textContent = 'READY';
        hudSkillStatus.className = 'skill-ready';
      }
    }
    renderHudInventory(me);
  }

  // Event log
  hudEvents.innerHTML = '';
  for (const ev of state.events || []) {
    const div = document.createElement('div');
    div.className = 'event-line';
    div.textContent = `> ${ev}`;
    hudEvents.appendChild(div);
  }

  // Victory / Defeat banner
  if (state.phase === 'victory' || state.phase === 'defeat') {
    endBanner.classList.remove('hidden');
    if (state.phase === 'victory' && isTest) {
      endTitle.textContent = 'LEVEL TEST PASSED — LEVEL VERIFIED';
    } else {
      endTitle.textContent =
        state.phase === 'victory' ? 'EXTRACTION SUCCESSFUL' : 'SQUAD WIPE — MISSION FAILED';
    }
    endTitle.style.color = state.phase === 'victory' ? '#34d399' : '#f87171';
    endSub.textContent = `Mission Time: ${state.mission_time}s`;
  } else {
    endBanner.classList.add('hidden');
  }
}

function renderHudInventory(me) {
  const sidebar = hudInventorySidebar || document.getElementById('hud-inventory-sidebar');
  if (!sidebar || !me || !me.inventory) return;
  sidebar.style.display = 'flex';
  const inv = me.inventory;
  const sig = JSON.stringify(inv);
  if (sig === lastHudInvSig) return;
  lastHudInvSig = sig;

  const equippedEl = hudInvEquipped || document.getElementById('hud-equipped-slots');
  const storageEl = hudInvStorage || document.getElementById('hud-storage-list');
  const weightEl = hudInvWeight || document.getElementById('hud-inv-weight');
  const fillEl = hudInvWeightFill || document.getElementById('hud-inv-weight-fill');

  const pct = Math.min(100, Math.round(((inv.current_weight || 0) / Math.max(1, inv.max_weight || 15)) * 100));
  if (weightEl) {
    weightEl.textContent = `${inv.current_weight || 0} / ${inv.max_weight || 15} WT`;
  }
  if (fillEl) {
    fillEl.style.width = `${pct}%`;
    fillEl.style.background = pct >= 90 ? '#ef4444' : pct >= 70 ? '#f59e0b' : '#38bdf8';
  }

  const eq = inv.equipped || inv;
  const activeSlot = inv.active_weapon_slot || 'primary_weapon';
  const equippedRows = [
    {
      slot: 'primary_weapon',
      label: '[1] Primary Gun',
      item: eq.primary_weapon,
      active: activeSlot === 'primary_weapon',
      switchable: true,
    },
    {
      slot: 'secondary_weapon',
      label: '[2] Secondary Gun',
      item: eq.secondary_weapon,
      active: activeSlot === 'secondary_weapon',
      switchable: true,
    },
    {
      slot: 'special_ability',
      label: '[SPACE] Class Skill',
      item: eq.special_ability,
      active: false,
      switchable: false,
    },
    {
      slot: 'quick_use',
      label: '[F] Quick-Use Gear',
      item: eq.quick_use,
      active: false,
      switchable: false,
      usable: Boolean(eq.quick_use && eq.quick_use.consumable),
    },
  ];

  if (equippedEl) {
    equippedEl.innerHTML = equippedRows
      .map((r) => {
        const name = r.item ? r.item.name : 'Empty';
        const wStr = r.item && r.item.weight ? ` (${r.item.weight}wt)` : '';
        return `
          <div class="inv-slot-row ${r.active ? 'active-weapon' : ''}">
            <div>
              <span style="color:#94a3b8;font-size:0.66rem;">${r.label}</span><br/>
              <strong style="color:${r.item ? '#f8fafc' : '#64748b'}">${name}${wStr}</strong>
            </div>
            <div style="display:flex;gap:4px;">
              ${
                r.switchable && r.item && !r.active
                  ? `<button class="mini-btn inv-switch-btn" data-slot="${r.slot}">Use</button>`
                  : ''
              }
              ${
                r.usable
                  ? `<button class="mini-btn inv-use-quick-btn">Use [F]</button>`
                  : ''
              }
              ${
                r.item && r.slot !== 'special_ability'
                  ? `<button class="mini-btn inv-unequip-btn" data-slot="${r.slot}">Store</button>`
                  : ''
              }
            </div>
          </div>
        `;
      })
      .join('');

    equippedEl.querySelectorAll('.inv-switch-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        send({ type: 'inventory_action', action: 'switch_weapon', slot: btn.dataset.slot });
      });
    });
    equippedEl.querySelectorAll('.inv-use-quick-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        send({ type: 'inventory_action', action: 'use_item', slot: 'quick_use' });
      });
    });
    equippedEl.querySelectorAll('.inv-unequip-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        send({ type: 'inventory_action', action: 'unequip_slot', slot: btn.dataset.slot });
      });
    });
  }

  const storage = inv.storage || [];
  if (storageEl) {
    if (storage.length === 0) {
      storageEl.innerHTML =
        '<div style="color:#64748b;font-size:0.72rem;padding:4px 0;">Pack empty. Press [E] near items or drag items here from an open chest!</div>';
    } else {
      storageEl.innerHTML = storage
        .map((it) => {
          const cat = it.category || it.slot;
          const canEquip = ['primary_weapon', 'secondary_weapon', 'quick_use'].includes(cat);
          const canUse = Boolean(it.consumable || it.effect);
          return `
            <div class="inv-storage-item" data-item-id="${it.id}">
              <div>
                <strong>${it.name}</strong> <span style="color:#38bdf8">(${it.weight || 1}wt)</span>
              </div>
              <div style="display:flex;gap:3px;">
                ${canUse ? `<button class="mini-btn inv-use-btn" data-item-id="${it.id}">Use</button>` : ''}
                ${canEquip ? `<button class="mini-btn inv-equip-btn" data-item-id="${it.id}">Equip</button>` : ''}
                <button class="mini-btn inv-drop-btn" data-item-id="${it.id}" title="Drop on ground">Drop</button>
              </div>
            </div>
          `;
        })
        .join('');

      storageEl.querySelectorAll('.inv-use-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          send({ type: 'inventory_action', action: 'use_item', item_id: btn.dataset.itemId });
        });
      });
      storageEl.querySelectorAll('.inv-equip-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          send({ type: 'inventory_action', action: 'equip_item', item_id: btn.dataset.itemId });
        });
      });
      storageEl.querySelectorAll('.inv-drop-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          send({ type: 'inventory_action', action: 'drop_item', item_id: btn.dataset.itemId });
        });
      });
    }
  }
}

if (hudInventorySidebar) {
  hudInventorySidebar.addEventListener('dragover', (ev) => {
    ev.preventDefault();
    hudInventorySidebar.classList.add('drag-target');
  });
  hudInventorySidebar.addEventListener('dragleave', () => {
    hudInventorySidebar.classList.remove('drag-target');
  });
  hudInventorySidebar.addEventListener('drop', (ev) => {
    ev.preventDefault();
    hudInventorySidebar.classList.remove('drag-target');
    try {
      const data = JSON.parse(ev.dataTransfer.getData('text/plain'));
      if (data.source === 'crate' && data.crate_id && data.item_id) {
        send({ type: 'take_crate_item', crate_id: data.crate_id, item_id: data.item_id });
      }
    } catch (_) {}
  });
}

let lastRenderedChestItemsKey = null;
let chestDelegated = false;

function initChestBodyEvents() {
  if (chestDelegated || !chestBody) return;
  chestDelegated = true;

  chestBody.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.chest-take-btn');
    if (btn && activeChest) {
      ev.preventDefault();
      ev.stopPropagation();
      const itemId = btn.dataset.itemId;
      if (itemId) {
        send({ type: 'take_crate_item', crate_id: activeChest.id, item_id: itemId });
      }
    }
  });

  chestBody.addEventListener('dragstart', (ev) => {
    if (ev.target.closest('.chest-take-btn')) {
      ev.preventDefault();
      return;
    }
    const card = ev.target.closest('.chest-item-card');
    if (card && activeChest && card.dataset.itemId) {
      ev.dataTransfer.setData(
        'text/plain',
        JSON.stringify({ source: 'crate', crate_id: activeChest.id, item_id: card.dataset.itemId })
      );
    }
  });
}

function renderChestModal(force = false) {
  if (!activeChest || !chestModal) return;
  initChestBodyEvents();
  chestModal.classList.remove('hidden');
  chestTitle.textContent = activeChest.name || activeChest.label || 'SUPPLY CRATE';
  const items = activeChest.items || [];
  const itemsKey = `${activeChest.id}_${items.map((it) => it.id).join(',')}`;
  if (!force && lastRenderedChestItemsKey === itemsKey) {
    return;
  }
  lastRenderedChestItemsKey = itemsKey;

  if (items.length === 0) {
    chestBody.innerHTML =
      '<div style="color:#64748b;font-size:0.85rem;padding:12px 0;">This chest is empty.</div>';
    return;
  }
  chestBody.innerHTML = items
    .map(
      (it) => `
      <div class="chest-item-card" draggable="true" data-item-id="${it.id}">
        <div>
          <strong style="color:#f8fafc;font-size:0.85rem;">${it.name}</strong>
          <span style="color:#38bdf8;font-weight:700;">(${it.weight || 1} WT)</span><br/>
          <span style="color:#94a3b8;font-size:0.73rem;">${it.description || it.slot || 'Equipment'}</span>
        </div>
        <button type="button" class="mini-btn chest-take-btn" data-item-id="${it.id}" draggable="false" style="position:relative;z-index:2;cursor:pointer;">→ Move to Storage</button>
      </div>
    `
    )
    .join('');
}

function renderPuzzleModal() {
  if (!activePuzzle) return;
  puzzleModal.classList.remove('hidden');
  puzzleTitle.textContent = activePuzzle.label || 'SECURITY TERMINAL';
  puzzleBody.innerHTML = '';

  const me = getLocalPlayer();
  const pdata = activePuzzle.data;

  if (pdata.type === 'power_reroute') {
    const info = document.createElement('p');
    info.textContent = 'Align all 4 power conduit relays to 0° [━] to unlock the bulkhead door:';
    puzzleBody.appendChild(info);

    const glyphs = ['━', '┏', '┃', '┗'];
    const row = document.createElement('div');
    row.className = 'puzzle-nodes-row';

    pdata.nodes.forEach((val, idx) => {
      const btn = document.createElement('button');
      btn.className = `puzzle-node-btn ${val === 0 ? 'aligned' : ''}`;
      btn.textContent = `${glyphs[val % 4]} (${val * 90}°)`;
      btn.addEventListener('click', () => {
        send({
          type: 'puzzle_action',
          puzzle_id: activePuzzle.id,
          action: { action: 'rotate', node_index: idx },
        });
      });
      row.appendChild(btn);
    });
    puzzleBody.appendChild(row);
  } else if (pdata.type === 'signal_decrypt') {
    const seqStr = pdata.target_sequence
      .map((s, i) => (i < pdata.progress ? `[✓ ${s}]` : i === pdata.progress ? `[👉 ${s}]` : s))
      .join('  ');
    const info = document.createElement('p');
    info.innerHTML = `Enter Target Hex Sequence in order:<br/><strong style="font-size:1.2rem;color:#38bdf8">${seqStr}</strong>`;
    puzzleBody.appendChild(info);

    const grid = document.createElement('div');
    grid.className = 'puzzle-nodes-row';
    for (const sym of pdata.options) {
      const btn = document.createElement('button');
      btn.className = 'puzzle-node-btn';
      btn.textContent = sym;
      btn.addEventListener('click', () => {
        send({
          type: 'puzzle_action',
          puzzle_id: activePuzzle.id,
          action: { action: 'input_symbol', symbol: sym },
        });
      });
      grid.appendChild(btn);
    }
    puzzleBody.appendChild(grid);
  }

  // Tech Class Hardware Bypass Button
  if (me && me.class_id === 'tech') {
    const bypassBtn = document.createElement('button');
    bypassBtn.className = 'tech-bypass-btn';
    bypassBtn.textContent = '⚡ [TECH PASSIVE] HARDWARE OVERRIDE (INSTANT SOLVE)';
    bypassBtn.addEventListener('click', () => {
      send({
        type: 'puzzle_action',
        puzzle_id: activePuzzle.id,
        action: { action: 'tech_bypass' },
      });
    });
    puzzleBody.appendChild(bypassBtn);
  }
}

// UI Button Listeners
saveNameBtn.addEventListener('click', () => {
  if (callsignInput.value.trim()) {
    send({ type: 'set_name', name: callsignInput.value.trim() });
  }
});

launchBtn.addEventListener('click', () => {
  send({ type: 'prepare_mission' });
});

changeClassBtn.addEventListener('click', () => renderClassModal());

closeClassBtn.addEventListener('click', () => {
  const me = getLocalPlayer();
  if (me && me.class_chosen) {
    classModal.classList.add('hidden');
    classModal.style.display = 'none';
  }
});

prepBackBtn.addEventListener('click', () => {
  send({ type: 'return_lobby' });
});

if (closeChestBtn) {
  closeChestBtn.addEventListener('click', () => {
    chestModal.classList.add('hidden');
    activeChest = null;
    lastRenderedChestItemsKey = null;
  });
}

returnLobbyBtn.addEventListener('click', () => {
  send({ type: 'return_lobby' });
});

closePuzzleBtn.addEventListener('click', () => {
  puzzleModal.classList.add('hidden');
  activePuzzle = null;
});

// Keyboard & Mouse Controls
window.addEventListener('keydown', (e) => {
  if ((!state || state.phase === 'lobby') && document.activeElement === callsignInput) return;
  const code = e.code;
  if (code === 'KeyW' || code === 'ArrowUp') keys.up = true;
  if (code === 'KeyS' || code === 'ArrowDown') keys.down = true;
  if (code === 'KeyA' || code === 'ArrowLeft') keys.left = true;
  if (code === 'KeyD' || code === 'ArrowRight') keys.right = true;
  if (code === 'ShiftLeft' || code === 'ShiftRight' || e.key === 'Shift') keys.shift = true;

  if (code === 'KeyR') send({ type: 'reload' });
  if (code === 'Digit1') send({ type: 'inventory_action', action: 'switch_weapon', slot: 'primary_weapon' });
  if (code === 'Digit2') send({ type: 'inventory_action', action: 'switch_weapon', slot: 'secondary_weapon' });
  if (code === 'KeyF') send({ type: 'inventory_action', action: 'use_item', slot: 'quick_use' });
  if (code === 'Space' || code === 'KeyQ') {
    e.preventDefault();
    send({ type: 'skill' });
  }
  if (code === 'KeyE' || e.key === 'e' || e.key === 'E') {
    e.preventDefault();
    if (activePuzzle) {
      puzzleModal.classList.add('hidden');
      activePuzzle = null;
    } else if (activeChest) {
      chestModal.classList.add('hidden');
      activeChest = null;
      lastRenderedChestItemsKey = null;
    } else {
      send({ type: 'interact' });
    }
  }
  if (code === 'Escape') {
    if (activePuzzle) {
      puzzleModal.classList.add('hidden');
      activePuzzle = null;
    }
    if (activeChest) {
      chestModal.classList.add('hidden');
      activeChest = null;
      lastRenderedChestItemsKey = null;
    }
  }
});

window.addEventListener('keyup', (e) => {
  const code = e.code;
  if (code === 'KeyW' || code === 'ArrowUp') keys.up = false;
  if (code === 'KeyS' || code === 'ArrowDown') keys.down = false;
  if (code === 'KeyA' || code === 'ArrowLeft') keys.left = false;
  if (code === 'KeyD' || code === 'ArrowRight') keys.right = false;
  if (code === 'ShiftLeft' || code === 'ShiftRight' || e.key === 'Shift') keys.shift = false;
});

canvas.addEventListener('mousemove', (e) => {
  mouseScreen.x = e.clientX;
  mouseScreen.y = e.clientY;
});

canvas.addEventListener('mousedown', (e) => {
  if (e.button === 0 && !activePuzzle && !activeChest) {
    const me = getLocalPlayer();
    if (me && !me.downed && state && state.map) {
      const mw = isoToWorld(e.clientX, e.clientY, camera);
      const lootItems = state.map.loot || [];
      const nearbyLoot = lootItems.find(
        (lt) => !lt.collected && Math.hypot(mw.x - lt.x, mw.y - lt.y) <= 1.8 && Math.hypot(me.x - lt.x, me.y - lt.y) <= 3.8
      );
      if (nearbyLoot) {
        send({ type: 'pickup_item', item_id: nearbyLoot.id });
        firing = false;
        return;
      }
      const crates = state.map.crates || [];
      const nearbyCrate = crates.find(
        (cr) => Math.hypot(mw.x - cr.x, mw.y - cr.y) <= 1.8 && Math.hypot(me.x - cr.x, me.y - cr.y) <= 3.8
      );
      if (nearbyCrate) {
        send({ type: 'interact' });
        firing = false;
        return;
      }
    }
    firing = true;
  }
});

window.addEventListener('mouseup', (e) => {
  if (e.button === 0) {
    firing = false;
  }
});

// 30Hz Network Input Sender
setInterval(() => {
  if (!state || state.phase === 'lobby') return;
  mouseWorld = isoToWorld(mouseScreen.x, mouseScreen.y, camera);
  const move = activePuzzle || activeChest
    ? { dx: 0, dy: 0 }
    : screenInputToWorldDelta(keys.up, keys.down, keys.left, keys.right);

  const me = getLocalPlayer();
  const isLight = isLocalPlayerLightWeapon(me);
  // When running with heavy weapons, weapon firing is blocked
  const canShoot = !keys.shift || isLight;

  send({
    type: 'input',
    dx: move.dx,
    dy: move.dy,
    aim_x: mouseWorld.x,
    aim_y: mouseWorld.y,
    firing: firing && canShoot && !activePuzzle && !activeChest,
    running: Boolean(keys.shift),
  });
}, 1000 / 30);

// 60fps Render Loop with Smooth Camera Tracking
function animate() {
  if (state && state.phase !== 'lobby' && state.map) {
    const me = getLocalPlayer();
    if (me) {
      camera.x += (me.x - camera.x) * 0.14;
      camera.y += (me.y - camera.y) * 0.14;
    }
    mouseWorld = isoToWorld(mouseScreen.x, mouseScreen.y, camera);
    renderer.render(state, localPlayerId, mouseWorld, camera);
  }
  requestAnimationFrame(animate);
}

// Pre-warm audio manager on first user action or start
const unlockAudio = () => {
  audioManager.init().catch(console.error);
};
window.addEventListener('click', unlockAudio);
window.addEventListener('keydown', unlockAudio);
window.addEventListener('pointerdown', unlockAudio);
connectWebSocket();
requestAnimationFrame(animate);
