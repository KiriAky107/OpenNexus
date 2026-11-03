"""Original ASGI messages and small exclusive records, all controlled/synthetic."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

import android_release_sync_quota_upload as upload
import android_release_sync_quota_fixture as fixture
from test_android_release_sync_quota_fixture import owner_scope, pg_modules, FakePG

REQUEST = json.dumps(dict(content_hash=upload.validation.HASH,size=56)).encode()
RESPONSE = b'{"error":{"code":"QUOTA_EXCEEDED","details":{}}}'


def parent():
    return upload.parent_value('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000003')


@contextmanager
def prepared(*, freeze=True):
    with owner_scope() as (root,state,config):
        with pg_modules(FakePG()):fixture.verify_prepared(root)
        context = upload.context_from_parent(parent(),owner_run='12345-1',remote_vault='1'*32,device_id='2'*32)
        if freeze:
            actual,digest=upload.freeze_context(root,parent(),remote_vault='1'*32,device_id='2'*32)
            assert actual==context
        yield root,context


def request_messages(raw=REQUEST, *, chunks=False, complete=True):
    if chunks:
        return [dict(type='http.request',body=raw[:9],more_body=True),
                dict(type='http.request',body=raw[9:],more_body=not complete)]
    return [dict(type='http.request',body=raw,more_body=not complete)]


def response_messages(raw=RESPONSE, *, chunks=False, complete=True, status=413, headers=None):
    values=[dict(type='http.response.start',status=status,headers=[] if headers is None else headers)]
    if chunks:values.append(dict(type='http.response.body',body=raw[:11],more_body=True))
    values.append(dict(type='http.response.body',body=raw[11:] if chunks else raw,more_body=not complete))
    return values


def service(receive_count, responses, marker, *, final_error=None, seen=None):
    async def app(scope,receive,send):
        if seen is not None:seen.append(('scope',scope))
        for _ in range(receive_count):
            message=await receive()
            if seen is not None:seen.append(('received',message))
        for message in responses:
            result=await send(message)
            if seen is not None:seen.append(('send_returned',result))
        if final_error is not None:raise final_error
        return marker
    return app


def invoke(observer, messages, *, scope=None, send_error=None, receive_error=None):
    scope = dict(type='http',method='POST',path='/sync/v1/vaults/'+'1'*32+'/uploads',query_string=b'') if scope is None else scope
    iterator=iter(messages);calls=[];sent=[];token=object()
    async def receive():
        calls.append('receive')
        if receive_error is not None:raise receive_error
        return next(iterator)
    async def send(message):
        calls.append('send');sent.append(message)
        if send_error is not None:raise send_error
        return token
    result=asyncio.run(observer(scope,receive,send))
    return result,calls,sent,token,scope


class UploadObservation(unittest.TestCase):
    def test_pre_ready_context_uses_known_typed_parent_and_one_original_buffer_without_overwrite(self):
        with prepared() as (root,context):
            raw=(root/upload.CONTEXT).read_bytes()
            self.assertEqual(raw,upload.encoded(context))
            actual,digest=upload.read_context(root,owner_run='12345-1',expected=context)
            self.assertEqual(actual,context);self.assertEqual(digest,hashlib.sha256(raw).hexdigest())
            self.assertEqual(context['parent_metadata_canonical_sha256'],hashlib.sha256(upload.encoded(parent())).hexdigest())
            writer=Mock()
            with patch.object(upload,'exclusive',writer),self.assertRaises(RuntimeError):
                upload.freeze_context(root,parent(),remote_vault='1'*32,device_id='2'*32)
            writer.assert_not_called();self.assertEqual((root/upload.CONTEXT).read_bytes(),raw)

    def test_chunked_original_messages_results_and_headers_are_not_replayed_or_retained(self):
        class Private:
            def __str__(self):raise AssertionError('private header read')
            def __repr__(self):raise AssertionError('private header read')
        with prepared() as (root,context):
            requests=request_messages(chunks=True);responses=response_messages(chunks=True,headers=Private())
            marker=object();seen=[];observer=upload.ObserveUpload(service(2,responses,marker,seen=seen),root,owner_run='12345-1')
            result,calls,sent,token,scope=invoke(observer,requests)
            self.assertIs(result,marker);self.assertEqual(calls,['receive','receive','send','send','send'])
            self.assertIs(seen[0][1],scope)
            for a,b in zip([v for k,v in seen if k=='received'],requests):self.assertIs(a,b)
            for a,b in zip(sent,responses):self.assertIs(a,b)
            for k,v in seen:
                if k=='send_returned':self.assertIs(v,token)
            self.assertTrue(observer.written)
            raw=(root/upload.OBSERVATION).read_bytes();self.assertLess(len(raw),4096)
            value=upload.validate_observation(raw,context=context)
            self.assertEqual(value['upload_response']['request'],dict(content_hash=upload.validation.HASH,size=56))
            self.assertTrue(value['device_identity_from_frozen_context'])
            for k in ('independent_Native_identity_observed','raw_HTTP_or_headers_recorded',
                      'client_received_or_applied_acknowledgement','Native_verified','UI_verified','runtime_verified'):
                self.assertIs(value[k],False)

    def test_private_duplicate_invalid_partial_oversize_or_float_request_is_only_unavailable(self):
        invalid=[(REQUEST+b' ',False),(b'{"content_hash":"private","size":56}',True),
                 (REQUEST[:-1]+b',"token":"private"}',True),(b'\xff',True),(b' '*513,True),
                 (REQUEST.replace(b'56',b'56.0'),True),(REQUEST.replace(b'"size": 56',b'"size":56,"size":56'),True)]
        for body,complete in invalid:
            with self.subTest(length=len(body),complete=complete),prepared() as (root,_):
                marker=object();responses=response_messages()
                observer=upload.ObserveUpload(service(1,responses,marker),root,owner_run='12345-1')
                result,calls,sent,*_=invoke(observer,request_messages(body,complete=complete))
                self.assertIs(result,marker);self.assertEqual(calls,['receive','send','send'])
                self.assertIs(sent[-1],responses[-1]);self.assertFalse((root/upload.OBSERVATION).exists())

    def test_complete_exact_quota_response_only_no_status_body_or_stream_fallback(self):
        cases=[response_messages(status=200),response_messages(status=True),response_messages(b'QUOTA_EXCEEDED'),
               response_messages(b'{"error":{"code":"QUOTA_EXCEEDED","details":{"private":1}}}'),
               response_messages(RESPONSE.replace(b'"code":',b'"code":"duplicate","code":')),
               response_messages(b'\xff'),response_messages(b'x'*1025),response_messages(complete=False)]
        for responses in cases:
            with prepared() as (root,_):
                marker=object();observer=upload.ObserveUpload(service(1,responses,marker),root,owner_run='12345-1')
                result,calls,sent,*_=invoke(observer,request_messages())
                self.assertIs(result,marker);self.assertEqual(calls.count('send'),len(responses))
                for a,b in zip(sent,responses):self.assertIs(a,b)
                self.assertFalse((root/upload.OBSERVATION).exists())

    def test_original_receive_send_and_post_send_app_exceptions_keep_the_same_object(self):
        for kind in ('receive','send','app-after-send'):
            with self.subTest(kind=kind),prepared() as (root,_):
                failure=RuntimeError('original-controlled-failure');writer=Mock()
                app=service(1,response_messages(),object(),final_error=failure if kind=='app-after-send' else None)
                observer=upload.ObserveUpload(app,root,owner_run='12345-1',writer=writer)
                with self.assertRaises(RuntimeError) as caught:
                    invoke(observer,request_messages(),receive_error=failure if kind=='receive' else None,
                           send_error=failure if kind=='send' else None)
                self.assertIs(caught.exception,failure);writer.assert_not_called()
                self.assertFalse((root/upload.OBSERVATION).exists())

    def test_optional_partial_existing_unknown_or_false_write_never_changes_service_or_retries(self):
        for kind in ('partial','existing','unknown-complete','unknown-empty','false'):
            with self.subTest(kind=kind),prepared() as (root,context):
                path=root/upload.OBSERVATION;writes=[];marker=object()
                if kind=='existing':path.write_bytes(b'original-partial')
                def writer(target,raw):
                    writes.append(raw)
                    if kind=='false':return False
                    if kind=='partial':target.write_bytes(raw[:12])
                    if kind=='unknown-complete':upload.exclusive(target,raw)
                    raise OSError('synthetic private write detail')
                observer=upload.ObserveUpload(service(1,response_messages(),marker),root,owner_run='12345-1',writer=writer)
                result,*_=invoke(observer,request_messages());self.assertIs(result,marker);self.assertFalse(observer.written)
                first=path.read_bytes() if path.exists() else None
                result,*_=invoke(observer,request_messages());self.assertIs(result,marker)
                self.assertEqual(len(writes),0 if kind=='existing' else 1)
                self.assertEqual(path.read_bytes() if path.exists() else None,first)
                if kind=='unknown-complete':
                    value=upload.validate_observation(first,context=context)
                    self.assertFalse(value['client_received_or_applied_acknowledgement'])

    def test_missing_or_unknown_first_context_is_not_repaired_by_observing_a_second_upload(self):
        for kind in ('missing','partial','wrong-owner','noncanonical','private'):
            with self.subTest(kind=kind),prepared(freeze=False) as (root,context):
                if kind!='missing':
                    value=dict(context)
                    if kind=='wrong-owner':value['owner_run']='999-1'
                    if kind=='private':value['token']='must not be retained'
                    raw=b'{' if kind=='partial' else upload.encoded(value)+(b' ' if kind=='noncanonical' else b'')
                    (root/upload.CONTEXT).write_bytes(raw)
                marker=object();writer=Mock()
                observer=upload.ObserveUpload(service(1,response_messages(),marker),root,owner_run='12345-1',writer=writer)
                result,*_=invoke(observer,request_messages());self.assertIs(result,marker)
                (root/upload.CONTEXT).write_bytes(upload.encoded(context))
                result,*_=invoke(observer,request_messages());self.assertIs(result,marker)
                writer.assert_not_called();self.assertFalse((root/upload.OBSERVATION).exists())

    def test_unknown_context_publish_preserves_original_file_and_only_same_id_read_can_reconcile(self):
        for complete in (False,True):
            with prepared(freeze=False) as (root,context):
                calls=[]
                def unknown(path,raw):
                    calls.append(True);path.write_bytes(raw if complete else raw[:8]);raise OSError('unknown original write')
                with patch.object(upload,'exclusive',side_effect=unknown):
                    with self.assertRaises(OSError):upload.freeze_context(root,parent(),remote_vault='1'*32,device_id='2'*32)
                    first=(root/upload.CONTEXT).read_bytes()
                    with self.assertRaises(RuntimeError):upload.freeze_context(root,parent(),remote_vault='1'*32,device_id='2'*32)
                self.assertEqual(calls,[True]);self.assertEqual((root/upload.CONTEXT).read_bytes(),first)
                if complete:self.assertEqual(upload.read_context(root,owner_run='12345-1',expected=context)[0],context)
                else:
                    with self.assertRaises((ValueError,RuntimeError)):upload.read_context(root,owner_run='12345-1',expected=context)

    def test_closed_reader_scope_hash_types_private_fields_duplicate_and_whole4096_bound(self):
        with prepared() as (root,context):
            observer=upload.ObserveUpload(service(1,response_messages(),None),root,owner_run='12345-1')
            invoke(observer,request_messages());raw=(root/upload.OBSERVATION).read_bytes();value=json.loads(raw)
            upload.validate_observation(raw+b' '*(4096-len(raw)),context=context)
            bads=[raw+b' '*(4097-len(raw)),b'\xff',raw[:-1],raw.replace(b'"schema":1',b'"schema":1,"schema":1',1)]
            for key,change in (('schema',True),('context_file_sha256','0'*64),('raw_HTTP_or_headers_recorded',True),('private_body','secret')):
                bad=deepcopy(value);bad[key]=change;bads.append(upload.encoded(bad))
            bad=deepcopy(value);bad['upload_response']['request']['size']=56.0;bads.append(upload.encoded(bad))
            bad=deepcopy(value);bad['context']['input_id']=bad['context']['parent_id'];bads.append(upload.encoded(bad))
            for bad in bads:
                with self.assertRaises(RuntimeError):upload.validate_observation(bad,context=context)
            wrong=dict(context,device_id='3'*32)
            with self.assertRaises(RuntimeError):upload.validate_observation(raw,context=wrong)

    def test_unrelated_routes_and_unknown_query_or_scope_values_are_never_coerced_or_hashed(self):
        class Unknown:
            def __str__(self):raise AssertionError('str')
            def __repr__(self):raise AssertionError('repr')
            def __bool__(self):raise AssertionError('bool')
            def __eq__(self,other):raise AssertionError('eq')
            def __hash__(self):raise AssertionError('hash')
        with prepared() as (root,context):
            marker=object();writer=Mock();observer=upload.ObserveUpload(service(1,response_messages(),marker),root,owner_run='12345-1',writer=writer)
            scope=dict(type='http',method='GET',path='/sync/v1/handshake',query_string=b'',headers=Unknown())
            self.assertIs(invoke(observer,request_messages(),scope=scope)[0],marker);self.assertFalse(observer.used)
            scope.update(method='POST',path='/sync/v1/vaults/'+'1'*32+'/uploads',query_string=Unknown())
            self.assertIs(invoke(observer,request_messages(),scope=scope)[0],marker);writer.assert_not_called()
            for key in ('schema','owner_run','device_id','parent_metadata_canonical_sha256'):
                bad=dict(context);bad[key]=Unknown()
                with self.assertRaises(RuntimeError):upload.validate_context(bad)
            bad=parent();bad['phase']='cycle-quota'
            with self.assertRaises(RuntimeError):upload.context_from_parent(bad,owner_run='12345-1',remote_vault='1'*32,device_id='2'*32)

    def test_disconnect_is_only_a_closed_post_body_signal_not_client_receipt_or_a_body_fallback(self):
        for complete,extra,accepted in ((True,False,True),(True,True,False),(False,False,False)):
            with self.subTest(complete=complete,extra=extra),prepared() as (root,context):
                messages=request_messages(complete=complete)
                signal=dict(type='http.disconnect')
                if extra:signal['private']='not retained'
                messages.append(signal)
                marker=object();observer=upload.ObserveUpload(service(2,response_messages(),marker),root,owner_run='12345-1')
                result,calls,*_=invoke(observer,messages)
                self.assertIs(result,marker);self.assertEqual(calls,['receive','receive','send','send'])
                self.assertEqual((root/upload.OBSERVATION).exists(),accepted)
                if accepted:
                    value=upload.validate_observation((root/upload.OBSERVATION).read_bytes(),context=context)
                    self.assertFalse(value['client_received_or_applied_acknowledgement'])


if __name__=='__main__':
    unittest.main()
