"""Synthetic Linux stage boundaries; no ADB, cloud credential or provider IO."""
from contextlib import contextmanager
from copy import deepcopy
import io
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from android_desktop_handoff import HandoffError
from android_product_pair_evidence import sha
from android_product_pair_linux import context_and_scope,provider_bodies,returned_files
from android_product_pair_stages import IMAGE_PATH


class LinuxProductStages(unittest.TestCase):
    def test_local_stage_refuses_before_artifact_package_or_output_access(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True),patch('android_product_pair_linux.owned_root') as owner:
            args=SimpleNamespace(serial='emulator-5562',output=Path(folder)/'not-created')
            with self.assertRaises(RuntimeError):context_and_scope(args)
            owner.assert_not_called();self.assertEqual(list(Path(folder).iterdir()),[])

    def test_S3_original_reads_are_bounded_and_hash_checked_without_creating_or_deleting_objects(self):
        bodies={'runtime-note.md':b'note','peer.canvas':b'canvas',IMAGE_PATH:b'image'}
        files=[{'path':p,'bytes':len(b),'sha256':sha(b)} for p,b in bodies.items()]
        for corrupt in (False,True):
            store=SimpleNamespace(client=SimpleNamespace(close=Mock()));calls=[]
            @contextmanager
            def opened(key):
                calls.append(key);digest=key.split('/')[1]
                body=next(b for b in bodies.values() if sha(b)==digest)
                yield io.BytesIO(body+(b'X' if corrupt else b''))
            store.open=opened
            package=ModuleType('sync_server');module=ModuleType('sync_server.storage');module.S3Objects=Mock(return_value=store)
            config={'environment':{'SYNC_S3_ENDPOINT':'http://127.0.0.1:19072'},'bucket':'owned-test'}
            with patch.dict(sys.modules,{'sync_server':package,'sync_server.storage':module}),\
                 patch('android_product_pair_linux.fixed_service',return_value=config):
                if corrupt:
                    with self.assertRaisesRegex(HandoffError,'S3_ORIGINAL_BYTES_MISMATCH'):provider_bodies(Path('/owned'),files,'e'*32)
                else:self.assertEqual(provider_bodies(Path('/owned'),files,'e'*32),bodies)
            store.client.close.assert_called_once()
            self.assertTrue(all(key.startswith('e'*32+'/') for key in calls))
            self.assertEqual(len(calls),1 if corrupt else 3)

    def test_return_matches_actual_native_original_issuer_ACK_and_keeps_unchanged_PNG_metadata(self):
        device='d'*32;files=[];provider={'rows':{'revisions':[]}};native={'jobs':[],'attempts':[],'issuers':[]}
        bodies={'runtime-note.md':b'Android note','peer.canvas':b'Android canvas',IMAGE_PATH:b'original image'}
        for seq,(path,body) in enumerate(bodies.items(),1):
            original={'path':path,'sequence':seq,'file_id':'file-'+str(seq),'operation_id':'Desktop-'+str(seq),'device_id':'c'*32,
                      'base_revision':0,'sha256':sha(body),'bytes':len(body)};files.append(original)
            if path==IMAGE_PATH:continue
            row={'path':path,'sequence':seq+3,'file_id':original['file_id'],'operation_id':'Android-'+str(seq),'device_id':device,
                 'base_revision':seq,'hash':sha(body),'size':len(body),'operation':'put'}
            provider['rows']['revisions'].append(row)
            native['jobs'].append({**{k:row[k] for k in ('path','file_id','operation_id','hash','size')},'remote_revision':seq+3,'state':'acked','error':None})
            native['issuers'].append({'operation_id':row['operation_id'],'device_id':device})
            native['attempts'].append({'operation_id':row['operation_id'],'attempts':1,'outcome':'succeeded','error':None})
        desktop={'files':files};result=returned_files(provider,native,bodies,desktop,device)
        self.assertEqual(result[-1],files[-1])
        for key,value in [('state','pending'),('file_id','replacement-file'),('path','other.md'),('remote_revision',99)]:
            changed=deepcopy(native);changed['jobs'][0][key]=value
            with self.subTest(key=key),self.assertRaises(HandoffError):returned_files(provider,changed,bodies,desktop,device)
        changed=deepcopy(native);changed['issuers'][0]['device_id']='0'*32
        with self.assertRaises(HandoffError):returned_files(provider,changed,bodies,desktop,device)


if __name__=='__main__':unittest.main()
