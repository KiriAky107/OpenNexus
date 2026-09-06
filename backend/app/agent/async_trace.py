"""Serialize and batch durable Trace writes off the asyncio event loop."""
import asyncio
from contextvars import copy_context


class AsyncTraceWriter:
    def __init__(self, repository):
        self.repository = repository
        self.queue = asyncio.Queue(maxsize=1024)
        self.worker = None

    async def submit(self, operation, *args):
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((operation, args, future))
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._drain())
        # Cancellation must not let an older snapshot commit after cancellation.
        cancelled = False
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                cancelled = True
        future.result()
        return cancelled

    async def _drain(self):
        while not self.queue.empty():
            batch = []
            while len(batch) < 64 and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            try:
                work = asyncio.get_running_loop().run_in_executor(
                    None, copy_context().run, self.repository.write_batch, [(op, args) for op, args, _ in batch])
                # asyncio.run/shutdown may cancel every Task simultaneously. The
                # executor Future survives; finish it and release all waiters.
                while not work.done():
                    try:
                        await asyncio.shield(work)
                    except asyncio.CancelledError:
                        pass
                work.result()
            except Exception as exc:
                for _, _, future in batch:
                    future.set_exception(exc)
            else:
                for _, _, future in batch:
                    future.set_result(None)
            finally:
                for _ in batch:
                    self.queue.task_done()
