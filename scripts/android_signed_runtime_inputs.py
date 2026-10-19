"""Fetch already signed APK inputs from one owned unpublished GitHub draft.

This tool has no write API or signing key access. Its receipt proves downloaded
bytes only; an emulator job must independently verify and execute those binaries.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from release_plan import COMMIT, HEX, VERSION, atomic_json, require

REPOSITORY = 'KiriAky107/OpenNexus'
PACKAGE = 'cc.kronecker.opennexus'
NONCE = re.compile(r'[0-9a-f]{32}')
LIMITS = {'apk': 160 * 1024**2, 'signature': 16 * 1024**2, 'input': 32 * 1024**2}
MANIFEST_LIMIT = 16384


def exact(value, keys, code):
    require(type(value) is dict and set(value) == set(keys), code)


def positive(value):
    return type(value) is int and 0 < value < 2**63


def pointer(text):
    require(type(text) is str and re.fullmatch(r'[1-9][0-9]{0,18}:[1-9][0-9]{0,18}:[0-9a-f]{64}', text),
            'ANDROID_RUNTIME_INPUT_POINTER')
    draft, asset, digest = text.split(':')
    require(positive(int(draft)) and positive(int(asset)), 'ANDROID_RUNTIME_INPUT_POINTER')
    return int(draft), int(asset), digest


def asset_record(value, name, limit):
    exact(value, ('id', 'name', 'bytes', 'sha256'), 'ANDROID_RUNTIME_ASSET_FIELDS')
    require(positive(value['id']) and value['name'] == name
            and positive(value['bytes']) and value['bytes'] <= limit
            and type(value['sha256']) is str and HEX.fullmatch(value['sha256']),
            'ANDROID_RUNTIME_ASSET_BINDING')


def manifest(value, draft_id):
    exact(value, ('schema', 'kind', 'repository', 'draft_id', 'nonce', 'application_id',
                  'certificate_sha256', 'predecessor', 'successor'), 'ANDROID_RUNTIME_MANIFEST_FIELDS')
    require(type(value['schema']) is int and value['schema'] == 1
            and value['kind'] == 'android-signed-runtime-inputs'
            and value['repository'] == REPOSITORY and type(value['draft_id']) is int
            and value['draft_id'] == draft_id and type(value['nonce']) is str
            and NONCE.fullmatch(value['nonce']) and value['application_id'] == PACKAGE
            and type(value['certificate_sha256']) is str and HEX.fullmatch(value['certificate_sha256']),
            'ANDROID_RUNTIME_DRAFT_IDENTITY')
    ids, hashes = [], []
    for role in ('predecessor', 'successor'):
        item = value[role]
        exact(item, ('source_commit', 'version', 'version_code', 'abi', 'apk', 'signature', 'input'),
              'ANDROID_RUNTIME_PAIR_FIELDS')
        require(type(item['source_commit']) is str and COMMIT.fullmatch(item['source_commit'])
                and type(item['version']) is str and VERSION.fullmatch(item['version'])
                and positive(item['version_code']) and item['abi'] == 'x86_64',
                'ANDROID_RUNTIME_PAIR_SOURCE')
        for kind in LIMITS:
            name = role + ('.apk' if kind == 'apk' else '-' + kind + '.json')
            asset_record(item[kind], name, LIMITS[kind])
            ids.append(item[kind]['id'])
        hashes.append(item['apk']['sha256'])
    old, new = value['predecessor'], value['successor']
    require(old['source_commit'] != new['source_commit'] and old['version_code'] < new['version_code']
            and hashes[0] != hashes[1] and len(set(ids)) == 6, 'ANDROID_RUNTIME_REAL_UPGRADE_REQUIRED')
    return value


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'ANDROID_RUNTIME_DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique)


def metadata_asset(actual, expected):
    require(type(actual) is dict and actual.get('id') == expected['id']
            and type(actual.get('id')) is int and actual.get('name') == expected['name']
            and type(actual.get('size')) is int and actual['size'] == expected['bytes']
            and actual.get('state') == 'uploaded'
            and actual.get('digest') in (None, 'sha256:' + expected['sha256']),
            'ANDROID_RUNTIME_REMOTE_ASSET_CHANGED')


def release_identity(value):
    require(type(value) is dict and type(value.get('assets')) is list
            and all(type(a) is dict and type(a.get('id')) is int for a in value['assets']),
            'ANDROID_RUNTIME_RELEASE_METADATA')
    return {**{k: value.get(k) for k in ('id', 'draft', 'prerelease', 'published_at', 'tag_name')},
            'assets': sorted([{k: a.get(k) for k in ('id', 'name', 'size', 'state', 'digest')}
                              for a in value['assets']], key=lambda a: a['id'])}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubReadOnly:
    def __init__(self, token):
        require(type(token) is str and bool(token), 'ANDROID_RUNTIME_DRAFT_TOKEN_REQUIRED')
        self.token = token
        self.direct = urllib.request.build_opener(NoRedirect())

    def _request(self, path, accept):
        require(re.fullmatch(r'/(?:releases/[1-9][0-9]*|releases/assets/[1-9][0-9]*)', path),
                'ANDROID_RUNTIME_API_PATH')
        return urllib.request.Request('https://api.github.com/repos/' + REPOSITORY + path,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': accept,
                     'User-Agent': 'OpenNexus-signed-runtime-inputs', 'X-GitHub-Api-Version': '2022-11-28'}, method='GET')

    def json(self, path):
        with self.direct.open(self._request(path, 'application/vnd.github+json'), timeout=90) as response:
            raw = response.read(1024**2 + 1)
        require(len(raw) <= 1024**2, 'ANDROID_RUNTIME_REMOTE_METADATA_LIMIT')
        return strict_json(raw)

    def stream(self, asset_id):
        try:
            return self.direct.open(self._request('/releases/assets/' + str(asset_id), 'application/octet-stream'), timeout=90)
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                raise
            location = error.headers.get('Location', '')
            error.close()
            parsed = urllib.parse.urlsplit(location)
            require(parsed.scheme == 'https' and parsed.hostname
                    and parsed.hostname.endswith(('.githubusercontent.com', '.blob.core.windows.net'))
                    and not parsed.username and not parsed.password and not parsed.fragment,
                    'ANDROID_RUNTIME_ASSET_REDIRECT')
            # A new request has no Authorization header. Reject further redirects.
            return self.direct.open(urllib.request.Request(location, method='GET'), timeout=90)


def download(api, expected, output):
    metadata_asset(api.json('/releases/assets/' + str(expected['id'])), expected)
    partial = output.with_name(output.name + '.partial')
    require(not partial.exists() and not output.exists(), 'ANDROID_RUNTIME_INPUT_EXISTS_RECONCILE_ONLY')
    count, digest = 0, hashlib.sha256()
    with api.stream(expected['id']) as stream, partial.open('xb') as destination:
        while block := stream.read(min(1024**2, expected['bytes'] - count + 1)):
            count += len(block)
            require(count <= expected['bytes'], 'ANDROID_RUNTIME_ASSET_LENGTH')
            digest.update(block)
            destination.write(block)
        destination.flush()
        os.fsync(destination.fileno())
    require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'],
            'ANDROID_RUNTIME_ASSET_BYTES_CHANGED')
    partial.replace(output)


def fetch(text, output, api):
    draft_id, manifest_id, expected_hash = pointer(text)
    require(not output.exists(), 'ANDROID_RUNTIME_OUTPUT_EXISTS_RECONCILE_ONLY')
    release = api.json('/releases/' + str(draft_id))
    require(type(release) is dict and type(release.get('id')) is int and release['id'] == draft_id
            and release.get('draft') is True and release.get('prerelease') is False
            and release.get('published_at') is None and type(release.get('tag_name')) is str
            and re.fullmatch('android-runtime-inputs-[0-9a-f]{32}', release['tag_name']),
            'ANDROID_RUNTIME_UNPUBLISHED_OWNED_DRAFT_REQUIRED')
    assets = release.get('assets')
    require(type(assets) is list and len(assets) == 7, 'ANDROID_RUNTIME_CLOSED_DRAFT_ASSETS_REQUIRED')
    candidates = [v for v in assets if type(v) is dict and v.get('id') == manifest_id]
    require(len(candidates) == 1, 'ANDROID_RUNTIME_ORIGINAL_MANIFEST_ID_REQUIRED')
    item = candidates[0]
    expected = dict(id=manifest_id, name='runtime-inputs.json', bytes=item.get('size'), sha256=expected_hash)
    asset_record(expected, 'runtime-inputs.json', MANIFEST_LIMIT)
    metadata_asset(item, expected)
    output.mkdir(parents=True)
    atomic_json(output / 'download-intent.json', dict(schema=1, repository=REPOSITORY, pointer=text,
        draft_id=draft_id, manifest_asset_id=manifest_id, automatic_retry=False,
        signing_or_API_write_or_emulator_execution=False))
    download(api, expected, output / 'runtime-inputs.json')
    raw = (output / 'runtime-inputs.json').read_bytes()
    value = manifest(strict_json(raw), draft_id)
    require(release['tag_name'] == 'android-runtime-inputs-' + value['nonce'], 'ANDROID_RUNTIME_DRAFT_NONCE_CHANGED')
    records = [value[role][kind] for role in ('predecessor', 'successor') for kind in LIMITS]
    require({v.get('id') for v in assets} == {manifest_id, *(v['id'] for v in records)}
            and len({v.get('id') for v in assets}) == 7, 'ANDROID_RUNTIME_DRAFT_ASSETS_CHANGED')
    for record in records:
        matches = [a for a in assets if a.get('id') == record['id']]
        require(len(matches) == 1, 'ANDROID_RUNTIME_DRAFT_ASSET_MISSING')
        metadata_asset(matches[0], record)
        download(api, record, output / record['name'])
    # Recheck the draft after transfer. Never switch to a published or other draft.
    final = api.json('/releases/' + str(draft_id))
    require(release_identity(final) == release_identity(release), 'ANDROID_RUNTIME_DRAFT_CHANGED_DURING_TRANSFER')
    for record in [expected, *records]:
        file = output / record['name']
        require(file.is_file() and not file.is_symlink() and file.stat().st_size == record['bytes'],
                'ANDROID_RUNTIME_DOWNLOADED_INPUT_CHANGED')
        with file.open('rb') as stream:
            require(hashlib.file_digest(stream, 'sha256').hexdigest() == record['sha256'],
                    'ANDROID_RUNTIME_DOWNLOADED_INPUT_CHANGED')
    receipt = dict(schema=1, kind='android-signed-runtime-input-download', repository=REPOSITORY,
        draft_id=draft_id, manifest_asset_id=manifest_id, manifest_sha256=expected_hash,
        downloaded_original_bytes_verified=True, files={r['name']: r for r in [expected, *records]},
        signature_or_source_payload_or_runtime_or_upgrade_or_physical_verified=False, published=False)
    atomic_json(output / 'download-proof.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pointer', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = fetch(args.pointer, args.output, GitHubReadOnly(os.environ.get('GITHUB_TOKEN')))
    print(json.dumps({k: result[k] for k in ('kind', 'draft_id', 'downloaded_original_bytes_verified', 'published')}))


if __name__ == '__main__':
    main()
