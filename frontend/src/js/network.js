export class ClientSession {
  constructor(onMessage, onDisconnect) {
    this.onMessage = onMessage;
    this.onDisconnect = onDisconnect;
    this.socket = null;
    this.generation = 0;
    this.playerId = null;
    this.state = null;
  }

  connect() {
    const generation = ++this.generation;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = this.socket = new WebSocket(`${proto}//${location.host}/ws`);
    socket.addEventListener('message', event => {
      if (generation !== this.generation || socket !== this.socket) return;
      let message;
      try { message = JSON.parse(event.data); }
      catch { console.warn('Invalid server message'); return; }
      if (message.type === 'welcome') this.playerId = message.player_id;
      if (message.type === 'welcome' || message.type === 'state') this.state = message.state || message;
      this.onMessage(message);
    });
    socket.addEventListener('close', () => {
      if (generation !== this.generation) return;
      this.onDisconnect();
      this.playerId = null;
      this.state = null;
      setTimeout(() => { if (generation === this.generation) this.connect(); }, 1500);
    });
  }

  send(payload) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify(payload));
    return true;
  }
}
