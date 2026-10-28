import test from 'node:test';
import assert from 'node:assert/strict';
import { ClientSession } from '../../frontend/src/js/network.js';

test('old socket callbacks never mutate new session and malformed message is harmless', t => {
  const sockets = [];
  const timers = [];
  class Socket {
    static OPEN = 1;
    readyState = 1;
    events = {};
    sent = [];
    constructor() { sockets.push(this); }
    addEventListener(name, fn) { this.events[name] = fn; }
    send(message) { this.sent.push(JSON.parse(message)); }
  }
  t.mock.method(globalThis, 'setTimeout', fn => timers.push(fn));
  const original = { WebSocket: globalThis.WebSocket, location: globalThis.location };
  globalThis.WebSocket = Socket;
  globalThis.location = { protocol: 'http:', host: 'localhost' };
  t.after(() => { globalThis.WebSocket = original.WebSocket; globalThis.location = original.location; });
  const messages = [];
  let disconnects = 0;
  const session = new ClientSession(msg => messages.push(msg), () => disconnects++);
  session.connect();
  const old = sockets[0];
  old.events.message({ data: '{' });
  old.events.message({ data: JSON.stringify({ type: 'welcome', player_id: 'one', state: { phase: 'active' } }) });
  assert.equal(session.playerId, 'one');
  session.connect();
  old.events.message({ data: JSON.stringify({ type: 'welcome', player_id: 'old' }) });
  old.events.close();
  assert.equal(disconnects, 0);
  assert.equal(messages.length, 1);
  sockets[1].events.close();
  assert.equal(disconnects, 1);
  assert.equal(session.playerId, null);
  timers[0]();
  assert.equal(sockets.length, 3);
  assert.equal(session.send({ type: 'input' }), true);
  assert.deepEqual(sockets[2].sent, [{ type: 'input' }]);
});
