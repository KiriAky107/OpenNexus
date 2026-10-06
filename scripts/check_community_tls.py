"""Exercise the production Community client against owned HTTPS endpoints."""

from __future__ import annotations

import hashlib
import http.server
import ipaddress
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def authority(label: str):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def certificate(root: Path, name: str, ca, *, mismatch=False, expired=False):
    ca_key, ca_cert = ca
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    names = [x509.DNSName('unrelated.invalid')] if mismatch else [
        x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
    ]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca_cert.subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, None, None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = root / (name + '.pem'), root / (name + '.key')
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


class Endpoint:
    def __init__(self, cert_path: Path, key_path: Path):
        self.requests: list[str] = []
        endpoint = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                endpoint.requests.append(self.path)
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', endpoint.url + 'followed')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                body = json.dumps({'schema_version': 1, 'source_id': 'owned-tls-fixture', 'keys': []}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(cert_path, key_path)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        except BaseException:
            self.server.server_close()
            raise
        self.url = f'https://127.0.0.1:{self.server.server_port}/'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('Owned HTTPS endpoint did not stop')


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    build = repo / '.build'
    build.mkdir(exist_ok=True)
    proof = {'passed': False, 'system_certificate_store_modified': False, 'process_scoped_roots': True}
    endpoints = {}
    with tempfile.TemporaryDirectory(prefix='community-tls-', dir=build) as temporary:
        root = Path(temporary)
        trusted = authority('owned trusted root')
        cert_file = root / 'trusted-ca.pem'
        cert_file.write_bytes(trusted[1].public_bytes(serialization.Encoding.PEM))
        proof['root_sha256'] = hashlib.sha256(cert_file.read_bytes()).hexdigest()
        try:
            for name, ca, options in [
                ('trusted', trusted, {}), ('untrusted', authority('untrusted root'), {}),
                ('mismatch', trusted, {'mismatch': True}), ('expired', trusted, {'expired': True}),
            ]:
                endpoints[name] = Endpoint(*certificate(root, name, ca, **options))
            environment = dict(os.environ)
            environment.pop('SSL_CERT_DIR', None)
            environment['SSL_CERT_FILE'] = str(cert_file)
            environment['NO_PROXY'] = environment['no_proxy'] = 'localhost,127.0.0.1,::1'
            environment['OPENNEXUS_COMMUNITY_TLS_ENDPOINTS'] = json.dumps({
                name: endpoint.url for name, endpoint in endpoints.items()
            })
            result = subprocess.run([
                'cargo', 'test', '--lib', '--features', 'desktop', '--locked',
                'extension_trust::fixture_tests::actual_https_roots_require_trust_hostname_and_validity',
                '--', '--ignored', '--exact', '--nocapture',
            ], cwd=repo / 'frontend/src-tauri', env=environment, check=False)
            proof['cargo_exit'] = result.returncode
            proof['http_requests'] = {name: endpoint.requests for name, endpoint in endpoints.items()}
            proof['passed'] = result.returncode == 0 and proof['http_requests'] == {
                'trusted': ['/catalog/v1/sources', '/redirect'],
                'untrusted': [], 'mismatch': [], 'expired': [],
            }
        finally:
            for endpoint in endpoints.values():
                endpoint.close()
            proof['owned_servers_stopped'] = True
    proof['fixture_files_removed'] = not root.exists()
    print('COMMUNITY_HTTPS_PROOF ' + json.dumps(proof), flush=True)
    return 0 if proof['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
