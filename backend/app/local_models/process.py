"""用于没有异步子进程支持的事件循环的管道适配器（Windows 重新加载）。"""
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
        # 即使工作线程生成格式错误的行，分配也会受到限制。
        return await asyncio.to_thread(self.pipe.readline, self.limit + 1)


class ThreadedProcess:
    def __init__(self, args, *, env, limit, creationflags=0):
        # 同步创建进程，避免取消操作留下无人管理的子进程。阻塞式管道 I/O 与进程回收在线程中执行，
        # 不占用服务器事件循环。
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
