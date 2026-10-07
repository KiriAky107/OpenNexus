"""Run the real Sync service with owned SQLite/object storage and private TLS.

Fault controls use the parent's stdin, never a remotely accessible endpoint.
Certificates are created by the native driver, which already has cryptography.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import sys
import threading

repo = Path(__file__).resolve().parents[2]
service = Path.cwd().resolve()
if service not in {(repo.parent/'Sync-for-OpenNexus').resolve(), (repo/'.build/sync-server').resolve()}:
    raise RuntimeError('Run only from the fixed Sync fixture checkout')
sys.path.insert(0, str(service))

from fastapi.responses import JSONResponse
from sqlalchemy import text
import uvicorn

from sync_server.app import create_app
from sync_server.database import Database
from sync_server.storage import DiskObjects
if not Path(sys.modules['sync_server.app'].__file__).resolve().is_relative_to(service):
    raise RuntimeError('Sync fixture loaded a different service module')


def main():
    root = Path(sys.argv[1]).resolve(strict=True)
    if not (root / '.opennexus-test').is_file():
        raise RuntimeError('ISOLATED_TEST_ROOT_REQUIRED')
    for name in ('leaf.pem', 'leaf.key', 'ca.pem'):
        if not (root / name).is_file() or (root / name).resolve().parent != root:
            raise RuntimeError('OWNED_CERTIFICATE_REQUIRED')
    if (root / 'sync.sqlite3').exists():
        raise RuntimeError('FRESH_DATABASE_REQUIRED')
    database = Database('sqlite:///' + str(root / 'sync.sqlite3'))
    app = create_app(database, DiskObjects(root / 'objects'), root / 'staging')
    database.add_user('native-fixture', 'controlled-fixture-password')
    fault = threading.Event()
    counts = {'changes_503': 0, 'changes_success': 0}

    @app.middleware('http')
    async def controlled_outage(request, call_next):
        changes = request.method == 'GET' and request.url.path.endswith('/changes')
        if changes and fault.is_set():
            counts['changes_503'] += 1
            return JSONResponse({'error': {'code': 'TEMPORARILY_UNAVAILABLE', 'details': {}}},
                                status_code=503, headers={'Retry-After': '8'})
        response = await call_next(request)
        if changes and response.status_code == 200:
            counts['changes_success'] += 1
        return response

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False,
        timeout_graceful_shutdown=1, ssl_certfile=str(root / 'leaf.pem'),
        ssl_keyfile=str(root / 'leaf.key')))

    def emit(value):
        # Redirected Windows stdout may use a legacy code page. ASCII JSON keeps
        # the control protocol valid UTF-8 while preserving decoded Unicode.
        print(json.dumps(value), flush=True)

    def parent():
        try:
            for line in sys.stdin:
                command = json.loads(line)
                action = command.get('action')
                if action == 'fault':
                    fault.set() if command['enabled'] else fault.clear()
                    emit({'action': action, 'enabled': fault.is_set()})
                elif action == 'state':
                    with database.transaction() as conn:
                        # Deliberately exclude all session and password columns.
                        tables = {}
                        for name, query in {
                            'devices': 'SELECT id,name,revoked FROM devices ORDER BY name',
                            'vaults': 'SELECT id,name,sequence,used,quota FROM vaults',
                            'revisions': 'SELECT sequence,path,hash,size,device_id,operation_id FROM revisions ORDER BY sequence',
                        }.items():
                            tables[name] = [dict(row) for row in conn.execute(text(query)).mappings()]
                    emit({'action': action, **tables, 'requests': dict(counts)})
                else:
                    raise ValueError('UNKNOWN_OWNED_CONTROL')
        finally:
            server.should_exit = True

    threading.Thread(target=parent, daemon=True).start()

    async def run():
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if task.done():
                await task
                raise RuntimeError('FIXTURE_START_FAILED')
            await asyncio.sleep(.01)
        emit({'url': f'https://127.0.0.1:{sock.getsockname()[1]}/',
              'ca_path': str(root / 'ca.pem'), 'storage': 'owned SQLite/DiskObjects'})
        await task

    try:
        asyncio.run(run())
    finally:
        sock.close()
        database.engine.dispose()


if __name__ == '__main__':
    main()
