"""Same-run, source-checked GitHub reads for the real paired-job handoff.

Preparation only until wired to actual paired clients. Never uploads a key,
credential or artifact and never retries a partially downloaded archive.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.parse import urlsplit

from android_desktop_exchange import PRODUCERS, artifact_name, read_exchange, verify_artifact_origin, verify_origin
from android_desktop_handoff import HandoffError, canonical, context_bytes, require

BASE = '/repos/KiriAky107/OpenNexus/actions'

# Only these reviewed constants may accompany the exception class in CI output.
# Never print arbitrary exception messages: they can contain signed URLs, a
# response body or a decrypted test account. Other failures keep the class only.
SAFE_FAILURE_CODES = frozenset({
    'PAIR_API_HOST_REFUSED', 'PAIR_API_TOKEN_UNAVAILABLE', 'PAIR_API_PATH_REFUSED',
    'PAIR_API_ORIGINAL_RUN_INVALID', 'PAIR_API_ARCHIVE_ORIGIN_INVALID',
    'PAIR_API_ARCHIVE_REDIRECT_INVALID', 'PAIR_API_READ_FAILED', 'PAIR_API_JSON_TOO_LARGE',
    'PAIR_API_JSON_INVALID', 'PAIR_API_READ_UNAVAILABLE', 'PAIR_API_ORIGINAL_DOWNLOAD_ALREADY_EXISTS',
    'PAIR_API_ARCHIVE_READ_FAILED', 'PAIR_API_ARCHIVE_TOO_LARGE', 'PAIR_API_ARCHIVE_DIGEST_INVALID',
    'PAIR_API_ORIGINAL_ARCHIVE_UNCONFIRMED', 'PAIR_API_WAIT_BOUND_INVALID', 'PAIR_API_CACHE_OWNER_INVALID',
    'PAIR_API_PARTIAL_REQUIRES_RECONCILIATION', 'PAIR_API_ARTIFACT_PAGE_UNCONFIRMED',
    'PAIR_API_ARTIFACT_AMBIGUOUS', 'PAIR_API_JOB_PAGE_UNCONFIRMED', 'PAIR_API_ORIGINAL_ARTIFACT_CHANGED',
    'PAIR_API_PRODUCER_AMBIGUOUS', 'PAIR_API_ORIGINAL_PRODUCER_FAILED', 'PAIR_API_ORIGINAL_STAGE_UNAVAILABLE',
    'PAIR_EXCHANGE_RUN_INVALID', 'PAIR_EXCHANGE_ARTIFACT_INVALID', 'PAIR_EXCHANGE_JOB_INVALID',
    'PAIR_EXCHANGE_UPLOAD_INCOMPLETE', 'PAIR_EXCHANGE_UPLOAD_TIME_INVALID', 'PAIR_EXCHANGE_ARCHIVE_INVALID',
    'PAIR_EXCHANGE_ENTRIES_INVALID', 'PAIR_EXCHANGE_ENVELOPE_INVALID', 'PAIR_EXCHANGE_BODY_INVALID',
    'HANDOFF_CONTEXT_INVALID', 'HANDOFF_ENVELOPE_INVALID', 'HANDOFF_PAYLOAD_INVALID',
})


def failure_label(error):
    label = type(error).__name__
    if type(error) in (HandoffError, RuntimeError) and str(error) in SAFE_FAILURE_CODES:
        return label + '_' + str(error)
    return label


def original_context(context, run):
    context_bytes(context)
    require(run.get('id') == context['run_id'] and run.get('head_sha') == context['tools_commit']
            and run.get('head_branch') == 'feat/android-sync-foreground'
            and run.get('path') == '.github/workflows/android.yml' and run.get('event') == 'workflow_dispatch'
            and run.get('run_attempt') == 1 and run.get('repository', {}).get('full_name') == 'KiriAky107/OpenNexus',
            'PAIR_API_ORIGINAL_RUN_INVALID')


def signed_archive_target(value):
    url = urlsplit(value)
    require(url.scheme == 'https' and url.hostname is not None and not url.username and not url.password
            and url.port in (None, 443) and not url.fragment
            and (url.hostname.endswith('.blob.core.windows.net') or url.hostname.endswith('.githubusercontent.com')),
            'PAIR_API_ARCHIVE_ORIGIN_INVALID')
    return url.hostname, url.path + ('?' + url.query if url.query else '')


def write_once(path, body):
    with path.open('xb') as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno())


class OriginalRunAPI:
    def __init__(self, context, env):
        context_bytes(context)
        require(env.get('GITHUB_ACTIONS') == 'true' and env.get('RUNNER_ENVIRONMENT') == 'github-hosted'
                and env.get('RUNNER_OS') in ('Linux', 'Windows') and env.get('GITHUB_REPOSITORY') == 'KiriAky107/OpenNexus'
                and env.get('GITHUB_RUN_ID') == str(context['run_id']) and env.get('GITHUB_SHA') == context['tools_commit'],
                'PAIR_API_HOST_REFUSED')
        token = env.get('GH_TOKEN') or env.get('GITHUB_TOKEN') or ''
        # GitHub tokens are opaque, including the longer stateless installation
        # token format. Check header safety only; no prefix, JWT or length rule.
        require(type(token) is str and bool(token) and all(33 <= ord(c) <= 126 for c in token),
                'PAIR_API_TOKEN_UNAVAILABLE')
        self.context, self.token = dict(context), token

    def request(self, path, *, redirect=False):
        require(re.fullmatch(BASE + r'/(?:runs/[1-9][0-9]{0,18}(?:/(?:jobs|artifacts)\?per_page=100)?|artifacts/[1-9][0-9]{0,18}/zip)', path),
                'PAIR_API_PATH_REFUSED')
        for delay in (0, 2, 6):
            if delay: time.sleep(delay)
            connection = http.client.HTTPSConnection('api.github.com', timeout=30)
            try:
                connection.request('GET', path, headers={'Authorization':'Bearer ' + self.token,
                    'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'OpenNexus-owned-product-pair'})
                response = connection.getresponse()
                if redirect:
                    require(response.status in (301,302,307), 'PAIR_API_ARCHIVE_REDIRECT_INVALID')
                    location = response.getheader('Location'); signed_archive_target(location)
                    response.read(4096)
                    return location
                require(response.status == 200, 'PAIR_API_READ_FAILED')
                body = response.read(2 * 1024**2 + 1)
                require(len(body) <= 2 * 1024**2, 'PAIR_API_JSON_TOO_LARGE')
                value = json.loads(body)
                require(type(value) is dict, 'PAIR_API_JSON_INVALID')
                return value
            except (ssl.SSLEOFError, TimeoutError, ConnectionError):
                if delay == 6: raise RuntimeError('PAIR_API_READ_UNAVAILABLE') from None
            finally:
                connection.close()

    def archive(self, item, destination):
        require(type(item.get('id')) is int and item['id'] > 0 and not destination.exists()
                and not destination.with_suffix('.partial').exists(), 'PAIR_API_ORIGINAL_DOWNLOAD_ALREADY_EXISTS')
        location = self.request(BASE + '/artifacts/' + str(item['id']) + '/zip', redirect=True)
        host, path = signed_archive_target(location)
        connection = http.client.HTTPSConnection(host, timeout=60)
        partial = destination.with_suffix('.partial')
        try:
            # A signed storage URL receives no GitHub Authorization header.
            connection.request('GET', path, headers={'User-Agent':'OpenNexus-owned-product-pair'})
            response = connection.getresponse()
            require(response.status == 200, 'PAIR_API_ARCHIVE_READ_FAILED')
            with partial.open('xb') as output:
                size = 0; digest = hashlib.sha256()
                while block := response.read(65536):
                    size += len(block); require(size <= 256 * 1024, 'PAIR_API_ARCHIVE_TOO_LARGE')
                    digest.update(block); output.write(block)
                output.flush(); os.fsync(output.fileno())
            require(size > 0 and item['digest'] == 'sha256:' + digest.hexdigest(), 'PAIR_API_ARCHIVE_DIGEST_INVALID')
            partial.rename(destination)
        except Exception:
            # Preserve any partial bytes. No automatic second archive download.
            raise RuntimeError('PAIR_API_ORIGINAL_ARCHIVE_UNCONFIRMED') from None
        finally:
            connection.close()


def wait_original_exchange(context, stage, api, cache, *, timeout=1200):
    """Caller first validates/claims its hosted root; cache is a NEW child.

    Existing complete cached originals are reread without another API/download.
    Unknown partial downloads require reconciliation, not a replacement.
    """
    name = artifact_name(context, stage)
    require(type(timeout) is int and 1 <= timeout <= 1800, 'PAIR_API_WAIT_BOUND_INVALID')
    cache = Path(cache)
    marker = canonical({'context':context,'stage':stage,'artifact_name':name})
    if cache.exists():
        require(not cache.is_symlink() and cache.is_dir() and (cache/'intent.json').read_bytes() == marker,
                'PAIR_API_CACHE_OWNER_INVALID')
    else:
        cache.mkdir(exist_ok=False)
        write_once(cache/'intent.json', marker)
    if (cache/'original.zip').exists():
        run = json.loads((cache/'run.json').read_bytes()); jobs = json.loads((cache/'jobs.json').read_bytes())
        item = json.loads((cache/'artifact.json').read_bytes())
        return read_exchange(context, stage, run, jobs, item, (cache/'original.zip').read_bytes())
    require(not (cache/'original.partial').exists(), 'PAIR_API_PARTIAL_REQUIRES_RECONCILIATION')
    if (cache/'artifact.json').exists():
        # Metadata was frozen but no archive body was opened/read. Reconcile
        # only that same original ID/digest, without listing a replacement.
        run = json.loads((cache/'run.json').read_bytes()); jobs = json.loads((cache/'jobs.json').read_bytes())
        item = json.loads((cache/'artifact.json').read_bytes())
        verify_origin(context, stage, run, jobs, item)
        api.archive(item,cache/'original.zip')
        return read_exchange(context, stage, run, jobs, item, (cache/'original.zip').read_bytes())
    deadline, delay = time.monotonic() + timeout, 2
    while True:
        run = api.request(BASE + '/runs/' + str(context['run_id'])); original_context(context, run)
        listing = api.request(BASE + '/runs/' + str(context['run_id']) + '/artifacts?per_page=100')
        require(type(listing.get('artifacts')) is list and listing.get('total_count',len(listing['artifacts'])) <= 100,
                'PAIR_API_ARTIFACT_PAGE_UNCONFIRMED')
        found = [x for x in listing['artifacts'] if x.get('name') == name]
        require(len(found) <= 1, 'PAIR_API_ARTIFACT_AMBIGUOUS')
        jobs_reply = api.request(BASE + '/runs/' + str(context['run_id']) + '/jobs?per_page=100')
        require(type(jobs_reply.get('jobs')) is list and jobs_reply.get('total_count',len(jobs_reply['jobs'])) <= 100,
                'PAIR_API_JOB_PAGE_UNCONFIRMED')
        jobs = jobs_reply['jobs']
        if found:
            item = found[0]
            verify_artifact_origin(context, stage, run, item)
            observed = cache/'observed-artifact.json'
            if observed.exists():
                original = json.loads(observed.read_bytes())
                immutable = ('id','name','size_in_bytes','digest','created_at','workflow_run')
                require(all(item[k] == original[k] for k in immutable), 'PAIR_API_ORIGINAL_ARTIFACT_CHANGED')
            else:
                write_once(observed,canonical(item))
            try:
                provenance = verify_origin(context, stage, run, jobs, item)
            except HandoffError as error:
                # Artifact visibility can precede the same upload step's final
                # status. Preserve its identity and wait; never download an
                # unconfirmed artifact or accept a different ID/digest.
                if str(error) != 'PAIR_EXCHANGE_UPLOAD_INCOMPLETE': raise
                own_jobs = [j for j in jobs if j['name'] == PRODUCERS[stage][0]]
                steps = [x for x in own_jobs[0]['steps'] if x['name'] == PRODUCERS[stage][1]]
                require(len(steps) == 1 and steps[0]['status'] in ('queued','in_progress')
                        and steps[0]['conclusion'] is None, 'PAIR_EXCHANGE_UPLOAD_INCOMPLETE')
            else:
                write_once(cache/'run.json',canonical(run)); write_once(cache/'jobs.json',canonical(jobs))
                write_once(cache/'artifact.json',canonical(item)); write_once(cache/'provenance.json',canonical(provenance))
                api.archive(item,cache/'original.zip')
                return read_exchange(context, stage, run, jobs, item, (cache/'original.zip').read_bytes())
        producer = [x for x in jobs if x.get('name') == PRODUCERS[stage][0]]
        require(len(producer) <= 1, 'PAIR_API_PRODUCER_AMBIGUOUS')
        require(not producer or producer[0].get('conclusion') in (None,'success'), 'PAIR_API_ORIGINAL_PRODUCER_FAILED')
        require(run.get('status') != 'completed' and time.monotonic() < deadline, 'PAIR_API_ORIGINAL_STAGE_UNAVAILABLE')
        time.sleep(min(delay,max(.01,deadline-time.monotonic()))); delay = min(delay*2,30)
