"""Prepare-safe GitHub stage/replace/verify/recover with an immutable local plan."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote, urlsplit
import urllib.request

from release_plan import Journal, Plan, ReleaseError, atomic_json, check_local_source, digest, journal_lock, require, text_digest


class GitHub:
    def __init__(self, token, root):
        require(isinstance(token, str) and bool(token) and not any(c.isspace() for c in token), 'GITHUB_TOKEN_REQUIRED')
        self.token, self.root = token, Path(root)

    def request(self, method, path, body=None, file=None):
        parsed = urlsplit(path)
        host = parsed.hostname or 'api.github.com'
        require(host in {'api.github.com', 'uploads.github.com'} and (not parsed.scheme or parsed.scheme == 'https'), 'API_HOST_INVALID')
        target = parsed.path+('?' + parsed.query if parsed.query else '')
        headers = {'Authorization': 'Bearer '+self.token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'OpenNexus-verified-release'}
        connection = http.client.HTTPSConnection(host, timeout=120)
        try:
            if file is None:
                payload = json.dumps(body).encode() if body is not None else None
                if payload is not None:
                    headers['Content-Type'] = 'application/json'
                connection.request(method, target, payload, headers)
            else:
                headers.update({'Content-Type': 'application/octet-stream', 'Content-Length': str(file.stat().st_size)})
                connection.putrequest(method, target)
                for name, value in headers.items():
                    connection.putheader(name, value)
                connection.endheaders()
                with file.open('rb') as source:
                    while block := source.read(1024*1024):
                        connection.send(block)
            response = connection.getresponse()
            data = response.read()
            if method == 'GET' and response.status == 404:
                return None
            require(200 <= response.status < 300, 'GITHUB_HTTP_'+str(response.status))
            return json.loads(data) if data else None
        except (OSError, http.client.HTTPException):
            raise ReleaseError('REMOTE_OUTCOME_UNKNOWN_RESUME_SAME_PLAN') from None
        finally:
            connection.close()

    def download(self, repository, asset, destination):
        """Never forward the API token to a redirected storage host."""
        connection = http.client.HTTPSConnection('api.github.com', timeout=120)
        try:
            connection.request('GET', f'/repos/{repository}/releases/assets/{asset["id"]}', headers={
                'Authorization': 'Bearer '+self.token, 'Accept': 'application/octet-stream', 'User-Agent': 'OpenNexus-verified-release'})
            response = connection.getresponse()
            if response.status in {301, 302, 307}:
                location = response.getheader('Location')
                parsed = urlsplit(location)
                require(parsed.scheme == 'https' and parsed.hostname and (parsed.hostname == 'release-assets.githubusercontent.com' or parsed.hostname.endswith('.blob.core.windows.net')), 'DOWNLOAD_HOST_INVALID')
                response.read()
                stream = urllib.request.urlopen(location, timeout=120)
            else:
                require(response.status == 200 and 'application/json' not in response.getheader('Content-Type', ''), 'ASSET_DOWNLOAD_FAILED')
                stream = response
            try:
                count, checksum = 0, hashlib.sha256()
                with destination.open('xb') as output:
                    while block := stream.read(1024*1024):
                        count += len(block)
                        require(count <= asset['bytes'], 'BACKUP_DOWNLOAD_TOO_LARGE')
                        output.write(block)
                        checksum.update(block)
                    output.flush()
                    os.fsync(output.fileno())
                require(count == asset['bytes'] and checksum.hexdigest() == asset['sha256'], 'BACKUP_DOWNLOAD_MISMATCH')
            finally:
                stream.close()
        except (OSError, http.client.HTTPException):
            raise ReleaseError('BACKUP_DOWNLOAD_INTERRUPTED') from None
        finally:
            connection.close()

    def tag_move(self, repository, tag, commit, lease):
        valid = subprocess.run(['git', 'cat-file', '-e', commit+'^{commit}'], cwd=self.root, capture_output=True)
        require(valid.returncode == 0, 'FIXED_COMMIT_NOT_LOCAL')
        env = os.environ.copy()
        # Git trace destinations may be inherited from a maintainer's terminal.
        # Disable all trace families before adding an in-memory auth header.
        for name in list(env):
            if name.upper().startswith('GIT_TRACE') or name.upper() == 'GIT_CURL_VERBOSE':
                del env[name]
        count = int(env.get('GIT_CONFIG_COUNT', '0'))
        header = 'AUTHORIZATION: basic '+base64.b64encode(('x-access-token:'+self.token).encode()).decode()
        env.update(GIT_TERMINAL_PROMPT='0', GIT_TRACE='0', GIT_CURL_VERBOSE='0', GIT_TRACE_REDACT='1')
        env['GIT_CONFIG_COUNT'] = str(count+1)
        env['GIT_CONFIG_KEY_'+str(count)] = 'http.https://github.com/.extraheader'
        env['GIT_CONFIG_VALUE_'+str(count)] = header
        response = subprocess.run(['git', 'push', '--porcelain', '--force-with-lease=refs/tags/'+tag+':'+(lease or ''),
                                   'https://github.com/'+repository+'.git', commit+':refs/tags/'+tag],
                                  cwd=self.root, env=env, capture_output=True, timeout=120)
        require(response.returncode == 0, 'TAG_LEASE_REJECTED_OR_OUTCOME_UNKNOWN')


class Publisher:
    def __init__(self, plan, api, clock=lambda: datetime.now(timezone.utc)):
        self.plan, self.api, self.clock = plan, api, clock
        self.p, self.journal = plan.data, Journal(plan)
        self.prefix = '/repos/'+self.p['repository']
        self.marker = plan.fingerprint[:16]

    def tag(self):
        ref = self.api.request('GET', self.prefix+'/git/ref/tags/'+quote(self.p['tag'], safe=''))
        if ref is None:
            return None
        obj, raw = ref['object'], ref['object']['sha']
        for _ in range(8):
            if obj['type'] == 'commit':
                return {'ref_sha': raw, 'commit': obj['sha']}
            require(obj['type'] == 'tag', 'TAG_OBJECT_INVALID')
            obj = self.api.request('GET', self.prefix+'/git/tags/'+obj['sha'])['object']
        raise ReleaseError('TAG_CHAIN_TOO_LONG')

    def lease(self, *, promoted=False):
        actual, original = self.tag(), self.p.get('original')
        old = original['tag_ref_sha'] if original else None
        if (actual['ref_sha'] if actual else None) == old:
            require(not original or actual['commit'] == original['tag_commit'], 'ORIGINAL_TAG_COMMIT_CHANGED')
            return old
        own = self.journal.events('tag-intent')
        require(promoted and actual and actual['ref_sha'] == self.p['commit'] and actual['commit'] == self.p['commit'] and own and own[-1]['lease'] == old, 'REMOTE_TAG_CHANGED')
        return old

    def release(self):
        original = self.p.get('original')
        known = self.journal.events('created')
        if not original and known:
            # Tag lookup only returns published releases. A durable draft ID
            # must remain authoritative across interrupted asset uploads.
            release = self.api.request('GET', self.prefix+'/releases/'+str(known[-1]['id']))
            require(release is not None, 'OWNED_RELEASE_DISAPPEARED')
        else:
            release = self.api.request('GET', self.prefix+'/releases/tags/'+quote(self.p['tag'], safe=''))
            if release is None and not original:
                # A create response may have been lost before its ID was saved.
                # Authenticated listings include drafts; never create a second
                # release while a same-tag draft has an uncertain owner.
                candidates, page = [], 1
                while True:
                    items = self.api.request('GET', self.prefix+f'/releases?per_page=100&page={page}')
                    candidates.extend(item for item in items if item['tag_name'] == self.p['tag'])
                    if len(items) < 100:
                        break
                    page += 1
                require(len(candidates) <= 1, 'AMBIGUOUS_RELEASES_FOR_TAG')
                release = candidates[0] if candidates else None
        if original:
            require(release and release['id'] == original['release_id'] and release['tag_name'] == self.p['tag'], 'ORIGINAL_RELEASE_CHANGED')
        elif release:
            created = self.journal.events('create-intent')
            require(created and release['tag_name'] == self.p['tag'] and release['target_commitish'] == self.p['commit'], 'RELEASE_ALREADY_EXISTS')
            if known:
                require(release['id'] == known[-1]['id'], 'RELEASE_ID_CHANGED')
                require(text_digest(release['body']) == self.p['notes_sha256'] and release['prerelease'] == self.plan.prerelease, 'OWNED_RELEASE_NOTES_CHANGED')
            else:
                require(release['draft'] and release['prerelease'] == self.plan.prerelease and text_digest(release['body']) == self.p['notes_sha256'], 'UNOWNED_DRAFT_RELEASE')
                require(datetime.fromisoformat(release['created_at'].replace('Z', '+00:00')).timestamp() >= datetime.fromisoformat(created[0]['at']).timestamp()-5, 'DRAFT_PREDATES_INTENT')
        return release

    def assets(self, release):
        result, page = [], 1
        while True:
            items = self.api.request('GET', self.prefix+f'/releases/{release["id"]}/assets?per_page=100&page={page}')
            result.extend(items)
            if len(items) < 100:
                break
            page += 1
        require(len({a['name'] for a in result}) == len(result), 'DUPLICATE_REMOTE_ASSET')
        return {a['name']: a for a in result}

    @staticmethod
    def matches(remote, local):
        return remote.get('state') == 'uploaded' and remote.get('size') == local['bytes'] and remote.get('digest') == 'sha256:'+local['sha256']

    def pending(self, asset):
        return 'pending-'+self.marker+'-'+asset['name']

    def preserved(self, asset):
        return 'original-'+self.marker+'-'+asset['name']

    def check_original(self, release, current, *, progressed=False):
        allowed = {self.pending(asset) for asset in self.p['assets']}
        original = self.p.get('original')
        if original:
            allowed.update(old['name'] for old in original['assets'])
            allowed.update(self.preserved(old) for old in original['assets'])
        allowed.update(event['name'] for event in self.journal.events('promote-intent'))
        require(set(current) <= allowed, 'UNEXPECTED_REMOTE_ASSET')
        if not original:
            return
        allowed = {original['body_sha256']}
        if progressed and self.journal.events('publish-intent'):
            allowed.add(self.p['notes_sha256'])
        require(text_digest(release['body']) in allowed and not release['draft'], 'ORIGINAL_NOTES_CHANGED')
        by_id = {asset['id']: asset for asset in current.values()}
        for old in original['assets']:
            actual = by_id.get(old['id'])
            removed = any(event.get('id') == old['id'] for event in self.journal.events('delete-intent'))
            require(actual is not None or (progressed and removed), 'ORIGINAL_ASSET_DISAPPEARED')
            if actual:
                names = {old['name']}
                if progressed and any(event.get('id') == old['id'] for event in self.journal.events('preserve-intent')):
                    names.add(self.preserved(old))
                require(actual['name'] in names and self.matches(actual, old), 'ORIGINAL_ASSET_CHANGED')

    def precheck(self):
        self.lease(promoted=True)
        release = self.release()
        if release:
            self.check_original(release, self.assets(release), progressed=True)
        return {'repository': self.p['repository'], 'tag': self.p['tag'], 'commit': self.p['commit'],
                'release_id': release['id'] if release else None, 'plan_sha256': self.plan.fingerprint, 'remote_modified': False}

    def backup(self, release):
        original = self.p.get('original')
        if not original:
            return
        directory = self.plan.base/('original-'+str(release['id'])+'-'+self.marker)
        directory.mkdir(exist_ok=True)
        require(not directory.is_symlink(), 'ORIGINAL_BACKUP_LINK')
        metadata = directory/'release.json'
        if not metadata.exists():
            atomic_json(metadata, {'schema': 1, 'plan_sha256': self.plan.fingerprint, 'release': release, 'tag': self.tag()})
        saved = json.loads(metadata.read_text('utf-8'))
        require(saved['plan_sha256'] == self.plan.fingerprint and saved['release']['id'] == original['release_id'] and text_digest(saved['release']['body']) == original['body_sha256'], 'ORIGINAL_BACKUP_CHANGED')
        for old in original['assets']:
            target = directory/old['name']
            if not target.exists():
                temporary = directory/(old['name']+'.incomplete')
                # An interrupted download is local and never becomes a verified backup.
                if temporary.exists():
                    require(temporary.is_file() and not temporary.is_symlink(), 'BACKUP_TEMP_LINK')
                    temporary.unlink()
                self.api.download(self.p['repository'], old, temporary)
                require(digest(temporary) == old['sha256'], 'BACKUP_BYTES_CHANGED')
                temporary.replace(target)
            require(target.is_file() and not target.is_symlink() and target.stat().st_size == old['bytes'] and digest(target) == old['sha256'], 'ORIGINAL_BACKUP_CHANGED')
        self.journal.record('original-backed-up', release_id=release['id'], directory=directory.name)

    def stage(self):
        self.lease(promoted=True)
        release = self.release()
        if release is None:
            self.journal.record('create-intent', commit=self.p['commit'])
            release = self.api.request('POST', self.prefix+'/releases', {'tag_name': self.p['tag'], 'target_commitish': self.p['commit'],
                'name': self.p['product']+' '+self.p['version'], 'body': self.plan.notes, 'draft': True, 'prerelease': self.plan.prerelease})
            self.journal.record('created', id=release['id'])
        elif not self.p.get('original') and not self.journal.events('created'):
            # A create response was lost; release() has checked the original intent.
            self.journal.record('created', id=release['id'], recovered=True)
        current = self.assets(release)
        self.check_original(release, current, progressed=True)
        self.backup(release)
        for asset in self.p['assets']:
            current = self.assets(release)
            candidate = current.get(self.pending(asset))
            canonical = current.get(asset['name'])
            if canonical and self.matches(canonical, asset) and any(event.get('id') == canonical['id'] for event in self.journal.events('promote-intent')):
                continue
            intents = [event for event in self.journal.events('upload-intent') if event['name'] == self.pending(asset)]
            if candidate:
                require(intents, 'UNOWNED_PENDING_ASSET')
                require(datetime.fromisoformat(candidate['created_at'].replace('Z', '+00:00')).timestamp() >= datetime.fromisoformat(intents[0]['at']).timestamp()-5, 'PENDING_ASSET_PREDATES_INTENT')
                if not self.matches(candidate, asset):
                    require(candidate['state'] != 'uploaded' and (self.clock()-datetime.fromisoformat(candidate['created_at'].replace('Z', '+00:00'))).total_seconds() >= 300, 'PENDING_OUTCOME_UNCERTAIN_OR_WRONG_BYTES')
                    self.journal.record('unfinished-delete-intent', id=candidate['id'], name=candidate['name'])
                    self.api.request('DELETE', self.prefix+'/releases/assets/'+str(candidate['id']))
                    self.journal.record('unfinished-deleted', id=candidate['id'])
                    candidate = None
            if candidate is None:
                file = self.plan.base/asset['file']
                require(file.stat().st_size == asset['bytes'] and digest(file) == asset['sha256'], 'ASSET_BYTES_CHANGED')
                self.journal.record('upload-intent', name=self.pending(asset), sha256=asset['sha256'])
                candidate = self.api.request('POST', release['upload_url'].split('{')[0]+'?name='+quote(self.pending(asset), safe=''), file=file)
            require(self.matches(candidate, asset), 'STAGED_ASSET_MISMATCH')
            self.journal.record('staged', id=candidate['id'], name=asset['name'])
        return {'release_id': release['id'], 'staged_assets': len(self.p['assets']), 'commit': self.p['commit']}

    def publish(self, *, replace=False):
        require(bool(self.p.get('original')) == replace, 'EXPLICIT_REPLACEMENT_REQUIRED' if self.p.get('original') else 'NEW_RELEASE_REQUIRES_PUBLISH')
        release = self.release()
        require(release is not None, 'STAGE_REQUIRED')
        current = self.assets(release)
        self.check_original(release, current, progressed=True)
        if replace:
            self.backup(release)
        for asset in self.p['assets']:
            staged = current.get(self.pending(asset))
            canonical = current.get(asset['name'])
            own = canonical and any(event.get('id') == canonical['id'] for event in self.journal.events('promote-intent'))
            require((staged and self.matches(staged, asset)) or (own and self.matches(canonical, asset)), 'ALL_ASSETS_MUST_BE_STAGED')
        lease = self.lease(promoted=True)
        actual = self.tag()
        if not actual or actual['ref_sha'] != self.p['commit']:
            self.journal.record('tag-intent', lease=lease, commit=self.p['commit'])
            self.api.tag_move(self.p['repository'], self.p['tag'], self.p['commit'], lease)
        require(self.tag() == {'ref_sha': self.p['commit'], 'commit': self.p['commit']}, 'TAG_PROMOTION_MISMATCH')
        self.journal.record('tag-promoted', commit=self.p['commit'])
        original_ids = {old['id']: old for old in (self.p.get('original') or {}).get('assets', [])}
        new_names = {asset['name'] for asset in self.p['assets']}
        for old in original_ids.values():
            current_by_id = {asset['id']: asset for asset in self.assets(release).values()}
            actual = current_by_id.get(old['id'])
            if old['name'] not in new_names and actual and actual['name'] == old['name']:
                require(self.matches(actual, old), 'ORIGINAL_ASSET_CHANGED')
                self.journal.record('preserve-intent', id=old['id'], name=self.preserved(old))
                result = self.api.request('PATCH', self.prefix+'/releases/assets/'+str(old['id']), {'name': self.preserved(old)})
                require(result['id'] == old['id'] and result['name'] == self.preserved(old) and self.matches(result, old), 'ORIGINAL_RENAME_MISMATCH')
        for asset in self.p['assets']:
            current = self.assets(release)
            canonical = current.get(asset['name'])
            if canonical and self.matches(canonical, asset) and any(event.get('id') == canonical['id'] for event in self.journal.events('promote-intent')):
                continue
            if canonical:
                old = original_ids.get(canonical['id'])
                require(old and self.matches(canonical, old), 'UNEXPECTED_CANONICAL_ASSET')
                self.journal.record('preserve-intent', id=canonical['id'], name=self.preserved(old))
                renamed = self.api.request('PATCH', self.prefix+'/releases/assets/'+str(canonical['id']), {'name': self.preserved(old)})
                require(renamed['id'] == old['id'] and renamed['name'] == self.preserved(old) and self.matches(renamed, old), 'ORIGINAL_RENAME_MISMATCH')
                self.journal.record('preserved', id=old['id'])
            current = self.assets(release)
            staged = current.get(self.pending(asset))
            require(staged and self.matches(staged, asset), 'STAGED_ASSET_DISAPPEARED')
            self.journal.record('promote-intent', id=staged['id'], name=asset['name'])
            promoted = self.api.request('PATCH', self.prefix+'/releases/assets/'+str(staged['id']), {'name': asset['name']})
            require(promoted['id'] == staged['id'] and promoted['name'] == asset['name'] and self.matches(promoted, asset), 'ASSET_PROMOTION_MISMATCH')
            self.journal.record('promoted', id=promoted['id'], name=asset['name'])
        desired = not release['draft'] and release['prerelease'] == self.plan.prerelease and text_digest(release['body']) == self.p['notes_sha256'] and release['target_commitish'] == self.p['commit']
        latest = self.api.request('GET', self.prefix+'/releases/latest') if desired else None
        correct_latest = (not latest or latest['id'] != release['id']) if self.plan.prerelease else (latest and latest['id'] == release['id'])
        if desired and correct_latest:
            updated = release
        else:
            self.journal.record('publish-intent', release_id=release['id'], notes_sha256=self.p['notes_sha256'])
            updated = self.api.request('PATCH', self.prefix+'/releases/'+str(release['id']), {'name': self.p['product']+' '+self.p['version'],
                'body': self.plan.notes, 'target_commitish': self.p['commit'], 'draft': False,
                'prerelease': self.plan.prerelease, 'make_latest': 'false' if self.plan.prerelease else 'true'})
        require(updated['id'] == release['id'] and text_digest(updated['body']) == self.p['notes_sha256'], 'PUBLISHED_NOTES_MISMATCH')
        self.verify(allow_originals=True)
        current = self.assets(updated)
        by_id = {asset['id']: asset for asset in current.values()}
        canonical_ids = {current[asset['name']]['id'] for asset in self.p['assets']}
        for old in original_ids.values():
            actual = by_id.get(old['id'])
            if actual and old['id'] not in canonical_ids:
                require(actual['name'] == self.preserved(old) and self.matches(actual, old), 'PRESERVED_ASSET_CHANGED')
                self.journal.record('delete-intent', id=old['id'], name=actual['name'])
                self.api.request('DELETE', self.prefix+'/releases/assets/'+str(old['id']))
                self.journal.record('deleted-original-after-verification', id=old['id'])
        return self.verify()

    def verify(self, *, allow_originals=False):
        release = self.release()
        require(release is not None and not release['draft'], 'PUBLISHED_RELEASE_REQUIRED')
        require(release['prerelease'] == self.plan.prerelease, 'RELEASE_STAGE_MISMATCH')
        require(text_digest(release['body']) == self.p['notes_sha256'] and release['target_commitish'] == self.p['commit'], 'RELEASE_NOTES_OR_COMMIT_MISMATCH')
        require(self.tag() == {'ref_sha': self.p['commit'], 'commit': self.p['commit']}, 'RELEASE_TAG_MISMATCH')
        latest = self.api.request('GET', self.prefix+'/releases/latest')
        is_latest = bool(latest and latest['id'] == release['id'])
        require(not is_latest if self.plan.prerelease else is_latest,
                'PRERELEASE_MARKED_LATEST' if self.plan.prerelease else 'RELEASE_NOT_LATEST')
        current = self.assets(release)
        expected = {asset['name'] for asset in self.p['assets']}
        if allow_originals:
            expected.update(self.preserved(old) for old in (self.p.get('original') or {}).get('assets', []) if any(asset['id'] == old['id'] for asset in current.values()))
        require(set(current) == expected, 'RELEASE_ASSET_SET_MISMATCH')
        for asset in self.p['assets']:
            require(self.matches(current[asset['name']], asset), 'RELEASE_ASSET_HASH_MISMATCH')
        self.journal.record('verified', release_id=release['id'], commit=self.p['commit'], assets=[{'name': a['name'], 'sha256': a['sha256']} for a in self.p['assets']])
        return {'repository': self.p['repository'], 'release_id': release['id'], 'tag': self.p['tag'],
                'commit': self.p['commit'], 'assets_verified': len(self.p['assets']), 'latest': is_latest,
                'formal': not self.plan.prerelease, 'prerelease': self.plan.prerelease}


def snapshot(api, repository, tag, output):
    import re
    from release_plan import HEX, NAME
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) and NAME.fullmatch(tag), 'SNAPSHOT_TARGET_INVALID')
    require(not output.exists(), 'SNAPSHOT_ALREADY_EXISTS')
    prefix = '/repos/'+repository
    release = api.request('GET', prefix+'/releases/tags/'+quote(tag, safe=''))
    require(release and not release['draft'] and release['tag_name'] == tag, 'PUBLISHED_ORIGINAL_REQUIRED')
    ref = api.request('GET', prefix+'/git/ref/tags/'+quote(tag, safe=''))
    require(ref is not None, 'ORIGINAL_TAG_REQUIRED')
    raw, obj = ref['object']['sha'], ref['object']
    for _ in range(8):
        if obj['type'] == 'commit':
            break
        require(obj['type'] == 'tag', 'TAG_OBJECT_INVALID')
        obj = api.request('GET', prefix+'/git/tags/'+obj['sha'])['object']
    require(obj['type'] == 'commit', 'TAG_CHAIN_TOO_LONG')
    assets, page = [], 1
    while True:
        batch = api.request('GET', prefix+f'/releases/{release["id"]}/assets?per_page=100&page={page}')
        for asset in batch:
            checksum = asset.get('digest', '').removeprefix('sha256:')
            require(asset['state'] == 'uploaded' and HEX.fullmatch(checksum) and NAME.fullmatch(asset['name']), 'ORIGINAL_ASSET_FINGERPRINT_REQUIRED')
            assets.append({'id': asset['id'], 'name': asset['name'], 'bytes': asset['size'], 'sha256': checksum})
        if len(batch) < 100:
            break
        page += 1
    data = {'release_id': release['id'], 'tag_ref_sha': raw, 'tag_commit': obj['sha'], 'body_sha256': text_digest(release['body']), 'assets': assets}
    atomic_json(output, data)
    return {'original_snapshot': str(output), 'release_id': release['id'], 'tag_commit': obj['sha'], 'assets': len(assets), 'remote_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['snapshot', 'precheck', 'stage', 'publish', 'replace', 'verify', 'recover'])
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--repository')
    parser.add_argument('--tag')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repository-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--offline', action='store_true', help='Only precheck local inputs, without a token or network access')
    args = parser.parse_args()
    try:
        if args.mode == 'snapshot':
            require(args.repository and args.tag and args.output and not args.offline, 'SNAPSHOT_ARGUMENTS_REQUIRED')
            api = GitHub(os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'), args.repository_dir)
            print(json.dumps(snapshot(api, args.repository, args.tag, args.output), indent=2))
            return 0
        require(args.plan is not None, 'PLAN_REQUIRED')
        plan = Plan(args.plan)
        check_local_source(plan, args.repository_dir)
        if args.offline:
            require(args.mode == 'precheck', 'OFFLINE_ONLY_PRECHECK')
            print(json.dumps({'commit': plan.data['commit'], 'plan_sha256': plan.fingerprint, 'local_inputs_verified': True, 'remote_modified': False, 'network_accessed': False}, indent=2))
            return 0
        api = GitHub(os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'), args.repository_dir)
        with journal_lock(plan.base/'release-journal.json'):
            publisher = Publisher(plan, api)
            if args.mode in {'precheck', 'recover'}:
                result = publisher.precheck()
                result['resume_with'] = 'stage, then publish' if not plan.data.get('original') else 'stage, then replace'
            elif args.mode == 'stage':
                result = publisher.stage()
            elif args.mode == 'verify':
                result = publisher.verify()
            else:
                result = publisher.publish(replace=args.mode == 'replace')
        print(json.dumps(result, indent=2))
        return 0
    except ReleaseError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        # No traceback, URLs, response payloads or environment values in release logs.
        print('RELEASE_FAILED_'+type(error).__name__, file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
