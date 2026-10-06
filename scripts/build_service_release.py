"""Verify a fixed service deployment ZIP in owned local/CI fixtures only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import tomllib
from types import SimpleNamespace
import uuid
import zipfile

from release_plan import NAME, ReleaseError, atomic_json, build_source, check_local_source, digest, fixed_archive, require

PRODUCTS = {
    'sync': ('notesagent-sync', 'Sync-for-OpenNexus', 'sync_server/'),
    'community': ('notesagent-community', 'Community-for-OpenNexus', 'community/'),
}


def included(kind, name):
    common = {'pyproject.toml', 'uv.lock', 'LICENSE', 'README.md', 'README.zh-CN.md'}
    if kind == 'sync':
        return name in common | {'Dockerfile', 'Dockerfile.objects', '.dockerignore', 'compose.yaml', '.env.example',
                                  'Caddyfile.example', 'tools/operations_probe.py'} or name.startswith('sync_server/') or (
                                      name.startswith('console/') and not name.startswith('console/tests/'))
    return name in common or name.startswith(('community/', 'deployment/'))


def package(root, output, kind, commit):
    root, output = Path(root).resolve(), Path(output).resolve()
    require(kind in PRODUCTS, 'SERVICE_KIND_INVALID')
    require(output.is_relative_to(root/'.build') and output != root/'.build', 'OWNED_BUILD_OUTPUT_REQUIRED')
    before = build_source(root, commit)
    project = tomllib.loads((root/'pyproject.toml').read_text('utf-8'))['project']
    require(project['name'] == PRODUCTS[kind][0] and NAME.fullmatch(project['version']), 'SERVICE_PROJECT_MISMATCH')
    required = {'pyproject.toml', 'uv.lock', PRODUCTS[kind][2]+'__main__.py'}
    required |= {'Dockerfile', 'compose.yaml', 'tools/operations_probe.py'} if kind == 'sync' else {'deployment/opennexus-community.service'}
    require(required <= set(before['files_sha256']), 'DEPLOYMENT_FILES_MISSING')
    output.mkdir(parents=True, exist_ok=False)
    deployment = output/f'{PRODUCTS[kind][1]}_{project["version"]}_deploy.zip'
    fixed_archive(root, commit, deployment, include=lambda name: included(kind, name))
    plan = SimpleNamespace(base=output, data={'commit': commit, 'assets': [{'kind': 'deployment', 'file': deployment.name}]})
    check_local_source(plan, root)
    extracted = output/'extracted'
    extracted.mkdir()
    with zipfile.ZipFile(deployment) as archive:
        require(set(archive.namelist()) == {n for n in before['files_sha256'] if included(kind, n)}, 'DEPLOYMENT_FILE_SET_MISMATCH')
        archive.extractall(extracted)
    inputs = {'schema': 1, 'kind': kind, 'source_commit': commit, 'version': project['version'],
              'deployment': deployment.name, 'deployment_sha256': digest(deployment), 'source_provenance': before}
    atomic_json(output/'build-inputs.json', inputs)
    return inputs


def environment():
    # Fixture code never reads normal service database pointers or API tokens.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('COMMUNITY_', 'SYNC_'))
           and k.upper() not in {'PYTHONPATH', 'GH_TOKEN', 'GITHUB_TOKEN'}}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def run_cli(extracted, env, *args):
    result = subprocess.run([sys.executable, '-m', 'community', *map(str, args)], cwd=extracted, env=env,
                            capture_output=True, timeout=60)
    require(result.returncode == 0, 'PACKAGED_COMMUNITY_CLI_FAILED')
    return json.loads(result.stdout)


def community_probe(extracted, work):
    work.mkdir(exist_ok=False)
    env = environment()
    owner = 'release-smoke-'+uuid.uuid4().hex
    database = work/'catalog.sqlite3'
    env.update(COMMUNITY_DATABASE_PATH=str(database), COMMUNITY_SOURCE_ID=owner)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    # Check the actual module resolution before launching the production CLI.
    origin = subprocess.run([sys.executable, '-c',
        'from pathlib import Path; import community.cli; assert Path(community.cli.__file__).resolve().is_relative_to(Path.cwd())'],
        cwd=extracted, env=env, capture_output=True, timeout=30)
    require(origin.returncode == 0, 'PACKAGED_MODULE_ORIGIN_MISMATCH')
    child = subprocess.Popen([sys.executable, '-m', 'community', 'serve', '--host', '127.0.0.1', '--port', str(port)],
                             cwd=extracted, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    result = {'passed': False, 'cleanup_complete': False, 'module_origin_verified': True, 'checks': []}
    def get(path):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            require(response.status == 200, 'PACKAGED_COMMUNITY_HTTP_FAILED')
            return json.loads(response.read(1024*1024))
        finally:
            connection.close()
    try:
        until = time.monotonic()+30
        while True:
            require(child.poll() is None, 'PACKAGED_COMMUNITY_EXITED')
            try:
                require(get('/catalog/v1/sources')['source_id'] == owner, 'PACKAGED_HTTP_OWNER_MISMATCH')
                break
            except OSError:
                require(time.monotonic() < until, 'PACKAGED_COMMUNITY_STARTUP_TIMEOUT')
                time.sleep(.2)
        require(get('/ready').get('database_schema') == 1 and get('/health')['status'] == 'ok', 'PACKAGED_READINESS_FAILED')
        require(get('/catalog/v1/packages')['total'] == 0, 'OWNED_CATALOG_NOT_EMPTY')
        result['checks'].append('actual production CLI, module origin, owned HTTP, readiness and catalog')
        backup = run_cli(extracted, env, 'backup', '--output-dir', work/'backup')
        run_cli(extracted, env, 'verify-backup', '--input-dir', work/'backup', '--expected-sha256', backup['sha256'])
        restored = run_cli(extracted, env, 'restore', '--input-dir', work/'backup', '--output', work/'restored.sqlite3', '--expected-sha256', backup['sha256'])
        require(restored['state'] == 'restored', 'PACKAGED_RESTORE_FAILED')
        result['checks'].append('actual packaged CLI backup, verification and restore to a new owned file')
        result['passed'] = True
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        result['cleanup_complete'] = child.poll() is not None
        result['owned_server_exit_code'] = child.returncode
        atomic_json(work/'result.json', result)
    return result


def sync_probe(extracted, work):
    env = environment()
    # Only the dedicated provider-probe settings are carried into the child.
    names = ('SYNC_OPERATIONS_PROBE', 'SYNC_OPERATIONS_PROBE_DATABASE_URL', 'SYNC_OPERATIONS_PROBE_S3_ENDPOINT')
    env.update({n: os.environ[n] for n in names if n in os.environ})
    code = ('from pathlib import Path; import sys; import sync_server.app; from tools.operations_probe import probe; '
            'assert Path(sync_server.app.__file__).resolve().is_relative_to(Path.cwd()); '
            'r=probe(Path(sys.argv[1])); sys.exit(0 if r["passed"] and r["cleanup_complete"] else 1)')
    child = subprocess.run([sys.executable, '-c', code, str(work)], cwd=extracted, env=env,
                           capture_output=True, timeout=600)
    require(child.returncode == 0 and (work/'result.json').is_file(), 'PACKAGED_PRODUCTION_PROBE_FAILED')
    result = json.loads((work/'result.json').read_text('utf-8'))
    require(result.get('passed') is True and result.get('cleanup_complete') is True, 'PACKAGED_PROVIDER_CLEANUP_FAILED')
    result['module_origin_verified'] = True
    return result


def build(root, output, kind, commit):
    root, output = Path(root).resolve(), Path(output).resolve()
    inputs = package(root, output, kind, commit)
    report = {'schema': 1, 'source_commit': commit, 'source_clean': True, 'version': inputs['version'],
              'passed': False, 'deployment_sha256': inputs['deployment_sha256'], 'verified_at': datetime.now(timezone.utc).isoformat()}
    try:
        extracted = output/'extracted'
        report['deployment_probe'] = (sync_probe if kind == 'sync' else community_probe)(extracted, output/'probe')
        require(report['deployment_probe'].get('passed') is True and report['deployment_probe'].get('cleanup_complete') is True
                and report['deployment_probe'].get('module_origin_verified') is True, 'PACKAGED_SERVICE_PROBE_FAILED')
        require(build_source(root, commit) == inputs['source_provenance'] and digest(output/inputs['deployment']) == inputs['deployment_sha256'], 'SERVICE_BUILD_INPUTS_CHANGED')
        report['passed'] = report['deployment_probe']['passed'] is True
        return report
    finally:
        atomic_json(output/'verification.json', report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', required=True, choices=PRODUCTS)
    parser.add_argument('--repository-dir', type=Path, default=Path.cwd())
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.repository_dir, args.output, args.kind, args.commit)
        print(json.dumps(result, indent=2))
        return 0 if result['passed'] else 2
    except ReleaseError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print('SERVICE_DEPLOYMENT_FAILED_'+type(error).__name__, file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
