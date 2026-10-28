"""Bounded single-sender delivery: reliable feedback and latest-only snapshots."""

import asyncio
from collections import deque
from contextlib import suppress
import json
import time
from starlette.websockets import WebSocketDisconnect


class Connection:
    def __init__(self, websocket, on_disconnect, *, reliable_limit=64, send_timeout=2.0):
        self.websocket = websocket
        self.on_disconnect = on_disconnect
        self.reliable_limit = reliable_limit
        self.send_timeout = send_timeout
        self.reliable = deque()
        self.snapshot = None
        self.ready = asyncio.Event()
        self.closed = False
        self.tokens = 120.0
        self.updated = time.monotonic()
        self.task = asyncio.create_task(self.run())

    def admit(self, now=None):
        now = time.monotonic() if now is None else now
        self.tokens = min(120.0, self.tokens + max(0, now - self.updated) * 60)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True

    def enqueue(self, payload, *, snapshot=False):
        if self.closed:
            return False
        text = json.dumps(payload, allow_nan=False)
        if snapshot:
            self.snapshot = text
        elif len(self.reliable) >= self.reliable_limit:
            self.disconnect()
            self.task.cancel()
            return False
        else:
            self.reliable.append(text)
        self.ready.set()
        return True

    def disconnect(self):
        if not self.closed:
            self.closed = True
            self.reliable.clear()
            self.snapshot = None
            self.on_disconnect()

    async def run(self):
        try:
            while not self.closed:
                await self.ready.wait()
                while self.reliable or self.snapshot is not None:
                    if self.reliable:
                        text = self.reliable.popleft()
                    else:
                        text, self.snapshot = self.snapshot, None
                    await asyncio.wait_for(self.websocket.send_text(text), timeout=self.send_timeout)
                self.ready.clear()
        except (OSError, RuntimeError, asyncio.TimeoutError, WebSocketDisconnect):
            pass
        finally:
            self.disconnect()
            with suppress(OSError, RuntimeError, asyncio.TimeoutError, WebSocketDisconnect):
                await asyncio.wait_for(self.websocket.close(), timeout=self.send_timeout)

    async def close(self):
        self.disconnect()
        self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task
