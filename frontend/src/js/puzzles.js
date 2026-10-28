// Localized renderer counterparts of the server's bounded mechanics.
function button(body, label, action, send, className = 'puzzle-node-btn') {
  const element = document.createElement('button');
  element.className = className;
  element.textContent = label;
  element.addEventListener('click', () => send(action));
  body.appendChild(element);
  return element;
}

function text(body, value, className = '') {
  const element = document.createElement('p');
  element.className = className;
  element.textContent = value;
  body.appendChild(element);
}

function power(body, puzzle, send) {
  text(body, `Align all ${puzzle.data.nodes.length} power conduit relays to 0\u00b0 [\u2501] to unlock:`);
  const row = document.createElement('div');
  row.className = 'puzzle-nodes-row';
  const glyphs = ['\u2501', '\u250f', '\u2503', '\u2517'];
  puzzle.data.nodes.forEach((value, index) => button(row, `${glyphs[value % 4]} (${value * 90}\u00b0)`, { action: 'rotate', node_index: index }, send,
    `puzzle-node-btn ${value === 0 ? 'aligned' : ''}`));
  body.appendChild(row);
}

function signal(body, puzzle, send) {
  text(body, `Enter Target Hex Sequence in order: ${puzzle.data.target_sequence.map((s, i) => i < puzzle.data.progress ? `[complete ${s}]` : s).join('  ')}`);
  const row = document.createElement('div');
  row.className = 'puzzle-nodes-row';
  for (const symbol of puzzle.data.options) button(row, symbol, { action: 'input_symbol', symbol }, send);
  body.appendChild(row);
}

function timed(body, puzzle, send, now) {
  const session = puzzle.session;
  text(body, 'Timed input fixture: the server owns the deadline and accepts input only.');
  if (session.status === 'ready') button(body, 'Start attempt', { action: 'start_attempt' }, send);
  else {
    text(body, `Server deadline ${session.deadline.toFixed(3)}; server time ${now.toFixed(3)}; remaining ${Math.max(0, session.deadline - now).toFixed(2)}s; ${session.status}`, 'qte-deadline');
    if (session.status === 'active') button(body, `Input ${puzzle.data.input}`, { action: 'timed_input', symbol: puzzle.data.input, attempt_id: session.id }, send);
    else text(body, 'Attempt expired or consumed. This fixture permits one attempt.', 'qte-expired');
  }
}

export function refreshPuzzleTime(body, puzzle, now) {
  const element = body.querySelector('.qte-deadline');
  if (element && puzzle.definition?.handler === 'timed_input') {
    const session = puzzle.session;
    element.textContent = `Server deadline ${session.deadline.toFixed(3)}; server time ${now.toFixed(3)}; remaining ${Math.max(0, session.deadline - now).toFixed(2)}s; ${session.status}`;
  }
}

export const PUZZLE_RENDERERS = Object.freeze({ power_reroute: power, signal_decrypt: signal, timed_input: timed });

export function renderPuzzle(body, puzzle, send, { now = 0, player = null, busy = false } = {}) {
  body.replaceChildren();
  const definition = puzzle.definition;
  const renderer = PUZZLE_RENDERERS[definition?.handler || puzzle.data.type];
  if (!renderer) { text(body, 'Unsupported terminal handler'); return; }
  const input = action => send(puzzle.session ? { ...action, puzzle_session_id: puzzle.session.id } : action);
  renderer(body, puzzle, input, now);
  if (player?.class_id === 'tech' && (definition?.allow_tech_bypass ?? true)) button(body, '[TECH PASSIVE] HARDWARE OVERRIDE (INSTANT SOLVE)', { action: 'tech_bypass' }, input, 'tech-bypass-btn');
  body.querySelectorAll('button').forEach(element => { element.disabled = busy; });
}
