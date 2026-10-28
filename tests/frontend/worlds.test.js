import test from 'node:test';
import assert from 'node:assert/strict';
import { InteractionModal } from '../../frontend/src/js/modal.js';

test('lobby and prep services use world capabilities and stale world replies cannot reopen', () => {
  const sent = [], changes = [];
  const modal = new InteractionModal(c => { sent.push(c); return true; }, active => changes.push(active));
  const service = { id: 'stash', type: 'stash', label: 'Shared Stash' };
  const state = { phase: 'lobby', world_id: 'hub1', world_capabilities: { interactions: true }, map: { interactions: [service] } };
  modal.reconcile(state, 'player');
  assert.equal(modal.request({ type: 'interact', target_id: service.id }, service.id), true);
  const request = sent.at(-1);
  assert.equal(modal.receive({ ...request, type: 'interact_result', service }), true);
  assert.equal(modal.active.kind, 'service');
  modal.reconcile({ ...state, phase: 'prep' }, 'player');
  assert.equal(modal.active, null);
  assert.equal(modal.request({ type: 'stash_to_player', template_id: 'medkit', target_id: 'stash' }, 'stash'), true);
  const late = sent.at(-1);
  modal.reconcile({ ...state, phase: 'active', world_id: 'mission', map: { interactions: [] } }, 'player');
  assert.equal(modal.receive({ ...late, type: 'interact_result', service }), false);
  assert.equal(modal.active, null);
  assert.ok(changes.includes(null));
});

test('removed service and disabled interaction rules close authoritative modal', () => {
  const modal = new InteractionModal(() => true, () => {});
  const service = { id: 'missions', type: 'mission_selection' };
  const state = { phase: 'active', world_id: 'hub', world_capabilities: { interactions: true }, map: { interactions: [service] } };
  modal.reconcile(state, 'player'); modal.setActive('service', service, service.id);
  modal.reconcile({ ...state, map: { interactions: [] } }, 'player');
  assert.equal(modal.active, null);
  modal.setActive('service', service, service.id);
  modal.reconcile({ ...state, world_capabilities: { interactions: false } }, 'player');
  assert.equal(modal.active, null);
  assert.equal(modal.request({ type: 'interact' }), false);
});
