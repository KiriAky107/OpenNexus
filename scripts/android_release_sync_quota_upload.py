"""Optional bounded observations of original normal-ASGI quota responses.

No HTTP/header/token/device/clock query or stream replay. Context identities are
frozen caller scope, not a server echo or independent Native identity/ACK. The
new quota host must admit its parent and freeze context BEFORE ready/input;
that host/driver integration is not implemented here.
"""
import hashlib
import json
from pathlib import Path
import re

from android_release_observation import exclusive
import android_release_sync_quota_validation as validation

PHASE = 'cycle-quota-signed-original'
CONTEXT = 'release-quota-upload-context.json'
OBSERVATION = 'release-quota-upload-observation.json'


def need(value):
    if not value:
        raise RuntimeError('SIGNED_QUOTA_UPLOAD_UNAVAILABLE')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def parsed(raw):
    def pairs(items):
        value = dict(items); need(len(value) == len(items)); return value
    def constant(_): need(False)
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def parent_value(parent, input_id):
    return dict(schema=1, phase=PHASE, parent_intent_id=parent, input_intent_id=input_id,
                native_invocation_id_observed=False)


def validate_context(value):
    keys = ('schema','phase','product','sync','owner_run','parent_id','input_id','remote_vault','device_id',
            'parent_metadata_canonical_sha256')
    need(validation._closed(value, keys) and validation._same(value['schema'], 1)
         and validation._same(value['phase'], PHASE) and validation._same(value['product'], validation.PRODUCT)
         and validation._same(value['sync'], validation.SYNC)
         and validation._pattern(value['owner_run'], r'[1-9][0-9]{0,17}-[1-9][0-9]{0,5}')
         and validation._uuid(value['parent_id']) and validation._uuid(value['input_id'])
         and value['parent_id'] != value['input_id']
         and all(validation._pattern(value[k], r'[0-9a-f]{32}') for k in ('remote_vault','device_id')))
    digest = hashlib.sha256(encoded(parent_value(value['parent_id'], value['input_id']))).hexdigest()
    need(validation._same(value['parent_metadata_canonical_sha256'], digest))
    return dict(value)


def context_from_parent(parent, *, owner_run, remote_vault, device_id):
    need(validation._closed(parent, ('schema','phase','parent_intent_id','input_intent_id','native_invocation_id_observed')))
    need(validation._uuid(parent['parent_intent_id']) and validation._uuid(parent['input_intent_id'])
         and validation._same(parent, parent_value(parent['parent_intent_id'],parent['input_intent_id'])))
    value = dict(schema=1, phase=PHASE, product=validation.PRODUCT, sync=validation.SYNC, owner_run=owner_run,
        parent_id=parent['parent_intent_id'], input_id=parent['input_intent_id'], remote_vault=remote_vault, device_id=device_id,
        parent_metadata_canonical_sha256=hashlib.sha256(encoded(parent)).hexdigest())
    return validate_context(value)


def read_context(owner, *, owner_run, expected=None):
    path = owner/CONTEXT
    need(owner.absolute() == owner.resolve() and path.absolute() == path.resolve()
         and path.is_file() and not path.is_symlink())
    stat = path.stat(); need(stat.st_nlink == 1 and 0 < stat.st_size <= 2048)
    with path.open('rb') as stream:
        raw = stream.read(2049)
    need(len(raw) == stat.st_size and len(raw) <= 2048)
    value = validate_context(parsed(raw))
    need(value['owner_run'] == owner_run and raw == encoded(value))
    if expected is not None:
        need(validation._same(value, validate_context(expected)))
    return value, hashlib.sha256(raw).hexdigest()


def freeze_context(owner, parent, *, remote_vault, device_id):
    # This is a future host pre-ready entry only, never an automatic continuation
    # or a new call against a closed driver. Unknown writes require read_context
    # reconciliation of the same original file, not another write attempt.
    from android_release_sync_quota_fixture import _owned, normal_target, require_prepare_proof
    run,_,_ = _owned(owner, ready=True)
    require_prepare_proof(owner)
    need(normal_target(owner) == remote_vault)
    value = context_from_parent(parent, owner_run=run, remote_vault=remote_vault, device_id=device_id)
    path = owner/CONTEXT
    need(not path.exists() and not path.is_symlink())
    raw = encoded(value); need(len(raw) <= 2048)
    result = exclusive(path, raw)
    need(result is None)
    return value, hashlib.sha256(raw).hexdigest()


def packet(context, context_sha):
    response = dict(owner_run=context['owner_run'], parent_id=context['parent_id'], input_id=context['input_id'],
        remote_vault=context['remote_vault'], device_id=context['device_id'], method='POST',
        path='/sync/v1/vaults/'+context['remote_vault']+'/uploads', request=dict(content_hash=validation.HASH,size=56),
        service_status=413, delivery_status=413, service_error_code='QUOTA_EXCEEDED', fault_operation=None,
        asgi_send_returned_reported=True, client_receipt_verified=False)
    return dict(schema=1, context=dict(context), context_file_sha256=context_sha, upload_response=response,
        request_from_original_receive=True, response_from_original_send=True, original_app_returned=True,
        device_identity_from_frozen_context=True, independent_Native_identity_observed=False,
        raw_HTTP_or_headers_recorded=False, client_received_or_applied_acknowledgement=False,
        UI_verified=False, Native_verified=False, runtime_verified=False)


def validate_observation(raw, *, context):
    try:
        need(type(raw) is bytes and 0 < len(raw) <= 4096)
        expected = validate_context(context)
        value = parsed(raw)
        closed = packet(expected, hashlib.sha256(encoded(expected)).hexdigest())
        need(validation._same(value, closed))
        return closed
    except Exception:
        raise RuntimeError('SIGNED_QUOTA_UPLOAD_UNAVAILABLE') from None


class Capture:
    def __init__(self):
        self.request = bytearray(); self.response = bytearray()
        self.request_done = self.started = self.response_done = self.dead = False

    def disable(self):
        self.dead = True; self.request.clear(); self.response.clear()

    def receive(self, value):
        if self.dead: return
        try:
            need(type(value) is dict and all(type(k) is str for k in value))
            if validation._same(value.get('type'), 'http.disconnect'):
                need(set(value) == {'type'} and self.request_done); return
            need(set(value) <= {'type','body','more_body'} and validation._same(value.get('type'), 'http.request')
                 and not self.request_done and type(value.get('body', b'')) is bytes
                 and type(value.get('more_body', False)) is bool)
            body = value.get('body', b'')
            need(len(self.request)+len(body) <= 512)
            self.request.extend(body)
            if not value.get('more_body', False):
                need(validation._same(parsed(bytes(self.request)), dict(content_hash=validation.HASH,size=56)))
                self.request_done = True; self.request.clear()
        except Exception:
            self.disable()

    def send(self, value):
        if self.dead: return
        try:
            need(type(value) is dict and all(type(k) is str for k in value))
            if validation._same(value.get('type'), 'http.response.start'):
                # Header values are deliberately never read or retained.
                need(set(value) <= {'type','status','headers','trailers'} and not self.started
                     and validation._same(value.get('status'), 413)
                     and value.get('trailers', False) is False)
                self.started = True; return
            need(set(value) <= {'type','body','more_body'} and validation._same(value.get('type'), 'http.response.body')
                 and self.started and not self.response_done and type(value.get('body', b'')) is bytes
                 and type(value.get('more_body', False)) is bool)
            body = value.get('body', b'')
            need(len(self.response)+len(body) <= 1024)
            self.response.extend(body)
            if not value.get('more_body', False):
                need(validation._same(parsed(bytes(self.response)), dict(error=dict(code='QUOTA_EXCEEDED',details={}))))
                self.response_done = True; self.response.clear()
        except Exception:
            self.disable()


class ObserveUpload:
    def __init__(self, app, owner, *, owner_run, writer=None):
        need(validation._pattern(owner_run, r'[1-9][0-9]{0,17}-[1-9][0-9]{0,5}'))
        self.app, self.owner, self.run = app, owner, owner_run
        self.writer = exclusive if writer is None else writer
        self.used = self.written = False

    async def __call__(self, scope, receive, send):
        candidate = (type(scope) is dict and validation._same(scope.get('type'), 'http')
            and validation._same(scope.get('method'), 'POST') and type(scope.get('path')) is str
            and len(scope['path']) <= 80 and re.fullmatch(r'/sync/v1/vaults/[0-9a-f]{32}/uploads',scope['path']))
        if self.used or not candidate:
            return await self.app(scope, receive, send)
        self.used = True
        try:
            context, digest = read_context(self.owner, owner_run=self.run)
            need(validation._same(scope.get('query_string'), b'')
                 and scope['path'] == '/sync/v1/vaults/'+context['remote_vault']+'/uploads')
        except Exception:
            return await self.app(scope, receive, send)
        capture = Capture()
        async def original_receive():
            value = await receive()  # Preserve original exception and message.
            capture.receive(value)
            return value
        async def original_send(value):
            result = await send(value)  # Only observe an original completed send.
            capture.send(value)
            return result
        try:
            result = await self.app(scope, original_receive, original_send)
            if not capture.dead and capture.request_done and capture.response_done:
                try:
                    raw = encoded(packet(context,digest)); need(len(raw) <= 4096)
                    path = self.owner/OBSERVATION
                    need(self.owner.absolute() == self.owner.resolve())
                    if not path.exists() and not path.is_symlink():
                        self.written = self.writer(path,raw) is None
                except Exception:
                    pass  # Optional diagnostics cannot replace service outcome.
            return result
        finally:
            capture.disable()
