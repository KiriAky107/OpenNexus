"""Bind Sync acceptance to the actual split service and reject skipped oracles."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess


class SyncServiceError(RuntimeError):
    """Known safe preflight codes, without remote response or credential text."""


def configure_service(*, fixture: bool = True) -> Path:
    configured = os.environ.get('OPENNEXUS_SYNC_SERVER_DIR')
    if not configured:
        raise SyncServiceError('SYNC_ACCEPTANCE_SERVICE_NOT_CONFIGURED')
    service = Path(configured).resolve()
    required = [service / 'sync_server/app.py',
        service / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')]
    if fixture:
        required.append(service / 'tests/host_fixture.py')
    if not all(path.is_file() for path in required):
        raise SyncServiceError('SYNC_ACCEPTANCE_SERVICE_INCOMPLETE')
    # Child Rust processes resolve this from their own working directory.
    os.environ['OPENNEXUS_SYNC_SERVER_DIR'] = str(service)
    return service


def source_receipts(root: Path, paths, *, allow_missing: bool = False) -> list[dict]:
    receipts = []
    for relative in paths:
        service_file = relative.startswith('sync-service/')
        try:
            source_root = configure_service(fixture=False) if service_file else root
            name = relative.removeprefix('sync-service/') if service_file else relative
            path = source_root / name
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        except (OSError, RuntimeError):
            if allow_missing:
                continue
            raise
        receipt = {'path': relative, 'sha256': digest}
        if service_file:
            receipt['source_root'] = str(source_root)
        receipts.append(receipt)
    return receipts


def run_exact(command: list[str], *, cwd: Path) -> bool:
    completed = subprocess.run(command, cwd=cwd, capture_output=True,
        text=True, encoding='utf-8', errors='replace', check=False)
    print(completed.stdout, end='')
    print(completed.stderr, end='')
    output = completed.stdout + completed.stderr
    return (completed.returncode == 0 and '1 passed; 0 failed' in completed.stdout
        and 'skipped:' not in output.casefold())
