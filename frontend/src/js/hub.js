import { html, joinHtml } from './ui.js';

// Both consoles use the authoritative shared roster/stash and existing domain commands.
export function renderService(body, service, state, localId, request, send, busy) {
  const leader = state.leader_id === localId;
  if (service.type === 'stash') {
    body.innerHTML = html`<p>Assign shared gear to the squad. Starter gear cannot be banked.</p>
      <label>Recipient <select class="stash-recipient">${joinHtml(state.players.map(p => html`<option value="${p.id}">${p.name}</option>`))}</select></label>
      <div class="hub-stock">${joinHtml(state.shared_stash.map(item => html`<div class="inv-slot-row"><span>${item.name} x${item.count}</span><button class="mini-btn hub-withdraw" data-template-id="${item.template_id}">Take</button></div>`))}</div>
      ${joinHtml(state.players.map(p => html`<h4>${p.name}</h4>${joinHtml([...p.inventory.storage, ...Object.values(p.inventory.equipped).filter(Boolean)].filter(it => !it.starter).map(it => html`<div class="inv-slot-row"><span>${it.name}</span><button class="mini-btn hub-deposit" data-item-id="${it.id}" data-owner="${p.id}">Bank</button></div>`))}`))}`;
    body.querySelector('.stash-recipient').value = localId;
    body.querySelectorAll('.hub-withdraw').forEach(button => button.onclick = () => request({ type: 'stash_to_player', template_id: button.dataset.templateId,
      target_player_id: body.querySelector('.stash-recipient').value, target_id: service.id }));
    body.querySelectorAll('.hub-deposit').forEach(button => button.onclick = () => request({ type: 'player_to_stash', item_id: button.dataset.itemId,
      target_player_id: button.dataset.owner, target_id: service.id }));
  } else if (service.type === 'mission_selection') {
    const ready = state.players.length > 0 && state.players.every(p => p.ready);
    body.innerHTML = html`<p>Leader selects an unlocked mission. Prepare, then every operative readies before departure.</p>
      ${joinHtml(state.levels.map(level => html`<button class="btn-secondary hub-level" data-level-id="${level.level_id}">${level.name}${level.level_id === state.selected_level_id ? ' (Selected)' : ''}${!level.unlocked ? ' (Locked)' : ''}</button>`))}
      <p>${joinHtml(state.players.map(p => html`<span>${p.name}: ${p.ready ? 'Ready' : 'Not Ready'} </span>`))}</p>
      <button class="btn-primary hub-prepare">Prepare Mission</button>
      <button class="btn-secondary hub-ready">${state.players.find(p => p.id === localId)?.ready ? 'Cancel Ready' : 'Ready Up'}</button>
      <button class="btn-primary hub-depart">Depart</button>`;
    body.querySelectorAll('.hub-level').forEach(button => {
      button.disabled = !leader || !state.levels.find(level => level.level_id === button.dataset.levelId).unlocked || state.phase !== 'lobby';
      button.onclick = () => request({ type: 'select_mission', level_id: button.dataset.levelId, prepare: false, target_id: service.id });
    });
    body.querySelector('.hub-prepare').disabled = !leader || state.phase !== 'lobby';
    body.querySelector('.hub-ready').disabled = state.phase !== 'prep';
    body.querySelector('.hub-depart').disabled = !leader || !ready || state.phase !== 'prep';
    body.querySelector('.hub-prepare').onclick = () => request({ type: 'prepare_mission', target_id: service.id });
    body.querySelector('.hub-ready').onclick = () => send({ type: 'toggle_ready' });
    body.querySelector('.hub-depart').onclick = () => request({ type: 'start_mission', target_id: service.id });
  }
  if (busy) body.querySelectorAll('button').forEach(button => { button.disabled = true; });
}
