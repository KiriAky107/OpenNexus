"""Pipe adapter for event loops without asyncio subprocess support (Windows reload)."""
from __future__ import annotations

import asyncio
import subprocess


class _Input:
    def __init__(self, pipe):
        self.pipe = pipe
        self.pending = bytearray()

    def write(self, data):
        self.pending.extend(data)

    async def drain(self):
        data = bytes(self.pending)
        self.pending.clear()

        def send():
            self.pipe.write(data)
            self.pipe.flush()

        await asyncio.to_thread(send)

    def close(self):
        self.pipe.close()


class _Output:
    def __init__(self, pipe, limit):
        self.pipe = pipe
        self.limit = limit

    async def readline(self):
        # Bound allocations even when the worker produces a malformed line.
        return await asyncio.to_thread(self.pipe.readline, self.limit + 1)


class ThreadedProcess:
    def __init__(self, args, *, env, limit, creationflags=0):
        # Spawn synchronously so cancellation cannot leave an unowned process.
        # Blocking pipe I/O and reaping run in threads, never on the server loop.
        self.process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, creationflags=creationflags,
        )
        self.stdin = _Input(self.process.stdin)
        self.stdout = _Output(self.process.stdout, limit)

    @property
    def returncode(self):
        return self.process.poll()

    def kill(self):
        self.process.kill()

    async def wait(self):
        return await asyncio.to_thread(self.process.wait)

    async def close(self):
        def close_pipes():
            self.process.stdin.close()
            self.process.stdout.close()
        await asyncio.to_thread(close_pipes)
