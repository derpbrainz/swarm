import test from 'node:test';
import assert from 'node:assert/strict';
import { InteractionModal } from '../../frontend/src/js/modal.js';

function fixture() {
  const sent = [];
  const modal = new InteractionModal(message => { sent.push(message); return true; }, () => {});
  const puzzle = { id: 'puzzle', solved: false, data: { nodes: [1] } };
  const crate = { id: 'crate', locked: false, items: [] };
  const state = { phase: 'active', world_id: 'world', interaction_revision: 1, map: { puzzles: [puzzle], crates: [crate] } };
  modal.reconcile(state, 'session');
  const reply = extra => ({ ...sent.at(-1), type: 'interact_result', interaction_revision: 1, ...extra });
  return { modal, sent, puzzle, crate, state, reply };
}

test('closed target generation wrong request world session and target ignore late replies', () => {
  const { modal, reply } = fixture();
  modal.request({ type: 'interact' }, 'crate');
  const good = reply({ crate: { id: 'crate', items: [] }, target_id: 'crate' });
  for (const key of ['request_id', 'session_id', 'world_id', 'target_id']) assert.equal(modal.receive({ ...good, [key]: 'old' }), false);
  modal.close();
  assert.equal(modal.receive(good), false);
  assert.equal(modal.active, null);
});

test('pending duplicate actions blocked and matching response alone opens chosen target', () => {
  const { modal, crate, reply } = fixture();
  assert.equal(modal.request({ type: 'interact' }, 'crate'), true);
  assert.equal(modal.request({ type: 'interact' }, 'puzzle'), false);
  assert.equal(modal.receive(reply({ crate, target_id: 'crate' })), true);
  assert.equal(modal.active.kind, 'chest');
  assert.equal(modal.busy, false);
});

test('co-op snapshots update active puzzle then solve/delete close, unrelated target stays unchanged', () => {
  const { modal, puzzle, state } = fixture();
  modal.setActive('puzzle', puzzle, 'puzzle');
  modal.reconcile({ ...state, map: { ...state.map, puzzles: [{ ...puzzle, data: { nodes: [2] } }] } }, 'session');
  assert.equal(modal.active.data.data.nodes[0], 2);
  modal.reconcile({ ...state, map: { ...state.map, puzzles: [{ ...puzzle, solved: true }] } }, 'session');
  assert.equal(modal.active, null);
  modal.setActive('puzzle', puzzle, 'puzzle');
  modal.reconcile({ ...state, map: { ...state.map, puzzles: [] } }, 'session');
  assert.equal(modal.active, null);
});

test('co-op crate puzzle unlock switches only its modal and deleted chest closes', () => {
  const { modal, state, puzzle, crate } = fixture();
  const locked = { ...crate, locked: true, puzzle };
  modal.reconcile({ ...state, map: { puzzles: [], crates: [locked] } }, 'session');
  modal.setActive('puzzle', puzzle, crate.id);
  modal.reconcile({ ...state, map: { puzzles: [], crates: [{ ...locked, locked: false, puzzle: { ...puzzle, solved: true } }] } }, 'session');
  assert.equal(modal.active.kind, 'chest');
  modal.reconcile({ ...state, map: { puzzles: [], crates: [] } }, 'session');
  assert.equal(modal.active, null);
});

test('snapshot revision wins delayed open after teammate solved before open reply', () => {
  const { modal, puzzle, state, reply } = fixture();
  modal.request({ type: 'interact' }, 'puzzle');
  modal.reconcile({ ...state, interaction_revision: 2, map: { ...state.map, puzzles: [{ ...puzzle, solved: true }] } }, 'session');
  modal.receive(reply({ puzzle, target_id: 'puzzle' }));
  assert.equal(modal.active, null);
});

test('phase world and connection changes invalidate pending replies and active instances', () => {
  for (const change of [{ phase: 'victory' }, { world_id: 'new-world' }, { session: 'new-session' }]) {
    const { modal, state, crate, reply } = fixture();
    modal.request({ type: 'interact' }, crate.id);
    const delayed = reply({ crate, target_id: crate.id });
    modal.reconcile({ ...state, ...change }, change.session || 'session');
    assert.equal(modal.receive(delayed), false);
    assert.equal(modal.active, null);
  }
});

test('unchanged puzzle response releases disabled controls and a repeated close key cannot reopen', () => {
  const { modal, puzzle, reply } = fixture();
  let changes = 0;
  modal.onChange = () => changes++;
  modal.setActive('puzzle', puzzle, puzzle.id);
  modal.request({ type: 'puzzle_action' }, puzzle.id);
  const before = changes;
  modal.receive(reply({ type: 'puzzle_update', puzzle, target_id: puzzle.id }));
  assert.equal(modal.busy, false);
  assert.ok(changes > before);
});
