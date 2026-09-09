import asyncio
import json
import logging
import threading
from pathlib import Path

from app.operation_logs import LogStore, ApplicationLogHandler, get_store, log_event, shutdown_logging


def test_logs_persist_filter_cursor_and_retention(tmp_path):
    store = LogStore(tmp_path / 'logs.db', retain=3)
    try:
        for i in range(6):
            store.emit('ERROR' if i % 2 else 'INFO', 'vectors', 'embedding.failed', {'run_id': f'run_{i}'})
        store.queue.join()
        first = store.query(limit=2)
        assert len(first['items']) == 2 and first['next_cursor']
        assert len(store.query(before=first['next_cursor'])['items']) == 1
        assert len(store.query(level='ERROR')['items']) == 2
        assert len(store.query(q='run_5')['items']) == 1
        assert not store.query(source='tasks')['items']
    finally:
        store.close()
    reopened = LogStore(tmp_path / 'logs.db', retain=3)
    try:
        assert len(reopened.query()['items']) == 3
    finally:
        reopened.close()


def test_logs_exclude_content_and_legacy_exception_messages():
    try:
        log_event('vectors', 'embedding.failed', level='ERROR',
                  error=ValueError('private note and secret'), model='embedding-v1',
                  prompt='private note', arguments={'api_key': 'secret'}, api_key='secret')
        handler = ApplicationLogHandler()
        record = logging.LogRecord('app.sample', logging.ERROR, __file__, 1,
                                   'private note and secret %s', ('credentials',), None)
        handler.emit(record)
        handler.emit(record)  # 记录器传播到另一个已安装的处理程序
        store = get_store()
        store.queue.join()
        data = json.dumps(store.query())
        assert len(store.query()['items']) == 2
        assert 'private note' not in data and 'credentials' not in data and 'api_key' not in data
        assert 'ValueError' in data and 'embedding-v1' in data
    finally:
        shutdown_logging()


def test_http_log_correlates_task_operations_without_body():
    import httpx
    from app.main import app
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/tasks', json={'title': 'private title'})
            assert response.status_code == 200
            rid = response.headers['x-request-id']
            store = get_store()
            await asyncio.to_thread(store.queue.join)
            result = (await client.get('/api/logs', params={'q': rid})).json()
            assert len(result['items']) >= 2
            assert 'private title' not in json.dumps(result)
            assert any(item['event'] == 'task.created' for item in result['items'])
            assert (await client.get('/api/logs', params={'limit': 201})).status_code == 422
    try:
        asyncio.run(scenario())
    finally:
        shutdown_logging()


def test_trace_writer_batches_off_loop_and_survives_cancel():
    from app.agent.async_trace import AsyncTraceWriter
    started, release = threading.Event(), threading.Event()
    class Repository:
        def write_batch(self, jobs):
            assert threading.current_thread() is not threading.main_thread()
            started.set()
            assert release.wait(2)
            self.jobs = jobs
    async def scenario():
        repo = Repository()
        writer = AsyncTraceWriter(repo)
        pending = asyncio.create_task(writer.submit('save', 'snapshot'))
        while not started.is_set():
            await asyncio.sleep(.001)
        pending.cancel()
        writer.worker.cancel()  # 同时应用程序关闭
        await asyncio.sleep(.005)
        assert not pending.done()
        release.set()
        assert await asyncio.wait_for(pending, 2) is True
        assert repo.jobs == [('save', ('snapshot',))]
        assert writer.queue.empty()
    asyncio.run(scenario())


def test_trace_write_failure_is_reported_and_next_submission_recovers():
    from app.agent.async_trace import AsyncTraceWriter
    class Repository:
        fail = True
        def write_batch(self, jobs):
            if self.fail:
                self.fail = False
                raise OSError('disk unavailable')
    async def scenario():
        import pytest
        writer = AsyncTraceWriter(Repository())
        with pytest.raises(OSError):
            await writer.submit('save', 'first')
        assert not await writer.submit('save', 'second')
        await writer.queue.join()
    asyncio.run(scenario())


def test_log_write_failure_does_not_stall_queue(tmp_path, monkeypatch):
    store = LogStore(tmp_path / 'failed.db')
    try:
        def broken():
            raise OSError('disk unavailable')
        monkeypatch.setattr(store, '_connect', broken)
        store.emit('ERROR', 'vectors', 'embedding.failed', {'duration_ms': float('nan')})
        store.queue.join()
        assert store.failed == 1
    finally:
        store.close()


def test_cancelled_task_write_keeps_its_slot_until_commit():
    from app.services.task_service import write_in_background
    started, release = threading.Event(), threading.Event()
    order = []
    def first():
        started.set()
        assert release.wait(2)
        order.append('first')
    async def scenario():
        import pytest
        pending = asyncio.create_task(write_in_background(first))
        while not started.is_set():
            await asyncio.sleep(.001)
        pending.cancel()
        second = asyncio.create_task(write_in_background(lambda: order.append('second')))
        await asyncio.sleep(.01)
        assert order == []
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await second
        assert order == ['first', 'second']
    asyncio.run(scenario())
