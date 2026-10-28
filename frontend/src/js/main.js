import { audioManager } from './audio.js';
/**
 * Main client controller: WebSocket networking, Lobby & Leader Mission selector,
 * HUD, Interactive Puzzle Modals, and real-time WASD + Mouse Aim/Fire input loop.
 */

import { isoToWorld } from './iso.js';
import { IsometricRenderer } from './renderer.js';
import { html, joinHtml, safeColor, canvasPoint } from './ui.js';
import { canFireWhileRunning } from './content.js';
import { ClientSession } from './network.js';
import { InputContext } from './input.js';
import { InteractionModal } from './modal.js';
import { renderPuzzle, refreshPuzzleTime } from './puzzles.js';
import { renderService } from './hub.js';

let localPlayerId = null;
let classesConfig = {};
let itemsConfig = {};
let contentCatalog = null;
let missionsConfig = {};
let state = null;
let activePuzzle = null;
let activeChest = null;
let lastHudInvSig = '';
let lastServiceSig = '';

const input = new InputContext(send, () => Boolean(state?.map && getLocalPlayer()?.capabilities?.can_move &&
  !modals.active && !modals.pending && classModal.classList.contains('hidden') && !getLocalPlayer()?.downed),
  () => mouseWorld, () => (modals.active || modals.pending) ? modals.close() : false,
  () => modals.request({ type: 'interact' }));
const keys = input.keys;
const modals = new InteractionModal(send, active => {
  input.reset();
  activePuzzle = active?.kind === 'puzzle' ? active.data : null;
  activeChest = active?.kind === 'chest' ? active.data : null;
  puzzleModal.classList.toggle('hidden', !activePuzzle);
  chestModal.classList.toggle('hidden', !activeChest);
  document.getElementById('service-modal').classList.toggle('hidden', active?.kind !== 'service');
  if (activePuzzle) renderPuzzleModal();
  if (activeChest) renderChestModal();
  if (active?.kind === 'service') renderServiceModal();
});

function isLocalPlayerLightWeapon(p) {
  return canFireWhileRunning(p, itemsConfig);
}

const mouseScreen = { x: window.innerWidth * 0.5, y: window.innerHeight * 0.5 };
let mouseWorld = { x: 8, y: 8 };

// Expose state and debug helpers on window for tests and console
if (typeof window !== 'undefined') {
  window.__game = {
    get state() { return state; },
    get activeChest() { return activeChest; },
    get activePuzzle() { return activePuzzle; },
    get camera() { return camera; },
    get firing() { return input.firing; },
    get localPlayerId() { return localPlayerId; },
    get catalog() { return contentCatalog; },
    keys,
    mouseScreen,
    send: (payload) => send(payload),
  };
}

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
  camera.screenW = window.innerWidth;
  camera.screenH = window.innerHeight;
  camera.pixelRatio = window.devicePixelRatio || 1;
  canvas.width = Math.round(camera.screenW * camera.pixelRatio);
  canvas.height = Math.round(camera.screenH * camera.pixelRatio);
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

function send(payload) {
  if (state?.map && (modals.active || modals.pending) && ['reload', 'skill', 'inventory_action'].includes(payload.type)) return false;
  if (state?.map && !state.world_capabilities?.combat && ['reload', 'skill'].includes(payload.type)) return false;
  if (payload.type === 'input' && payload.firing && state?.map && !state.world_capabilities?.combat) payload = { ...payload, firing: false };
  return session.send(payload);
}

function getLocalPlayer() {
  if (!state || !state.players) return null;
  return state.players.find((p) => p.id === localPlayerId) || null;
}

const session = new ClientSession(msg => {
    if (msg.type === 'welcome') {
      localPlayerId = msg.player_id;
      contentCatalog = msg.catalog;
      classesConfig = contentCatalog.classes;
      itemsConfig = contentCatalog.items;
      missionsConfig = contentCatalog.missions;
      renderer.setClassesConfig(classesConfig);
      renderer.setItemsConfig(itemsConfig);
      renderer.setPresentation(contentCatalog.presentation);
      audioManager.setCatalog(contentCatalog);
      state = msg.state;
      modals.reconcile(state, localPlayerId);
      const me = getLocalPlayer();
      audioManager.consumeSnapshot(state, me ? { x: me.x, y: me.y } : null);
      if (me && callsignInput) {
        callsignInput.value = me.name;
      }
      updateScreens(true);
      maybeShowClassModal();
    } else if (msg.type === 'command_error' || msg.type === 'inventory_feedback') {
      const current = !msg.request_id || modals.receive(msg);
      if (current && (msg.ok === false || msg.type === 'command_error')) {
        console.warn(msg.message || msg.reason || 'Command rejected');
        showFeedback(msg.message || msg.reason || 'Command rejected');
      }
    } else if (msg.type === 'state') {
      const previousWorld = state?.world_id;
      state = msg;
      modals.reconcile(state, localPlayerId);

      if (activePuzzle) refreshPuzzleTime(puzzleBody, activePuzzle, state.simulation_time);

      // The audio consumer owns world reset, event dedup and asset/gesture readiness.
      const me = getLocalPlayer();
      audioManager.consumeSnapshot(msg, me ? { x: me.x, y: me.y } : null);
      updateScreens(previousWorld !== state.world_id);
    } else if (['interact_result', 'chest_update', 'puzzle_update', 'service_feedback'].includes(msg.type)) {
      if (modals.receive(msg) && msg.ok === false) showFeedback(msg.message || msg.reason || 'Target unavailable');
    }
  }, () => {
    modals.close();
    input.reset();
    state = null;
    localPlayerId = null;
    renderer.resetExplored();
    renderer.scene = [];
    audioManager.resetWorld();
  });

function updateScreens(worldChanged) {
  if (!state.map) {
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
    if (!['lobby', 'prep'].includes(state.phase)) classModal.classList.add('hidden');
    gameScreen.classList.remove('hidden');
    if (worldChanged) {
      input.reset();
      lastHudInvSig = '';
      if (callsignInput) callsignInput.blur();
      renderer.resetExplored();
      renderer.scene = [];
      const me = getLocalPlayer();
      if (me) {
        camera.x = me.x;
        camera.y = me.y;
      }
    }
    updateHUD();
    maybeShowClassModal();
    if (modals.active?.kind === 'service') renderServiceModal();
  }
}

function renderServiceModal() {
  const active = modals.active;
  if (active?.kind !== 'service' || !state) return;
  const signature = JSON.stringify([active, state.phase, state.selected_level_id, state.shared_stash, state.players.map(p => [p.id, p.name, p.ready, p.inventory]), state.levels, modals.busy]);
  if (signature === lastServiceSig) return;
  lastServiceSig = signature;
  document.getElementById('service-title').textContent = active.data.label;
  renderService(document.getElementById('service-body'), active.data, state, localPlayerId,
    payload => modals.request(payload, active.targetId), send, modals.busy);
}

function showFeedback(message) {
  const notice = document.getElementById('command-feedback');
  notice.textContent = message;
  notice.classList.remove('hidden');
}

let lastLobbySig = '';
let localLockNotice = '';

function getClassPortraitSvg(classId, color) {
  color = safeColor(color);
  const visorColor = '#38bdf8';
  const crest =
    classId === 'vanguard'
      ? html`<rect x="18" y="8" width="28" height="8" rx="3" fill="${color}"/>`
      : classId === 'tech'
      ? html`<circle cx="48" cy="18" r="5" fill="#fde047"/><line x1="48" y1="6" x2="48" y2="14" stroke="#fde047" stroke-width="2"/>`
      : classId === 'medic'
      ? html`<path d="M29 9 H35 V13 H39 V19 H35 V23 H29 V19 H25 V13 H29 Z" fill="#22c55e"/>`
      : html`<polygon points="32,6 42,16 22,16" fill="${color}"/>`;
  return html`
    <svg width="62" height="62" viewBox="0 0 64 64" style="flex-shrink:0;background:#090e1a;border:2px solid ${color};border-radius:8px;padding:3px;">
      <path d="M12 58 C12 42, 52 42, 52 58 Z" fill="${color}" opacity="0.85"/>
      <rect x="18" y="16" width="28" height="26" rx="9" fill="#1e293b" stroke="${color}" stroke-width="2.5"/>
      <rect x="22" y="23" width="20" height="8" rx="3" fill="${visorColor}"/>
      ${crest}
    </svg>
  `;
}

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
  if (!state || !['lobby', 'prep'].includes(state.phase)) return;
  input.reset();
  const me = getLocalPlayer();
  closeClassBtn.style.display = me && me.class_chosen ? '' : 'none';
  classGridEl.innerHTML = '';
  if (!Object.keys(classesConfig).length) {
    classGridEl.textContent = 'Content catalog unavailable. Waiting for server.';
  }
  for (const cls of Object.values(classesConfig)) {
    const selected = me && me.class_chosen && me.class_id === cls.id;
    const cap = cls.carry_capacity;
    const card = document.createElement('div');
    card.className = `card class-card ${selected ? 'selected' : ''}`;
    card.style.borderColor = selected ? safeColor(cls.color) : '';
    card.style.cursor = 'pointer';
    card.innerHTML = html`
      <div style="display:flex;gap:10px;align-items:center;">
        ${getClassPortraitSvg(cls.id, cls.color)}
        <div>
          <div class="class-title" style="color:${safeColor(cls.color)}">${cls.name}</div>
          <div class="class-role">${cls.title || ''}</div>
        </div>
      </div>
      <div class="stats-row">
        <span>HP: <b>${cls.max_hp}</b></span>
        <span>SPD: <b>${cls.speed}</b></span>
        <span>CARRY: <b style="color:#38bdf8">${cap} WT</b></span>
      </div>
      <div class="weapon-box"><strong>Weapon:</strong> ${cls.weapon.name}<br/><small>${cls.weapon.damage} damage / ${cls.weapon.mag_size} rounds / ${cls.weapon.range} range</small></div>
      <div class="skill-box"><strong>Skill:</strong> ${cls.skill.name} (${cls.skill.cooldown}s)<br/><small>${cls.skill.description}${cls.skill.radius ? ` Radius: ${cls.skill.radius}.` : cls.skill.range ? ` Range: ${cls.skill.range}.` : ''}</small></div>
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
    const leaderBadge = p.id === state.leader_id ? html`<span class="badge-leader">★ LEADER</span>` : '';
    const youBadge = p.id === localPlayerId ? html`<span class="badge-you">(YOU)</span>` : '';
    li.innerHTML = html`
      <div class="player-info">
        <span class="class-dot" style="background:${safeColor(cls.color)}"></span>
        <strong>${p.name}</strong> ${youBadge} ${leaderBadge}
      </div>
      <div class="player-class-tag" style="color:${safeColor(cls.color)}">${p.class_chosen ? cls.name : 'Choosing...'}</div>
    `;
    playerListEl.appendChild(li);
  }

  // 2. Shared loot list — information only (scrollable, no interaction)
  const stash = state.shared_stash || [];
  lobbySharedStash.innerHTML =
    stash.length === 0
      ? '<div style="color:#64748b;font-size:0.75rem;">Shared loot is empty. Extract loot from missions!</div>'
      : joinHtml(stash
          .map(
            (st) => html`
        <div class="stash-item">
          <div>
            <strong>${st.name}</strong>
            <span style="color:#94a3b8;font-size:0.7rem;">(${st.weight} wt • ${(st.category || '').replace('_', ' ')})</span>
          </div>
          <span style="color:#fbbf24;font-weight:800;">x${st.count}</span>
        </div>`
          )
          );

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
      ? html`<div style="margin:6px 0;padding:6px 10px;border-radius:6px;background:rgba(245,158,11,0.15);border:1px solid #f59e0b;color:#fcd34d;font-size:0.78rem;font-weight:600;">
           🔒 LOCKED — ${missionLockReason}
         </div>`
      : '';

    const levelsHtml = joinHtml(missionLevels
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
            ? html`<div style="color:#fca5a5;font-size:0.72rem;margin-top:3px;font-weight:600;">↳ Requires: ${lvl.lock_reason}</div>`
            : '';
        return html`
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
      );

    card.innerHTML = html`
      <div class="card-header">
        <span class="mission-title">${anyMissionUnlocked ? '🔓' : '🔒'} ${m.name}</span>
        <span class="difficulty-tag diff-${['standard', 'hard', 'extreme'].includes(m.difficulty.toLowerCase()) ? m.difficulty.toLowerCase() : 'standard'}">${m.difficulty}</span>
      </div>
      <p class="mission-desc">${m.description}</p>
      ${missionLockBanner}
      <div class="segments-preview">${levelsHtml}</div>
      ${
        isLeader && anyMissionUnlocked
          ? html`<button class="btn-secondary gen-level-btn" data-mission-id="${m.id}" style="margin-top:8px;width:100%;font-size:0.78rem;border-color:#38bdf8;color:#38bdf8;">+ GENERATE NEW WFC LEVEL (ENTERS TESTING PHASE)</button>`
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
  if (!item) return html`<span style="color:#475569">— empty —</span>`;
  const back =
    canEdit && item.category !== 'special_ability'
      ? html`<button class="mini-btn prep-return-btn" data-item-id="${item.id}" data-owner="${ownerId}" title="Return to crate">↩</button>`
      : '';
  return html`<span><strong>${item.name}</strong> <span style="color:#38bdf8">${item.weight || 1}wt</span></span>${back}`;
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
      : joinHtml(stash
          .map(
            (st) => html`
        <div class="prep-crate-item" draggable="true" data-template-id="${st.template_id}">
          <div style="display:flex;justify-content:space-between;">
            <strong>${st.name}</strong><span style="color:#fbbf24;font-weight:800;">x${st.count}</span>
          </div>
          <span style="color:#94a3b8;font-size:0.7rem;">${st.weight} wt • ${(st.category || '').replace('_', ' ')}</span>
        </div>`
          )
          );
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
    const inv = p.inventory || { equipped: {}, storage: [], current_weight: 0, max_weight: cls.carry_capacity };
    const eq = inv.equipped || {};
    const pct = Math.min(100, Math.round(((inv.current_weight || 0) / Math.max(1, inv.max_weight)) * 100));
    const storage = inv.storage || [];

    slotEl.innerHTML = html`
      <div class="prep-player-card ${isMe ? 'is-me' : ''} ${p.ready ? 'is-ready' : ''}">
        <div style="display:flex;gap:10px;align-items:center;">
          ${getClassPortraitSvg(p.class_id, cls.color)}
          <div style="flex:1;">
            <div style="font-weight:800;">${p.name} ${isMe ? html`<span class="badge-you">(YOU)</span>` : ''}</div>
            <div style="color:${safeColor(cls.color)};font-size:0.8rem;font-weight:700;">${cls.name}</div>
            <div style="font-size:0.72rem;color:#94a3b8;margin-top:3px;">Carry: <b style="color:#38bdf8">${inv.current_weight || 0} / ${inv.max_weight} WT</b></div>
            <div class="inv-weight-bar-wrap" style="margin-top:3px;"><div class="inv-weight-bar-fill" style="width:${pct}%;background:${pct >= 90 ? '#ef4444' : '#38bdf8'}"></div></div>
          </div>
        </div>
        <div class="inv-section-label">EQUIPPED</div>
        <div class="prep-inv-grid">
          ${joinHtml(SLOT_DEFS.map(
            (sd) => html`<div class="inv-slot-row" style="flex-direction:column;align-items:flex-start;gap:2px;">
              <span style="color:#94a3b8;font-size:0.64rem;">${sd.label}</span>
              <div style="display:flex;justify-content:space-between;width:100%;gap:4px;">${itemChipHtml(eq[sd.key], p.id, true)}</div>
            </div>`
          ))}
        </div>
        <div class="inv-section-label">STORAGE (${storage.length} items)</div>
        <div class="inv-storage-list">
          ${
            storage.length === 0
              ? html`<div style="color:#64748b;font-size:0.72rem;">Drop items here from the crate.</div>`
              : joinHtml(storage
                  .map(
                    (it) => html`<div class="inv-storage-item" draggable="true" data-item-id="${it.id}" data-owner="${p.id}">
                      ${itemChipHtml(it, p.id, true)}
                    </div>`
                  )
                  )
          }
        </div>
        ${
          isMe
            ? html`<div style="display:flex;gap:6px;margin-top:auto;">
                 <button class="btn-secondary prep-min-btn" style="font-size:0.72rem;">⚡ Min Loadout</button>
                 <button class="btn-secondary prep-class-btn" style="font-size:0.72rem;">⇄ Class</button>
               </div>
               <button class="prep-ready-btn ${p.ready ? 'ready' : ''}">${p.ready ? '✓ READY — CLICK TO CANCEL' : 'READY UP'}</button>`
            : html`<div class="prep-ready-btn ${p.ready ? 'ready' : ''}" style="cursor:default;text-align:center;">${p.ready ? '✓ READY' : 'PREPARING...'}</div>`
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
  hudMissionTitle.textContent = `${titlePrefix}${state.map.level_name || state.map.world_name || state.map.mission_name} (${state.map.segments.length} Sectors)`;
  const hub = state.world_kind === 'hub';
  document.getElementById('hub-controls').classList.toggle('hidden', !hub);
  document.getElementById('hub-ready-btn').textContent = me?.ready ? 'Cancel Ready' : 'Ready Up';
  document.getElementById('hub-ready-btn').disabled = state.phase !== 'prep';

  // Objective progress
  const objectives = (state.map.objectives || []).filter(o => o.required);
  const solvedCount = objectives.filter((p) => p.completed).length;
  if (!state.world_capabilities?.objectives) {
    hudObjectiveText.textContent = 'Noncombat world: [E] or click an interaction.';
  } else if (state.phase === 'dropship') {
    hudObjectiveText.textContent = `Dropship docking... Ramp opens in ${state.dropship_timer}s`;
  } else if (solvedCount < objectives.length) {
    const nextObjective = objectives.find((p) => !p.completed);
    hudObjectiveText.textContent = `${isTest ? 'Test ' : ''}Objective (${solvedCount}/${objectives.length}): ${nextObjective.label}`;
  } else {
    hudObjectiveText.textContent = !state.world_capabilities?.extraction ? 'WORLD OBJECTIVES COMPLETE' : isTest
      ? 'TEST OBJECTIVES UNLOCKED — Reach Extraction Pad to Verify Level!'
      : 'PRIMARY COMPLETE — All operatives proceed to Extraction Pad!';
  }

  if (me) {
    const cls = classesConfig[me.class_id];
    const hpPct = Math.max(0, Math.min(100, (me.hp / me.max_hp) * 100));
    hudHpFill.style.width = `${hpPct}%`;
    hudHpText.textContent = me.downed ? 'DOWNED (NEED REVIVE)' : `${me.hp} / ${me.max_hp} HP`;
    hudAmmoText.textContent = !state.world_capabilities?.combat ? 'NONCOMBAT' :
      me.reload_timer > 0
        ? `RELOADING (${me.reload_timer.toFixed(1)}s)`
        : `${me.ammo} / ${me.max_ammo}${me.running ? (isLocalPlayerLightWeapon(me) ? ' [SPRINTING]' : ' [SPRINT: LIGHT WEAPONS ONLY]') : ''}`;

    if (cls) {
      hudSkillName.textContent = state.world_capabilities?.combat ? `[SPACE] ${cls.skill.name}` : cls.skill.name;
      if (!state.world_capabilities?.combat) {
        hudSkillStatus.textContent = 'DISABLED';
      } else if (me.skill_timer > 0) {
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

  const pct = Math.min(100, Math.round(((inv.current_weight || 0) / Math.max(1, inv.max_weight)) * 100));
  if (weightEl) {
    weightEl.textContent = `${inv.current_weight || 0} / ${inv.max_weight} WT`;
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
    equippedEl.innerHTML = joinHtml(equippedRows
      .map((r) => {
        const name = r.item ? r.item.name : 'Empty';
        const wStr = r.item && r.item.weight ? ` (${r.item.weight}wt)` : '';
        return html`
          <div class="inv-slot-row ${r.active ? 'active-weapon' : ''}">
            <div>
              <span style="color:#94a3b8;font-size:0.66rem;">${r.label}</span><br/>
              <strong style="color:${r.item ? '#f8fafc' : '#64748b'}">${name}${wStr}</strong>
            </div>
            <div style="display:flex;gap:4px;">
              ${
                r.switchable && r.item && !r.active
                   ? html`<button class="mini-btn inv-switch-btn" data-slot="${r.slot}">Use</button>`
                  : ''
              }
              ${
                r.usable
                   ? html`<button class="mini-btn inv-use-quick-btn">Use [F]</button>`
                  : ''
              }
              ${
                r.item && r.slot !== 'special_ability'
                   ? html`<button class="mini-btn inv-unequip-btn" data-slot="${r.slot}">Store</button>`
                  : ''
              }
            </div>
          </div>
        `;
      })
      );

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
      storageEl.innerHTML = joinHtml(storage
        .map((it) => {
          const cat = it.category || it.slot;
          const canEquip = ['primary_weapon', 'secondary_weapon', 'quick_use'].includes(cat);
          const effect = itemsConfig[it.template_id]?.effect;
          const canUse = ['heal', 'ammo_refill', 'turret', 'sonar'].includes(effect?.behavior);
          const lootValue = effect?.behavior === 'loot' ? (it.value?.loot ?? effect.loot) : null;
          return html`
            <div class="inv-storage-item" data-item-id="${it.id}">
              <div>
                <strong>${it.name}</strong> <span style="color:#38bdf8">(${it.weight || 1}wt)</span>${lootValue !== null ? html` <small>Value: ${lootValue}</small>` : ''}
              </div>
              <div style="display:flex;gap:3px;">
                ${canUse ? html`<button class="mini-btn inv-use-btn" data-item-id="${it.id}">Use</button>` : ''}
                ${canEquip ? html`<button class="mini-btn inv-equip-btn" data-item-id="${it.id}">Equip</button>` : ''}
                <button class="mini-btn inv-drop-btn" data-item-id="${it.id}" title="Drop on ground">Drop</button>
              </div>
            </div>
          `;
        })
        );

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
  document.getElementById('inventory-toggle').addEventListener('click', event => {
    const expanded = hudInventorySidebar.classList.toggle('inventory-expanded');
    event.currentTarget.setAttribute('aria-expanded', String(expanded));
    event.currentTarget.textContent = expanded ? 'Hide Inventory' : 'Show Inventory';
    input.reset();
  });
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
        if (activeChest?.id === data.crate_id) modals.request({ type: 'take_crate_item', crate_id: data.crate_id, item_id: data.item_id }, data.crate_id);
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
        modals.request({ type: 'take_crate_item', crate_id: activeChest.id, item_id: itemId }, activeChest.id);
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
  const itemsKey = JSON.stringify([activeChest, modals.busy]);
  if (!force && lastRenderedChestItemsKey === itemsKey) {
    return;
  }
  lastRenderedChestItemsKey = itemsKey;

  if (items.length === 0) {
    chestBody.innerHTML =
      '<div style="color:#64748b;font-size:0.85rem;padding:12px 0;">This chest is empty.</div>';
    return;
  }
  chestBody.innerHTML = joinHtml(items
    .map(
      (it) => html`
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
    );
  chestBody.querySelectorAll('button').forEach(button => { button.disabled = modals.busy; });
}

function renderPuzzleModal() {
  if (!activePuzzle) return;
  puzzleModal.classList.remove('hidden');
  puzzleTitle.textContent = activePuzzle.label || 'SECURITY TERMINAL';
  renderPuzzle(puzzleBody, activePuzzle, action => modals.request({ type: 'puzzle_action', puzzle_id: activePuzzle.id, action }, modals.active.targetId),
    { now: state.simulation_time, player: getLocalPlayer(), busy: modals.busy });
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
    modals.close();
    lastRenderedChestItemsKey = null;
  });
}

returnLobbyBtn.addEventListener('click', () => {
  send({ type: 'return_lobby' });
});

closePuzzleBtn.addEventListener('click', () => {
  modals.close();
});
document.getElementById('close-service-btn').addEventListener('click', () => { modals.close(); lastServiceSig = ''; });
document.getElementById('hub-class-btn').addEventListener('click', renderClassModal);
document.getElementById('hub-ready-btn').addEventListener('click', () => send({ type: 'toggle_ready' }));

// Keyboard & Mouse Controls
window.addEventListener('keydown', event => input.keydown(event));
window.addEventListener('keyup', event => input.keyup(event));
window.addEventListener('blur', () => { input.focused = false; input.reset(); });
window.addEventListener('focus', () => { input.focused = true; input.reset(); });
document.addEventListener('visibilitychange', () => { input.hidden = document.hidden; input.reset(); });
document.addEventListener('focusin', () => input.reset());
window.addEventListener('pointercancel', () => input.reset());

canvas.addEventListener('mousemove', (e) => {
  Object.assign(mouseScreen, canvasPoint(e, canvas, camera));
});

canvas.addEventListener('mousedown', (e) => {
  if (e.button === 0 && input.allowed(document.activeElement)) {
    const me = getLocalPlayer();
    if (me && !me.downed && state && state.map) {
      Object.assign(mouseScreen, canvasPoint(e, canvas, camera));
      const mw = isoToWorld(mouseScreen.x, mouseScreen.y, camera);
      const targets = [...(state.map.interactions || []), ...(state.map.crates || []), ...(state.map.puzzles || []), ...(state.map.loot || [])]
        .filter(target => Math.hypot(mw.x - target.x, mw.y - target.y) <= 1.0)
        .sort((a, b) => Math.hypot(mw.x - a.x, mw.y - a.y) - Math.hypot(mw.x - b.x, mw.y - b.y));
      if (targets.length) {
        modals.request({ type: 'interact', target_id: targets[0].id }, targets[0].id);
        input.firing = false;
        return;
      }
    }
    input.firing = Boolean(state?.world_capabilities?.combat);
  }
});

window.addEventListener('mouseup', (e) => {
  if (e.button === 0) {
    input.firing = false;
  }
});

// 30Hz Network Input Sender
setInterval(() => {
  if (!state?.map || !getLocalPlayer()?.capabilities?.can_move) return;
  mouseWorld = isoToWorld(mouseScreen.x, mouseScreen.y, camera);
  send(input.intent(document.activeElement));
}, 1000 / 30);

// 60fps Render Loop with Smooth Camera Tracking
function animate() {
  if (camera.pixelRatio !== (window.devicePixelRatio || 1)) resizeCanvas();
  if (state?.map) {
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

window.addEventListener('pagehide', () => { input.reset(); audioManager.dispose(); renderer.dispose(); });
session.connect();
requestAnimationFrame(animate);
