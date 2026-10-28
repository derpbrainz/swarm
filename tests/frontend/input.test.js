import test from 'node:test';
import assert from 'node:assert/strict';
import { InputContext, isEditing } from '../../frontend/src/js/input.js';
import { canvasPoint, safeColor } from '../../frontend/src/js/ui.js';

function fixture() {
  const messages = [];
  const play = { value: true };
  const input = new InputContext(message => messages.push(message), () => play.value, () => ({ x: 20, y: 10 }), () => false, () => messages.push({ type: 'interact' }));
  const event = (code, repeat = false, target = null) => ({ code, repeat, target, preventDefault() {} });
  return { input, messages, play, event };
}

test('every reset clears movement shift firing and immediately sends neutral intent', () => {
  const { input, messages, event } = fixture();
  input.keydown(event('KeyW')); input.keydown(event('ShiftLeft')); input.firing = true;
  input.reset();
  assert.deepEqual(input.keys, { up: false, down: false, left: false, right: false, shift: false });
  assert.equal(input.firing, false);
  assert.deepEqual(messages.at(-1), { type: 'input', dx: 0, dy: 0, firing: false, running: false });
});

test('modal inactive hidden blur and all editing contexts suppress combat and movement', () => {
  for (const reason of ['modal', 'blur', 'hidden', 'editing']) {
    const { input, messages, play, event } = fixture();
    let target;
    if (reason === 'modal') play.value = false;
    if (reason === 'blur') input.focused = false;
    if (reason === 'hidden') input.hidden = true;
    if (reason === 'editing') target = { closest: () => ({}) };
    for (const code of ['KeyR', 'KeyF', 'Digit1', 'Digit2', 'Space', 'KeyQ', 'KeyE', 'KeyW']) input.keydown(event(code, false, target));
    input.firing = true;
    assert.equal(messages.length, 0);
    assert.equal(input.intent(target).firing, false);
    assert.equal(input.intent(target).dx, 0);
  }
  assert.ok(isEditing({ closest: () => ({}) }));
});

test('held and repeat keydown never retrigger discrete actions, keyup permits next action', () => {
  const { input, messages, event } = fixture();
  for (const code of ['KeyR', 'KeyF', 'Space', 'KeyQ', 'Digit1', 'Digit2', 'KeyE']) {
    const before = messages.length;
    input.keydown(event(code)); input.keydown(event(code)); input.keydown(event(code, true));
    assert.equal(messages.length, before + 1);
    input.keyup(event(code)); input.keydown(event(code));
    assert.equal(messages.length, before + 2);
  }
});

test('Shift always sends raw stationary and backward fire intent for server eligibility', () => {
  const { input, event } = fixture();
  input.keydown(event('ShiftLeft')); input.firing = true;
  assert.equal(input.intent().firing, true);
  assert.equal(input.intent().running, true);
  assert.equal(input.intent().dx, 0);
  input.keydown(event('KeyW'));
  assert.equal(input.intent().firing, true);
  assert.ok(input.intent().dx < 0);
});

test('DPR uses CSS pixel mouse coordinates including offset/scaled canvas bounds', () => {
  assert.deepEqual(canvasPoint({ clientX: 210, clientY: 120 }, { getBoundingClientRect: () => ({ left: 10, top: 20, width: 400, height: 200 }) }, { screenW: 800, screenH: 400 }), { x: 400, y: 200 });
  assert.equal(safeColor('#abcdef'), '#abcdef');
  assert.equal(safeColor('red; background:url(https://evil)'), '#38bdf8');
});
