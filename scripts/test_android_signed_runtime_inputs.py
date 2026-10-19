import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from android_signed_runtime_inputs import GitHubReadOnly, fetch, manifest, pointer, strict_json
from release_plan import ReleaseError


def fixture():
    payloads, assets = {}, []
    value = dict(schema=1, kind='android-signed-runtime-inputs', repository='KiriAky107/OpenNexus',
        draft_id=123, nonce='a'*32, application_id='cc.kronecker.opennexus', certificate_sha256='b'*64)
    for index, role in enumerate(('predecessor', 'successor')):
        item = dict(source_commit=('c' if index == 0 else 'd')*40,
                    version='0.1.0-dev1' if index == 0 else '0.1.0-alpha1', version_code=index+1, abi='x86_64')
        for offset, kind in enumerate(('apk', 'signature', 'input')):
            name = role + ('.apk' if kind == 'apk' else '-' + kind + '.json')
            raw = ('synthetic transport payload only; not an actual APK: ' + name).encode()
            record = dict(id=10+index*3+offset, name=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            item[kind] = record
            assets.append(dict(id=record['id'], name=name, size=len(raw), digest='sha256:'+record['sha256'], state='uploaded', download_count=0))
            payloads[record['id']] = raw
        value[role] = item
    raw = json.dumps(value).encode()
    assets.append(dict(id=99, name='runtime-inputs.json', size=len(raw), digest='sha256:'+hashlib.sha256(raw).hexdigest(), state='uploaded', download_count=0))
    payloads[99] = raw
    release = dict(id=123, draft=True, prerelease=False, published_at=None,
                   tag_name='android-runtime-inputs-'+'a'*32, assets=assets)
    return value, release, payloads, '123:99:'+hashlib.sha256(raw).hexdigest()


class FakeReadOnly:
    def __init__(self, release, payloads):
        self.release, self.payloads, self.reads = copy.deepcopy(release), payloads, []
        self.after_stream = None

    def json(self, path):
        self.reads.append(path)
        if path == '/releases/123':
            return copy.deepcopy(self.release)
        index = int(path.rsplit('/', 1)[1])
        return copy.deepcopy(next(a for a in self.release['assets'] if a['id'] == index))

    def stream(self, index):
        next(a for a in self.release['assets'] if a['id'] == index)['download_count'] += 1
        if self.after_stream:
            self.after_stream(index)
        return io.BytesIO(self.payloads[index])


class SignedRuntimeInputs(unittest.TestCase):
    def test_closed_real_upgrade_manifest_rejects_same_source_code_wrong_abi_and_private_fields(self):
        value, _, _, _ = fixture()
        manifest(value, 123)
        for change in ('same-source', 'same-code', 'wrong-abi', 'private-field', 'duplicate-id', 'unsafe-name', 'boolean-size'):
            data = copy.deepcopy(value)
            if change == 'same-source': data['successor']['source_commit'] = data['predecessor']['source_commit']
            if change == 'same-code': data['successor']['version_code'] = 1
            if change == 'wrong-abi': data['successor']['abi'] = 'arm64-v8a'
            if change == 'private-field': data['keystore'] = 'never uploaded'
            if change == 'duplicate-id': data['successor']['signature']['id'] = data['predecessor']['signature']['id']
            if change == 'unsafe-name': data['successor']['input']['name'] = '../credential.json'
            if change == 'boolean-size': data['predecessor']['apk']['bytes'] = True
            with self.subTest(change=change), self.assertRaises(ReleaseError): manifest(data, 123)

    def test_pointer_and_json_duplicate_reject_without_remote_calls(self):
        for text in ('main', '0:1:'+'a'*64, '1:2:'+'A'*64, '1:2:'+'a'*64+' ', '1:2:'+'a'*64+':3'):
            with self.subTest(text=text), self.assertRaises(ReleaseError): pointer(text)
        with self.assertRaises(ReleaseError): strict_json(b'{"draft_id":123,"draft_id":456}')

    def test_transport_saves_exact_bytes_but_never_claims_signature_or_runtime(self):
        _, release, payloads, text = fixture()
        api = FakeReadOnly(release, payloads)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)/'new'
            result = fetch(text, out, api)
            self.assertTrue(result['downloaded_original_bytes_verified'])
            self.assertFalse(result['signature_or_source_payload_or_runtime_or_upgrade_or_physical_verified'])
            self.assertFalse(result['published'])
            self.assertEqual(len(result['files']), 7)
            for item in release['assets']: self.assertEqual((out/item['name']).read_bytes(), payloads[item['id']])
            self.assertEqual(api.reads.count('/releases/123'), 2)
            before = list(api.reads)
            with self.assertRaisesRegex(ReleaseError, 'EXISTS_RECONCILE_ONLY'): fetch(text, out, api)
            self.assertEqual(api.reads, before)

    def test_wrong_draft_published_or_extra_assets_fail_before_output_or_transfer(self):
        for change in ('wrong-id', 'published', 'extra-asset', 'manifest-id', 'different-tag'):
            _, release, payloads, text = fixture()
            if change == 'wrong-id': release['id'] = 456
            if change == 'published': release.update(draft=False, published_at='2026-01-01T00:00:00Z')
            if change == 'extra-asset': release['assets'].append(dict(id=999, name='private.p12'))
            if change == 'manifest-id': text = text.replace(':99:', ':98:')
            if change == 'different-tag': release['tag_name'] = 'android-v0.1.0-alpha1'
            api = FakeReadOnly(release, payloads)
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                out = Path(folder)/'new'
                with self.assertRaises(ReleaseError): fetch(text, out, api)
                self.assertFalse(out.exists())

    def test_partial_hash_failure_is_retained_and_retry_never_overwrites(self):
        value, release, payloads, text = fixture()
        payloads[value['predecessor']['apk']['id']] = b'wrong'
        api = FakeReadOnly(release, payloads)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)/'new'
            with self.assertRaisesRegex(ReleaseError, 'ASSET_BYTES_CHANGED'): fetch(text, out, api)
            self.assertEqual((out/'predecessor.apk.partial').read_bytes(), b'wrong')
            self.assertTrue((out/'download-intent.json').is_file())
            self.assertFalse((out/'download-proof.json').exists())
            with self.assertRaisesRegex(ReleaseError, 'RECONCILE_ONLY'): fetch(text, out, api)
            self.assertEqual((out/'predecessor.apk.partial').read_bytes(), b'wrong')

    def test_mid_transfer_publication_or_asset_change_retains_originals_without_success(self):
        for change in ('publish', 'asset'):
            _, release, payloads, text = fixture()
            api = FakeReadOnly(release, payloads)
            def mutate(index):
                if index == 15:
                    if change == 'publish': api.release['draft'] = False
                    else: api.release['assets'][0]['digest'] = 'sha256:'+'e'*64
            api.after_stream = mutate
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                out = Path(folder)/'new'
                with self.assertRaisesRegex(ReleaseError, 'DRAFT_CHANGED'): fetch(text, out, api)
                self.assertTrue((out/'successor.apk').is_file())
                self.assertFalse((out/'download-proof.json').exists())

    def test_authorization_never_crosses_official_asset_redirect(self):
        api = GitHubReadOnly('synthetic-token')
        calls = []
        def request(req, timeout):
            calls.append(req)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 302, 'Found', {'Location':'https://release-assets.githubusercontent.com/owned?nonce=1'}, io.BytesIO())
            return io.BytesIO(b'synthetic')
        with patch.object(api.direct, 'open', side_effect=request):
            self.assertEqual(api.stream(11).read(), b'synthetic')
        self.assertEqual(calls[0].get_header('Authorization'), 'Bearer synthetic-token')
        self.assertIsNone(calls[1].get_header('Authorization'))
        self.assertTrue(all(req.get_method() == 'GET' for req in calls))
        with patch.object(api.direct, 'open', side_effect=urllib.error.HTTPError('unused', 302, 'Found', {'Location':'https://example.com/unowned'}, io.BytesIO())):
            with self.assertRaisesRegex(ReleaseError, 'REDIRECT'): api.stream(11)


if __name__ == '__main__': unittest.main()
