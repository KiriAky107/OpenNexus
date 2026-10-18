"""Quota contracts using explicit synthetic data; no real provider/device claim."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import asyncio,hashlib,json,os,tempfile,unittest,yaml
import android_auth_fixture as auth
import android_sync_fixture as fixture
from android_sync_quota import validate_quota_rejection,QUOTA
from android_workflow_gate import admitted
OP='0c6a71e8-25d7-4b44-af5e-ed4f4adf236e';OLD='1b6a71e8-25d7-4b44-af5e-ed4f4adf236e';FILE='2c6a71e8-25d7-4b44-af5e-ed4f4adf236e';DEVICE='a'*32

def scene():
    raw=b'x'*99;digest=hashlib.sha256(raw).hexdigest()
    rows=[dict(operation_id=key,file_id=FILE,revision=1,path='runtime-note.md',hash=digest,operation='put',state=state,payload_size=0)
        for key,state in ((OLD,'archived'),(OP,'pending'))]
    before=dict(binding=dict(id='original',remote_vault='b'*32,state='active',cursor=0),association={'keep':'original'},
        outbox=rows,payloads=[dict(operation_id=key,hash=digest,size=len(raw)) for key in (OLD,OP)],
        jobs=[],attempts=[],issuers=[],heads=[],inbox=[],conflicts=[])
    after=deepcopy(before);after['outbox'][1]['state']='queued'
    after['jobs']=[dict(operation_id=OP,file_id=FILE,path='runtime-note.md',hash=digest,size=len(raw),state='pending',remote_revision=None,error=None)]
    after['attempts']=[dict(operation_id=OP,attempts=1,outcome='failed',error='QUOTA_EXCEEDED')]
    after['issuers']=[dict(operation_id=OP,device_id=DEVICE)]
    progress=dict(device_id=DEVICE,committed_sequence=None,committed_at=None,offered_sequence=0,offered_at=1)
    provider=dict(vault=dict(id='b'*32,quota=QUOTA,used=0,sequence=0),rows={key:[] for key in ('revisions','files','objects','uploads')},delivery=dict(counts={},events=[]))
    provider['rows']['device_progress']=[progress]
    remote=deepcopy(provider);remote['rows']['device_progress'][0]['offered_at']=2
    remote['delivery']=dict(counts={'changes:200':1,'upload:413':1},events=[dict(action=kind,service_status=status,delivery_status=status,fault_operation=None) for kind,status in (('changes',200),('upload',413))])
    return before,after,provider,remote,raw

class QuotaContract(unittest.TestCase):
    def test_known_quota_failure_keeps_original_pending_identity_payload_and_has_no_commit_or_apply_ack(self):
        before,after,provider,remote,raw=scene()
        proof=validate_quota_rejection(before,after,provider,remote,DEVICE,raw,raw)
        self.assertEqual(proof['actual_failed_attempt_operation_id'],OP)
        self.assertTrue(proof['actual_original_pending_job_and_payload_retained'])
        self.assertTrue(proof['original_pending_job_and_payload_retained'])
        self.assertFalse(proof['unchanged_native_boundary'])
        self.assertEqual(proof['preserved_archived_operation_ids'],[OLD])

    def test_loss_ack_replacement_duplicate_attempt_or_wrong_issuer_is_rejected(self):
        for kind in ('spool','ack','id','attempt','issuer','content','cursor'):
            before,after,provider,remote,raw=scene();observed=raw
            if kind=='spool':after['payloads'].pop()
            elif kind=='ack':after['jobs'][0]['state']='acked'
            elif kind=='id':after['jobs'][0]['operation_id']=OLD
            elif kind=='attempt':after['attempts'][0]['attempts']=2
            elif kind=='issuer':after['issuers'][0]['device_id']='f'*32
            elif kind=='content':observed=b'changed'
            else:after['binding']['cursor']=1
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):
                validate_quota_rejection(before,after,provider,remote,DEVICE,raw,observed)

    def test_wrong_quota_fabricated_delivery_success_or_provider_write_is_rejected(self):
        for kind in ('quota','status','fault','extra','reservation','commit','client_ack'):
            before,after,provider,remote,raw=scene()
            if kind=='quota':provider['vault']['quota']=1000
            elif kind=='status':remote['delivery']['events'][-1]['service_status']=200
            elif kind=='fault':remote['delivery']['events'][-1]['fault_operation']=OP
            elif kind=='extra':remote['delivery']['counts']['upload:413']=2
            elif kind=='reservation':remote['rows']['uploads']=[{'id':'unexpected'}]
            elif kind=='commit':remote['vault']['sequence']=1
            else:remote['rows']['device_progress'][0]['committed_sequence']=1
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):
                validate_quota_rejection(before,after,provider,remote,DEVICE,raw,raw)

    def test_only_explicit_quota_proxy_tracks_one_upload_status_without_changing_response(self):
        async def run():
            with tempfile.TemporaryDirectory() as d:
                owner=Path(d);control=fixture.initialize_control(owner);vault='b'*32;fixture.freeze_target(owner,vault)
                calls=[];sent=[]
                async def app(scope,receive,send):
                    calls.append(scope['path']);await send({'type':'http.response.start','status':413});await send({'type':'http.response.body','body':b'unit unchanged'})
                async def receive():return {'type':'http.request','body':b'unit','more_body':False}
                async def send(message):sent.append(message)
                proxy=fixture.TargetedDeliveryProxy(app,owner,uploads=True)
                await proxy(dict(type='http',method='POST',path='/sync/v1/vaults/'+vault+'/uploads'),receive,send)
                self.assertEqual(len(calls),1);self.assertEqual(sent[-1]['body'],b'unit unchanged')
                counts=auth.read_json(control/'request-counts.json');self.assertEqual(counts['counts'],{'upload:413':1})
                self.assertEqual(fixture.routes(vault),{('POST','/sync/v1/vaults/'+vault+'/revisions'):'revision',('GET','/sync/v1/vaults/'+vault+'/changes'):'changes'})
        asyncio.run(run())

    def test_named_case_routes_only_to_its_real_provider_and_reused_apk(self):
        root=Path(__file__).resolve().parents[1];workflow=yaml.load((root/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        for name in ('debug-apk','desktop-startup','desktop-pair','android-desktop-pair'):
            self.assertFalse(admitted(workflow['jobs'][name]['if'],'sync-quota',reuse='39112675356',build='skipped'))
        steps=workflow['jobs']['emulator-runtime']['steps']
        preflight=next(s for s in steps if s.get('name')=='Validate the immutable reuse selection')
        code=preflight['run'].split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        env=dict(REUSE_RUN='39112675356',REUSE_COMMIT='20af3d2cad9da2350f0e775f0e5605630f834443',RUNTIME_SCOPE='sync-quota',WEBVIEW_PREVIEW='true',WEBVIEW_CYCLE='true')
        with patch.dict(os.environ,env,clear=True):exec(compile(code,'quota-workflow','exec'),{})
        with patch.dict(os.environ,dict(env,REVOCATION_REFRESH_ONLY='true'),clear=True),self.assertRaises(AssertionError):exec(compile(code,'quota-workflow','exec'),{})
        selected=next(s for s in steps if 'scripts/android_sync_runtime.py' in s.get('run',''))
        self.assertTrue(admitted(selected['if'],'sync-quota'));self.assertIn("'sync-quota' && '--quota'",selected['run'])
        generic=next(s for s in steps if 'scripts/android_runtime_probe.py --commit' in s.get('run',''))
        self.assertFalse(admitted(generic['if'],'sync-quota'))

if __name__=='__main__':unittest.main()
