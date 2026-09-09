"""继承的 Host 管道上的同步、有界 RPC（绝不是 HTTP 或 env 机密）。"""
from __future__ import annotations
import json
import queue
import threading
import uuid


class HostBridge:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.pending = {}
        self.lock = threading.Lock()
        self.closed = threading.Event()

    def call(self, method, **params):
        request_id = uuid.uuid4().hex
        result = queue.Queue(maxsize=1)
        payload = json.dumps({"rpc": method, "request_id": request_id, "params": params}, separators=(",", ":"))
        if len(payload.encode()) > (8 * 1024 * 1024):
            raise RuntimeError("HOST_REQUEST_TOO_LARGE")
        with self.lock:
            if self.closed.is_set():
                raise RuntimeError("HOST_UNAVAILABLE")
            self.pending[request_id] = result
            try:
                self.writer.write(payload + "\n")
                self.writer.flush()
            except Exception:
                self.pending.pop(request_id, None)
                raise RuntimeError("HOST_UNAVAILABLE") from None
        try:
            response = result.get(timeout=30)
            if response.get("error"):
                raise RuntimeError(response["error"])
            return response.get("result")
        except queue.Empty:
            raise RuntimeError("HOST_TIMEOUT") from None
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def listen(self, on_disconnect):
        try:
            while line := self.reader.readline((8 * 1024 * 1024 + 1)):
                if len(line) > (8 * 1024 * 1024):
                    break
                message = json.loads(line)
                with self.lock:
                    target = self.pending.get(message.get("request_id"))
                if target is not None:
                    try:
                        target.put_nowait(message)
                    except queue.Full:
                        pass
        finally:
            self.closed.set()
            with self.lock:
                for result in self.pending.values():
                    try:
                        result.put_nowait({"error": "HOST_UNAVAILABLE"})
                    except queue.Full:
                        pass
            on_disconnect()


active: HostBridge | None = None

# 仅由经过身份验证的 Host HTTP 传输设置；由Agent任务继承。
from contextvars import ContextVar
vault_id: ContextVar[str | None] = ContextVar("host_vault_id", default=None)
operation_id: ContextVar[str | None] = ContextVar("host_operation_id", default=None)
