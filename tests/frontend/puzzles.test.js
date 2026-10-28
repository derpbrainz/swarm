import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderPuzzle, PUZZLE_RENDERERS } from '../../frontend/src/js/puzzles.js';

class Element {
  constructor() { this.children = []; this.textContent = ''; this.className = ''; }
  appendChild(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  addEventListener(type, callback) { this.click = callback; }
  querySelectorAll(type) { return this.children.flatMap(c => [...(c.click ? [c] : []), ...c.querySelectorAll(type)]); }
}
globalThis.document = { createElement: () => new Element() };

test('bounded renderers support data-only power and signal variants with input-only payloads', () => {
  assert.deepEqual(Object.keys(PUZZLE_RENDERERS), ['power_reroute', 'signal_decrypt', 'timed_input']);
  const body = new Element();
  const sent = [];
  renderPuzzle(body, { definition: { handler: 'power_reroute', allow_tech_bypass: true }, data: { nodes: [1, 3] } }, c => sent.push(c));
  assert.equal(body.querySelectorAll('button').length, 2);
  body.querySelectorAll('button')[1].click();
  assert.deepEqual(sent[0], { action: 'rotate', node_index: 1 });
  renderPuzzle(body, { definition: { handler: 'signal_decrypt' }, data: { target_sequence: ['<svg onload=alert()>'], options: ['YES', 'NO'], progress: 0 } }, c => sent.push(c));
  assert.match(body.children[0].textContent, /<svg/);
  body.querySelectorAll('button')[0].click();
  assert.deepEqual(sent[1], { action: 'input_symbol', symbol: 'YES' });
});

test('timed renderer exposes server deadline and attempt inputs, expired has no input or bypass', () => {
  const body = new Element();
  const sent = [];
  const puzzle = { definition: { handler: 'timed_input', allow_tech_bypass: false }, data: { input: 'ACK' }, session: { id: 'ready', status: 'ready' } };
  renderPuzzle(body, puzzle, c => sent.push(c), { player: { class_id: 'tech' } });
  body.querySelectorAll('button')[0].click();
  assert.deepEqual(sent[0], { action: 'start_attempt', puzzle_session_id: 'ready' });
  puzzle.session = { id: 'attempt', status: 'active', deadline: 12 };
  renderPuzzle(body, puzzle, c => sent.push(c), { now: 11, busy: true });
  assert.match(body.children[1].textContent, /remaining 1.00s/);
  assert.equal(body.querySelectorAll('button')[0].disabled, true);
  body.querySelectorAll('button')[0].click();
  assert.deepEqual(sent[1], { action: 'timed_input', symbol: 'ACK', attempt_id: 'attempt', puzzle_session_id: 'attempt' });
  puzzle.session.status = 'expired';
  renderPuzzle(body, puzzle, () => {}, { now: 13, player: { class_id: 'tech' } });
  assert.equal(body.querySelectorAll('button').length, 0);
  assert.match(body.children[2].textContent, /expired/);
});
