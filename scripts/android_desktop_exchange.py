"""Read only same-run, original-job encrypted Desktop/Android handoffs.

No network or extraction is performed here. The future paired driver supplies
the original GitHub API metadata and archive bytes; public-key and sealed
envelopes and bounded public stage receipts are accepted payloads. Plaintext test credentials are never
an exchange artifact, and encryption alone does not establish job provenance.
"""
from datetime import datetime, timedelta
import hashlib
import io
import json
import re
import stat
import zipfile

from android_desktop_handoff import canonical, context_bytes, decode, require, unique_pairs
from android_product_pair_stages import STAGES, validate_stage

PRODUCERS = {
    **STAGES,
    'recipient': ('desktop-pair', 'Publish owned desktop receiving key'),
    'sealed': ('android-desktop-pair', 'Publish sealed owned fixture'),
}


def artifact_name(context, stage):
    context_bytes(context)
    require(stage in PRODUCERS, 'PAIR_EXCHANGE_STAGE_INVALID')
    return 'onx-pair-' + stage + '-' + str(context['run_id']) + '-' + context['tools_commit'][:12]


def validate_body(context, stage, body):
    require(type(body) is dict, 'PAIR_EXCHANGE_BODY_INVALID')
    if stage == 'recipient':
        require(set(body) == {'public_key'}, 'PAIR_EXCHANGE_BODY_INVALID')
        decode(body['public_key'], 32)
    elif stage == 'sealed':
        require(set(body) == {'schema', 'context', 'recipient_sha256', 'ephemeral_public_key', 'nonce', 'ciphertext'}
                and type(body['schema']) is int and body['schema'] == 1
                and context_bytes(body['context']) == context_bytes(context)
                and type(body['recipient_sha256']) is str
                and re.fullmatch(r'[0-9a-f]{64}', body['recipient_sha256']), 'PAIR_EXCHANGE_BODY_INVALID')
        decode(body['ephemeral_public_key'], 32)
        decode(body['nonce'], 12)
        require(16 < len(decode(body['ciphertext'])) <= 4096, 'PAIR_EXCHANGE_BODY_INVALID')
    elif stage in STAGES:
        validate_stage(context,stage,body)
    else:
        require(False, 'PAIR_EXCHANGE_STAGE_INVALID')
    return body


def exchange_bytes(context, stage, body):
    """Bytes for the one explicitly named exchange.json upload, not a file write."""
    artifact_name(context, stage)
    validate_body(context, stage, body)
    return canonical({'schema': 1, 'context': context, 'stage': stage, 'body': body})


def verify_artifact_origin(context, stage, run, artifact):
    """Check immutable original metadata before waiting for its upload step."""
    name = artifact_name(context, stage)
    require(run['id'] == context['run_id'] and run['head_sha'] == context['tools_commit']
            and run['head_branch'] == 'feat/android-sync-foreground'
            and run['path'] == '.github/workflows/android.yml'
            and run['event'] == 'workflow_dispatch' and run['run_attempt'] == 1
            and run['repository']['full_name'] == 'KiriAky107/OpenNexus', 'PAIR_EXCHANGE_RUN_INVALID')
    origin = artifact['workflow_run']
    require(origin['id'] == run['id'] and origin['head_sha'] == context['tools_commit']
            and origin['head_branch'] == run['head_branch']
            and origin['repository_id'] == origin['head_repository_id'] == run['repository']['id']
            and artifact['name'] == name and artifact['expired'] is False
            and type(artifact['id']) is int and artifact['id'] > 0
            and type(artifact['size_in_bytes']) is int and 0 < artifact['size_in_bytes'] <= 256 * 1024
            and type(artifact['digest']) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', artifact['digest']),
            'PAIR_EXCHANGE_ARTIFACT_INVALID')
    return name


def verify_origin(context, stage, run, jobs, artifact):
    """Bind immutable artifacts to this workflow/source AND the named upload step.

    The artifact API has no producer job_id. Its timestamp must fall within the
    original named job's successful upload step, whose fixed workflow names are
    part of the reviewed tools source. Do not claim a cryptographic sender
    signature from this API correlation.
    """
    name = verify_artifact_origin(context, stage, run, artifact)
    job_name, step_name = PRODUCERS[stage]
    own_jobs = [job for job in jobs if job['name'] == job_name]
    require(len(own_jobs) == 1 and own_jobs[0]['run_id'] == run['id'], 'PAIR_EXCHANGE_JOB_INVALID')
    job = own_jobs[0]
    steps = [step for step in job['steps'] if step['name'] == step_name]
    require(len(steps) == 1 and steps[0]['status'] == 'completed'
            and steps[0]['conclusion'] == 'success', 'PAIR_EXCHANGE_UPLOAD_INCOMPLETE')
    step = steps[0]
    created = datetime.fromisoformat(artifact['created_at'].replace('Z', '+00:00'))
    started = datetime.fromisoformat(step['started_at'].replace('Z', '+00:00'))
    completed = datetime.fromisoformat(step['completed_at'].replace('Z', '+00:00'))
    require(started <= created <= completed + timedelta(seconds=2), 'PAIR_EXCHANGE_UPLOAD_TIME_INVALID')
    return {'artifact_id': artifact['id'], 'artifact_name': name, 'archive_digest': artifact['digest'],
            'original_run_id': run['id'], 'original_job_id': job['id'], 'original_upload_step': step_name,
            'tools_commit': context['tools_commit'], 'artifact_has_producer_job_id': False,
            'producer_correlated_by_fixed_step_and_timestamp': True}


def read_exchange(context, stage, run, jobs, artifact, archive):
    provenance = verify_origin(context, stage, run, jobs, artifact)
    require(type(archive) is bytes and 0 < len(archive) <= 256 * 1024
            and 'sha256:' + hashlib.sha256(archive).hexdigest() == artifact['digest'],
            'PAIR_EXCHANGE_ARCHIVE_INVALID')
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        entries = source.infolist()
        require(len(entries) == 1 and entries[0].filename == 'exchange.json'
                and 0 < entries[0].file_size <= 16 * 1024
                and stat.S_IFMT(entries[0].external_attr >> 16) != stat.S_IFLNK,
                'PAIR_EXCHANGE_ENTRIES_INVALID')
        body = source.read(entries[0])
    value = json.loads(body.decode('utf-8'), object_pairs_hook=unique_pairs)
    require(type(value) is dict and set(value) == {'schema', 'context', 'stage', 'body'}
            and type(value['schema']) is int and value['schema'] == 1 and value['stage'] == stage
            and context_bytes(value['context']) == context_bytes(context), 'PAIR_EXCHANGE_ENVELOPE_INVALID')
    return dict(validate_body(context, stage, value['body'])), provenance
