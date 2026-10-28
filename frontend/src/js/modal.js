// A reply may update only the current request generation, target, world and connection.
export class InteractionModal {
  constructor(send, onChange) {
    this.send = send;
    this.onChange = onChange;
    this.sessionId = null;
    this.worldId = null;
    this.phase = null;
    this.generation = 0;
    this.serial = 0;
    this.pending = null;
    this.active = null;
    this.busy = false;
    this.snapshot = null;
  }

  close() {
    const hadModal = Boolean(this.active || this.pending);
    this.generation++;
    this.pending = null;
    this.active = null;
    this.busy = false;
    this.onChange(null);
    return hadModal;
  }

  reconcile(snapshot, sessionId) {
    if (this.sessionId !== sessionId || this.worldId !== snapshot.world_id || this.phase !== snapshot.phase) this.close();
    this.sessionId = sessionId;
    this.worldId = snapshot.world_id;
    this.phase = snapshot.phase;
    this.snapshot = snapshot;
    if (!this.active) return;
    const map = snapshot.map;
    if (this.active.kind === 'service') {
      const target = map?.interactions?.find(t => t.id === this.active.targetId);
      if (!target || !snapshot.world_capabilities?.interactions) this.close();
      else this.setActive('service', target, target.id);
      return;
    }
    const crate = map?.crates?.find(c => c.id === this.active.targetId || c.puzzle?.id === this.active.data.id);
    const puzzle = map?.puzzles?.find(p => p.id === this.active.data.id) || crate?.puzzle;
    if (this.active.kind === 'puzzle') {
      if (!puzzle) this.close();
      else if (puzzle.solved) {
        if (crate && !crate.locked) this.setActive('chest', crate, crate.id);
        else this.close();
      } else {
        this.setActive('puzzle', puzzle, this.active.targetId);
      }
    } else if (!crate || crate.locked) this.close();
    else this.setActive('chest', crate, crate.id);
  }

  setActive(kind, data, targetId) {
    if (this.active?.kind === kind && this.active.targetId === targetId && JSON.stringify(this.active.data) === JSON.stringify(data)) return;
    this.active = { kind, data, targetId };
    this.onChange(this.active);
  }

  request(payload, targetId = null) {
    if (this.busy || !this.worldId || !this.sessionId || !(this.snapshot?.world_capabilities?.interactions ?? ['active', 'dropship'].includes(this.phase))) return false;
    const request = { request_id: `${this.generation}:${++this.serial}`, session_id: this.sessionId, world_id: this.worldId };
    this.pending = { ...request, generation: this.generation, targetId };
    this.busy = true;
    if (!this.send({ ...payload, ...request })) { this.pending = null; this.busy = false; return false; }
    this.onChange(this.active);
    return true;
  }

  receive(message) {
    const pending = this.pending;
    if (!pending || pending.generation !== this.generation || message.request_id !== pending.request_id ||
        message.session_id !== this.sessionId || message.world_id !== this.worldId ||
        (pending.targetId && message.target_id !== pending.targetId)) return false;
    this.pending = null;
    this.busy = false;
    if (this.snapshot && this.snapshot.interaction_revision > message.interaction_revision) {
      // Never display stale data, even for a single frame, before reconciliation.
      if (!this.active && message.puzzle) this.active = { kind: 'puzzle', data: message.puzzle, targetId: message.target_id };
      else if (!this.active && message.crate) this.active = { kind: 'chest', data: message.crate, targetId: message.target_id };
      this.reconcile(this.snapshot, this.sessionId);
      this.onChange(this.active);
      return true;
    }
    if (message.ok === false || message.type === 'command_error') { this.onChange(this.active); return true; }
    if (message.service) this.setActive('service', message.service, message.target_id);
    else if (message.puzzle && !message.puzzle.solved) this.setActive('puzzle', message.puzzle, message.target_id);
    else if (message.unlocked_crate || message.crate) {
      const crate = message.unlocked_crate || message.crate;
      this.setActive('chest', crate, crate.id);
    } else if (message.solved) this.close();
    else this.onChange(this.active);
    if (this.active) this.onChange(this.active);
    // The latest snapshot wins even when a delayed reply contains older puzzle data.
    return true;
  }
}
