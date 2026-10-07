"""Marker-owned signed HTTPS catalog for native application acceptance."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import socket
import sys
import threading
import zipfile

repo = Path(__file__).resolve().parents[2]
service = Path.cwd().resolve()
if service not in {(repo.parent/'Community-for-OpenNexus').resolve(), (repo/'.build/community-server').resolve()}:
    raise RuntimeError('Run only from the fixed Community fixture checkout')
sys.path.insert(0,str(service))

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from community.app import Registry, create_app
from community.package import Release, signed_payload
if not Path(sys.modules['community.app'].__file__).resolve().is_relative_to(service):
    raise RuntimeError('Community fixture loaded a different service module')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_community_tls import authority, certificate


async def main():
    root = Path(sys.argv[1]).resolve(strict=True)
    if not root.is_dir() or not (root / '.opennexus-test').is_file() or (root / 'catalog.sqlite3').exists():
        raise RuntimeError('Fresh owned catalog required')
    registry = Registry(root / 'catalog.sqlite3')
    signer = Ed25519PrivateKey.generate()
    public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    registry.add_key('native-key', 'examples', public)
    contracts = Path(__file__).resolve().parents[2] / 'frontend/src/services/fixtures'
    configurations = json.loads((contracts / 'community-v1-configurations.json').read_text('utf-8'))['cases']
    source = (Path(__file__).parent / 'community_template_probe.py').read_text('utf-8')
    manifests = {
        'persona': {'name': '原生课程人设', 'system_prompt': 'NATIVE_PERSONA: 请依据真实课程输入回答。', 'dialogue_pairs': []},
        'template': {'markdown': '# 原生模板\r\n确认后独立运行。\r\n', 'executable': False, 'experiment': {
            'schema_version': 1, 'entry': '课程 #%.py', 'inputs': ['inputs/data.json', 'inputs/表格.csv'],
            'files': [{'path': '课程 #%.py', 'content': source},
                      {'path': 'inputs/data.json', 'content': '{"value":2}\r\n'},
                      {'path': 'inputs/表格.csv', 'content': 'name,value\r\n中文,3\r\n'}]}},
        'mcp': next(c['manifest'] for c in configurations if c['name'] == 'mcp-streamable-http'),
        'model': next(c['manifest'] for c in configurations if c['name'] == 'model-bekko-runtime'),
    }
    counter = 0
    def publish(kind, package, name, version='1.0.0', manifest=None, connection=None):
        nonlocal counter
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr(kind + '.json', json.dumps(manifest or manifests[kind], ensure_ascii=False))
        blob = output.getvalue()
        release = Release(namespace='examples', package_id=package, type=kind, version=version, name=name,
            author_id='native-fixture-author', license='MIT', description='Owned native HTTPS fixture · 中文 #%',
            sha256=hashlib.sha256(blob).hexdigest(), size=len(blob), platforms=['windows'], architectures=['x86_64'],
            min_app_version='0.6.0' if kind == 'template' else '0.2.0', permissions=[], changelog='Reviewed native fixture', published_at='2026-10-07T00:00:00Z',
            key_id='native-key', signature='')
        release.signature = base64.b64encode(signer.sign(signed_payload(release))).decode()
        identity = f'native-{counter:04}'
        counter += 1
        values=(identity,release.namespace,release.package_id,version,release.model_dump_json(),blob,release.author_id,'published')
        if connection is not None:
            connection.execute('INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?)',values)
        else:
            with registry.connect() as conn:
                conn.execute('INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?)',values)
        return identity
    with registry.connect() as conn:
        releases = {kind: publish(kind, 'native-' + kind, '原生 ' + kind,connection=conn) for kind in manifests}
        for index in range(135):
            publish('persona', f'page-{index:04}', f'分页课程 {index:03}',connection=conn)
    print('OWNED_CATALOG_SEEDED 139',file=sys.stderr,flush=True)
    ca = authority('Owned native catalog CA')
    ca_path = root / 'ca.pem'
    ca_path.write_bytes(ca[1].public_bytes(serialization.Encoding.PEM))
    cert_path, key_path = certificate(root, 'native-source', ca)
    print('OWNED_CATALOG_CERT_READY',file=sys.stderr,flush=True)
    leaf = x509.load_pem_x509_certificate(cert_path.read_bytes())
    spki = base64.b64encode(hashlib.sha256(leaf.public_key().public_bytes(
        serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).digest()).decode()
    app = create_app(registry, 'native-catalog-fixture', ('http://tauri.localhost','https://tauri.localhost','tauri://localhost'))
    sock = socket.socket(); sock.bind(('127.0.0.1',0)); sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app,log_config=None,access_log=False,timeout_graceful_shutdown=1,
        ssl_certfile=str(cert_path),ssl_keyfile=str(key_path)))
    def control():
        try:
            for line in sys.stdin:
                request=json.loads(line)
                if request['action']=='publish-update':
                    result=publish('persona','native-persona','原生 persona','1.1.0',
                        {**manifests['persona'],'system_prompt':'NATIVE_PERSONA_UPDATED: 核对更新后的实际输入。'})
                elif request['action']=='withdraw':
                    with registry.connect() as conn:
                        conn.execute("UPDATE submissions SET state='withdrawn' WHERE id=?",(releases[request['kind']],))
                    result=True
                elif request['action']=='revoke':
                    with registry.connect() as conn:
                        conn.execute("UPDATE keys SET revoked=1 WHERE id='native-key'")
                    result=True
                else: raise RuntimeError('Unknown owned fixture control')
                print(json.dumps({'action':request['action'],'result':result}),flush=True)
        finally: server.should_exit=True
    threading.Thread(target=control,daemon=True).start()
    task=asyncio.create_task(server.serve(sockets=[sock]))
    print('OWNED_CATALOG_SERVER_TASK',file=sys.stderr,flush=True)
    while not server.started:
        if task.done(): await task; raise RuntimeError('Native catalog did not start')
        await asyncio.sleep(.01)
    print(json.dumps({'url':f'https://127.0.0.1:{sock.getsockname()[1]}/','ca_path':str(ca_path),
        'leaf_spki_sha256':spki,'releases':releases,'catalog_items':139,
        'module_origin_verified':True}),flush=True)
    try: await task
    finally: sock.close()


if __name__ == '__main__':
    asyncio.run(main())
