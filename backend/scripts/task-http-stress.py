"""使用单独的临时 Uvicorn 进程进行真实环回 HTTP 任务负载。"""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from time import perf_counter

import httpx


def stats(values):
    values = sorted(values)
    return {'count': len(values), 'p95_ms': round(values[math.ceil(len(values)*.95)-1], 2),
            'max_ms': round(values[-1], 2)} if values else {'count': 0}


async def main(args):
    with tempfile.TemporaryDirectory(prefix='notes-task-http-') as directory:
        root = Path(directory)
        env = {**os.environ, 'APP_DATA_DIR': str(root/'data'), 'APP_DB_PATH': str(root/'app.db'), 'APP_VAULT_PATH': str(root/'vault')}
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port), '--log-level', 'error'],
            cwd=Path(__file__).resolve().parents[1], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=30) as client:
                for _ in range(150):
                    if process.poll() is not None: raise RuntimeError('Isolated Uvicorn exited')
                    try:
                        (await client.get('/health')).raise_for_status(); break
                    except httpx.HTTPError: await asyncio.sleep(.1)
                else: raise TimeoutError('Isolated Uvicorn startup')
                sem = asyncio.Semaphore(args.concurrency)
                timings = {key: [] for key in ('create', 'update', 'list', 'delete', 'health')}
                errors = []
                async def request(method, path, kind, **kwargs):
                    start = perf_counter()
                    response = await client.request(method, path, **kwargs)
                    timings[kind].append((perf_counter()-start)*1000)
                    response.raise_for_status()
                    return response.json()
                health_stop = asyncio.Event()
                async def health():
                    while not health_stop.is_set():
                        try: await request('GET', '/health', 'health')
                        except httpx.HTTPError as error: errors.append(type(error).__name__)
                        try:
                            await asyncio.wait_for(health_stop.wait(), timeout=.05)
                        except TimeoutError:
                            pass
                heartbeat = asyncio.create_task(health())
                start = perf_counter()
                try:
                    async def create(index):
                        async with sem:
                            return (await request('POST', '/api/tasks', 'create', json={'title': f'HTTP 压测 {index}'}))['task_id']
                    ids = await asyncio.gather(*(create(i) for i in range(args.count)))
                    seen = []
                    for offset in range(0, args.count, 100):
                        page = await request('GET', f'/api/tasks?limit=100&offset={offset}', 'list')
                        seen.extend(item['task_id'] for item in page['items'])
                    assert set(seen) == set(ids) and len(seen) == args.count
                    async def change(task_id):
                        async with sem:
                            updated = await request('PATCH', f'/api/tasks/{task_id}', 'update', json={'status': 'done'})
                            assert updated['status'] == 'done'
                            await request('DELETE', f'/api/tasks/{task_id}', 'delete')
                    await asyncio.gather(*(change(task_id) for task_id in ids))
                    remaining = await request('GET', '/api/tasks', 'list')
                    assert remaining['page']['total'] == 0
                finally:
                    health_stop.set()
                    await asyncio.wait_for(heartbeat, timeout=35)
                report = {'transport': 'real loopback HTTP, separate Uvicorn process', 'tasks': args.count,
                    'concurrency': args.concurrency, 'elapsed_ms': round((perf_counter()-start)*1000, 2),
                    'latencies': {key: stats(value) for key,value in timings.items()}, 'health_errors': errors,
                    'pagination_complete': True, 'final_total': 0}
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(report, ensure_ascii=False))
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--concurrency', type=int, default=20)
    args = parser.parse_args()
    if args.count < 1 or args.concurrency < 1: parser.error('count and concurrency must be positive')
    asyncio.run(main(args))
