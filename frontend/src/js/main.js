/**
 * Main client controller: WebSocket networking, Lobby & Leader Mission selector,
 * HUD, Interactive Puzzle Modals, and real-time WASD + Mouse Aim/Fire input loop.
 */

import { isoToWorld, screenInputToWorldDelta } from './iso.js';
import { IsometricRenderer } from './renderer.js';

let ws = null;
let localPlayerId = null;
let classesConfig = {};
let missionsConfig = {};
let state = null;
let activePuzzle = null;

const keys = {
  up: false,
  down: false,
  left: false,
  right: false,
};

const mouseScreen = { x: window.innerWidth * 0.5, y: window.innerHeight * 0.5 };
let mouseWorld = { x: 8, y: 8 };
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
      renderLobbyUI();
    } else if (msg.type === 'state') {
      const prevPhase = state ? state.phase : null;
      state = msg;
      if (state.phase === 'lobby') {
        lobbyScreen.classList.remove('hidden');
        gameScreen.classList.add('hidden');
        puzzleModal.classList.add('hidden');
        activePuzzle = null;
        renderLobbyUI();
      } else {
        lobbyScreen.classList.add('hidden');
        gameScreen.classList.remove('hidden');
        if (prevPhase === 'lobby') {
          if (callsignInput) callsignInput.blur();
          renderer.resetExplored();
          const me = getLocalPlayer();
          if (me) {
            camera.x = me.x;
            camera.y = me.y;
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
      }
    } else if (msg.type === 'puzzle_update') {
      if (msg.puzzle) {
        activePuzzle = msg.puzzle;
      }
      if (msg.solved) {
        puzzleModal.classList.add('hidden');
        activePuzzle = null;
      } else if (activePuzzle) {
        renderPuzzleModal();
      }
    }
  });

  ws.addEventListener('close', () => {
    setTimeout(connectWebSocket, 1500);
  });
}

function renderLobbyUI() {
  if (!state) return;
  const isLeader = state.leader_id === localPlayerId;
  const me = getLocalPlayer();

  // 1. Player Roster
  playerListEl.innerHTML = '';
  for (const p of state.players || []) {
    const cls = classesConfig[p.class_id] || { name: p.class_id, color: '#38bdf8' };
    const li = document.createElement('div');
    li.className = 'player-row';
    const leaderBadge =
      p.id === state.leader_id ? '<span class="badge-leader">★ LEADER</span>' : '';
    const youBadge = p.id === localPlayerId ? '<span class="badge-you">(YOU)</span>' : '';
    li.innerHTML = `
      <div class="player-info">
        <span class="class-dot" style="background:${cls.color}"></span>
        <strong>${p.name}</strong> ${youBadge} ${leaderBadge}
      </div>
      <div class="player-class-tag" style="color:${cls.color}">${cls.name}</div>
    `;
    playerListEl.appendChild(li);
  }

  // 2. Character Classes
  classGridEl.innerHTML = '';
  for (const cls of Object.values(classesConfig)) {
    const selected = me && me.class_id === cls.id;
    const card = document.createElement('div');
    card.className = `card class-card ${selected ? 'selected' : ''}`;
    card.style.borderColor = selected ? cls.color : '';
    card.innerHTML = `
      <div class="card-header">
        <span class="class-title" style="color:${cls.color}">${cls.name}</span>
        <span class="class-role">${cls.title}</span>
      </div>
      <div class="stats-row">
        <span>HP: <b>${cls.max_hp}</b></span>
        <span>SPD: <b>${cls.speed}</b></span>
        <span>NOISE: <b>${cls.weapon.sound_radius}m</b></span>
      </div>
      <div class="weapon-box">
        <strong>Weapon:</strong> ${cls.weapon.name} (${cls.weapon.damage}${cls.weapon.pellets > 1 ? 'x' + cls.weapon.pellets : ''} DMG)
      </div>
      <div class="skill-box">
        <strong>Skill [SPACE]:</strong> ${cls.skill.name} (${cls.skill.cooldown}s CD)<br/>
        <small>${cls.skill.description}</small>
      </div>
      <div class="passive-box">
        <strong>Passive:</strong> ${cls.passive.name} — <small>${cls.passive.description}</small>
      </div>
    `;
    card.addEventListener('click', () => {
      send({ type: 'select_class', class_id: cls.id });
    });
    classGridEl.appendChild(card);
  }

  // 3. Mission Selection (Leader Controlled)
  leaderStatusBanner.textContent = isLeader
    ? 'You are the Squad Leader. Select a mission and launch when ready.'
    : 'Waiting for the Squad Leader to select a mission and launch...';

  missionGridEl.innerHTML = '';
  for (const m of Object.values(missionsConfig)) {
    const selected = state.selected_mission_id === m.id;
    const card = document.createElement('div');
    card.className = `card mission-card ${selected ? 'selected' : ''} ${!isLeader ? 'disabled-card' : ''}`;
    card.innerHTML = `
      <div class="card-header">
        <span class="mission-title">${m.name}</span>
        <span class="difficulty-tag diff-${m.difficulty.toLowerCase()}">${m.difficulty}</span>
      </div>
      <p class="mission-desc">${m.description}</p>
      <div class="segments-preview">
        Segments: <b>${m.segment_sequence.length} Sectors</b> (${m.segment_sequence.join(' → ')})
      </div>
    `;
    if (isLeader) {
      card.addEventListener('click', () => {
        send({ type: 'select_mission', mission_id: m.id });
      });
    }
    missionGridEl.appendChild(card);
  }

  launchBtn.disabled = !isLeader;
  launchBtn.textContent = isLeader ? 'LAUNCH DROPSHIP' : 'WAITING FOR LEADER...';
}

function updateHUD() {
  if (!state || !state.map) return;
  const me = getLocalPlayer();
  hudMissionTitle.textContent = `${state.map.mission_name} (${state.map.segments.length} Sectors)`;

  // Objective progress
  const puzzles = state.map.puzzles || [];
  const solvedCount = puzzles.filter((p) => p.solved).length;
  if (state.phase === 'dropship') {
    hudObjectiveText.textContent = `Dropship docking... Ramp opens in ${state.dropship_timer}s`;
  } else if (solvedCount < puzzles.length) {
    const nextPz = puzzles.find((p) => !p.solved);
    hudObjectiveText.textContent = `Objective (${solvedCount}/${puzzles.length}): ${nextPz ? nextPz.label : 'Complete Terminals'}`;
  } else {
    hudObjectiveText.textContent = 'PRIMARY COMPLETE — All operatives proceed to Extraction Pad!';
  }

  if (me) {
    const cls = classesConfig[me.class_id];
    const hpPct = Math.max(0, Math.min(100, (me.hp / me.max_hp) * 100));
    hudHpFill.style.width = `${hpPct}%`;
    hudHpText.textContent = me.downed ? 'DOWNED (NEED REVIVE)' : `${me.hp} / ${me.max_hp} HP`;
    hudAmmoText.textContent =
      me.reload_timer > 0
        ? `RELOADING (${me.reload_timer.toFixed(1)}s)`
        : `${me.ammo} / ${me.max_ammo}`;

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
    endTitle.textContent =
      state.phase === 'victory' ? 'EXTRACTION SUCCESSFUL' : 'SQUAD WIPE — MISSION FAILED';
    endTitle.style.color = state.phase === 'victory' ? '#34d399' : '#f87171';
    endSub.textContent = `Mission Time: ${state.mission_time}s`;
  } else {
    endBanner.classList.add('hidden');
  }
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
  send({ type: 'start_mission' });
});

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

  if (code === 'KeyR') send({ type: 'reload' });
  if (code === 'Space' || code === 'KeyQ') {
    e.preventDefault();
    send({ type: 'skill' });
  }
  if (code === 'KeyE' || e.key === 'e' || e.key === 'E') {
    e.preventDefault();
    if (activePuzzle) {
      puzzleModal.classList.add('hidden');
      activePuzzle = null;
    } else {
      send({ type: 'interact' });
      const me = getLocalPlayer();
      if (me && !me.downed && state && state.map && state.map.puzzles) {
        const nearby = state.map.puzzles.find(
          (pz) => !pz.solved && Math.hypot(me.x - pz.x, me.y - pz.y) <= (pz.radius || 2.8) + 0.4
        );
        if (nearby) {
          activePuzzle = nearby;
          keys.up = keys.down = keys.left = keys.right = false;
          firing = false;
          renderPuzzleModal();
        }
      }
    }
  }
  if (code === 'Escape' && activePuzzle) {
    puzzleModal.classList.add('hidden');
    activePuzzle = null;
  }
});

window.addEventListener('keyup', (e) => {
  const code = e.code;
  if (code === 'KeyW' || code === 'ArrowUp') keys.up = false;
  if (code === 'KeyS' || code === 'ArrowDown') keys.down = false;
  if (code === 'KeyA' || code === 'ArrowLeft') keys.left = false;
  if (code === 'KeyD' || code === 'ArrowRight') keys.right = false;
});

canvas.addEventListener('mousemove', (e) => {
  mouseScreen.x = e.clientX;
  mouseScreen.y = e.clientY;
});

canvas.addEventListener('mousedown', (e) => {
  if (e.button === 0 && !activePuzzle) {
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
  const move = activePuzzle
    ? { dx: 0, dy: 0 }
    : screenInputToWorldDelta(keys.up, keys.down, keys.left, keys.right);

  send({
    type: 'input',
    dx: move.dx,
    dy: move.dy,
    aim_x: mouseWorld.x,
    aim_y: mouseWorld.y,
    firing: firing && !activePuzzle,
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

connectWebSocket();
requestAnimationFrame(animate);
