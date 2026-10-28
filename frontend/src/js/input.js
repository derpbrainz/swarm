import { screenInputToWorldDelta } from './iso.js';

export function isEditing(target) {
  return Boolean(target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"])'));
}

export class InputContext {
  constructor(send, canPlay, aim, closeModal, interact) {
    this.send = send;
    this.canPlay = canPlay;
    this.aim = aim;
    this.closeModal = closeModal;
    this.interact = interact;
    this.keys = { up: false, down: false, left: false, right: false, shift: false };
    this.held = new Set();
    this.firing = false;
    this.focused = true;
    this.hidden = false;
  }

  reset() {
    for (const key of Object.keys(this.keys)) this.keys[key] = false;
    this.held.clear();
    this.firing = false;
    this.send({ type: 'input', dx: 0, dy: 0, firing: false, running: false });
  }

  allowed(target) {
    return this.focused && !this.hidden && this.canPlay() && !isEditing(target);
  }

  keydown(event) {
    if (isEditing(event.target)) return;
    const code = event.code;
    const repeat = event.repeat || this.held.has(code);
    this.held.add(code);
    if ((code === 'Escape' || code === 'KeyE') && !repeat && this.closeModal()) {
      this.held.add(code);
      event.preventDefault();
      return;
    }
    if (!this.allowed(event.target)) return;
    const movement = { KeyW: 'up', ArrowUp: 'up', KeyS: 'down', ArrowDown: 'down', KeyA: 'left', ArrowLeft: 'left', KeyD: 'right', ArrowRight: 'right', ShiftLeft: 'shift', ShiftRight: 'shift' };
    if (movement[code]) { this.keys[movement[code]] = true; event.preventDefault(); }
    if (repeat) return;
    const actions = {
      KeyR: { type: 'reload' }, Digit1: { type: 'inventory_action', action: 'switch_weapon', slot: 'primary_weapon' },
      Digit2: { type: 'inventory_action', action: 'switch_weapon', slot: 'secondary_weapon' },
      KeyF: { type: 'inventory_action', action: 'use_item', slot: 'quick_use' }, Space: { type: 'skill' }, KeyQ: { type: 'skill' },
    };
    if (code === 'KeyE') { event.preventDefault(); this.interact(); this.held.add(code); }
    else if (actions[code]) { event.preventDefault(); this.send(actions[code]); }
  }

  keyup(event) {
    this.held.delete(event.code);
    const movement = { KeyW: 'up', ArrowUp: 'up', KeyS: 'down', ArrowDown: 'down', KeyA: 'left', ArrowLeft: 'left', KeyD: 'right', ArrowRight: 'right', ShiftLeft: 'shift', ShiftRight: 'shift' };
    if (movement[event.code]) this.keys[movement[event.code]] = false;
  }

  intent(target) {
    const allowed = this.allowed(target);
    const move = allowed ? screenInputToWorldDelta(this.keys.up, this.keys.down, this.keys.left, this.keys.right) : { dx: 0, dy: 0 };
    return { type: 'input', ...move, aim_x: this.aim().x, aim_y: this.aim().y,
      firing: allowed && this.firing, running: allowed && this.keys.shift };
  }
}
