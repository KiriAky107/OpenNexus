from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
from android_runtime_probe import DRAFT
from android_sync_runtime import SyncProbe,read_original_object,validate_upload


class InitialSyncBoundaries(unittest.TestCase):
    def exchange(self):
        content=DRAFT.encode();digest=hashlib.sha256(content).hexdigest()
        job={'operation_id':'original-op','file_id':'original-file','path':'runtime-note.md',
            'hash':digest,'size':len(content),'remote_revision':1}
        remote={**{k:job[k] for k in ('operation_id','file_id','path','hash','size')},'device_id':'actual-android','sequence':1}
        native={'jobs':[job],'heads':[{'file_id':'original-file','revision':1,'hash':digest}]}
        provider={'rows':{'revisions':[remote]},'vault':{'sequence':1}}
        return provider,native

    def test_counts_alone_cannot_substitute_for_original_operation_and_device(self):
        provider,native=self.exchange();self.assertEqual(validate_upload(provider,native,'actual-android')['sequence'],1)
        for key,value in (('operation_id','other'),('device_id','setup-actor'),('file_id','other'),
                ('hash','0'*64),('size',0),('path','other.md'),('sequence',2)):
            changed=deepcopy(provider);changed['rows']['revisions'][0][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):validate_upload(changed,native,'actual-android')

    def test_duplicate_revisions_or_wrong_confirmed_head_are_not_success(self):
        provider,native=self.exchange()
        provider['rows']['revisions']*=2;native['jobs']*=2
        with self.assertRaises(RuntimeError):validate_upload(provider,native,'actual-android')
        provider,native=self.exchange();native['heads'][0]['revision']=0
        with self.assertRaises(RuntimeError):validate_upload(provider,native,'actual-android')

    def test_immutable_original_object_read_cannot_follow_a_redirect_or_accept_bad_bytes(self):
        raw=DRAFT.encode();digest=hashlib.sha256(raw).hexdigest()
        for status,body in ((200,raw),(302,raw),(200,b'changed')):
            connection=Mock();connection.getresponse.return_value=SimpleNamespace(status=status,read=lambda size:body)
            with patch('android_sync_runtime.http.client.HTTPSConnection',return_value=connection):
                if status==200 and body==raw:self.assertEqual(read_original_object('https://owned-origin.trycloudflare.com','1'*32,digest,'a'*40),raw)
                else:
                    with self.assertRaises(RuntimeError):read_original_object('https://owned-origin.trycloudflare.com','1'*32,digest,'a'*40)
            connection.request.assert_called_once();self.assertEqual(connection.request.call_args.args[0],'GET')
            connection.close.assert_called_once()

    def test_untrusted_original_object_target_rejects_before_credentials_or_network(self):
        with patch('android_sync_runtime.http.client.HTTPSConnection') as connect:
            for endpoint,vault,digest in (('http://owned-origin.trycloudflare.com','1'*32,'2'*64),
                    ('https://example.com','1'*32,'2'*64),('https://owned-origin.trycloudflare.com','../private','2'*64)):
                with self.assertRaises(RuntimeError):read_original_object(endpoint,vault,digest,'a'*40)
            connect.assert_not_called()

    def test_failed_sync_UI_retains_intent_and_never_replays_the_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=SyncProbe.__new__(SyncProbe);probe.output=Path(directory);probe.phases=[]
            probe.start_app=Mock();probe.shell=Mock(return_value=subprocess.CompletedProcess([],0,b'OPENNEXUS_SYNC_PHASE_FAILED stage=static\n'))
            with self.assertRaises(RuntimeError):probe.sync_phase('associate')
            probe.shell.assert_called_once();self.assertEqual(probe.phases,[])
            intent=json.loads((probe.output/'sync-dispatch-1.json').read_text())
            self.assertFalse(intent['server_or_native_operation_id']);self.assertFalse(intent['automatic_retry'])


if __name__=='__main__':unittest.main()
