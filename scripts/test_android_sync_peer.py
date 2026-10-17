import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from android_sync_peer import Peer


class OriginalPeerChecks(unittest.TestCase):
    def peer(self,root,*,lose_commit=False,retain_commit=True):
        peer=Peer.__new__(Peer);peer.root=root;peer.vault='1'*32;peer.device='2'*32
        peer.prefix='/sync/v1/vaults/'+peer.vault
        peer.setup=SimpleNamespace(endpoint='https://owned-origin.trycloudflare.com',session=lambda:{'device_id':peer.device,'access_token':'t'*40})
        calls=[];remote=[];first=[True];uploaded=[]
        def request(method,path,body=None):
            calls.append((method,path,body))
            if method=='GET':return {'status':200,'body':{'items':list(remote),'cursor':len(remote),'boundary':len(remote),'has_more':False}}
            if path==peer.prefix+'/uploads':return {'status':200,'body':{'complete':False,'upload_id':'3'*32,'offset':0}}
            if method=='PUT':uploaded.append(body);return {'status':200,'body':{'offset':len(body)}}
            if path.endswith('/complete'):return {'status':200,'body':{'complete':True,'content_hash':hashlib.sha256(uploaded[0]).hexdigest()}}
            receipt={**body,'hash':body['content_hash'],'device_id':peer.device,'vault_id':peer.vault,'sequence':1}
            if first[0]:
                first[0]=False
                if retain_commit:remote.append(receipt)
                if lose_commit:raise TimeoutError('Synthetic dropped original response after or before commit')
            if not remote:remote.append(receipt)
            return {'status':200,'body':receipt}
        peer.request=Mock(side_effect=request)
        return peer,calls,remote

    def test_one_original_upload_and_commit_are_not_repeated_when_receipt_is_known(self):
        with tempfile.TemporaryDirectory() as directory:
            peer,calls,_=self.peer(Path(directory));content='真实原文 😀'.encode()
            receipt=peer.publish('new-note','peer-note.md',content)
            self.assertEqual([m for m,_,_ in calls],['GET','POST','PUT','POST','POST'])
            original_count=len(calls);self.assertEqual(peer.publish('new-note','peer-note.md',content),receipt)
            self.assertEqual(len(calls),original_count)
            intent=json.loads((peer.root/'new-note/revision-intent.json').read_text())
            self.assertEqual(receipt['operation_id'],intent['body']['operation_id'])
            self.assertEqual((peer.root/'new-note/original.bin').read_bytes(),content)
            attempt=json.loads((peer.root/'new-note/revision-attempted.json').read_text())
            self.assertEqual(len(attempt['actual_lookup_frames']),1)
            lookup=attempt['actual_lookup_frames'][0]
            self.assertEqual(hashlib.sha256((peer.root/lookup['file']).read_bytes()).hexdigest(),lookup['sha256'])
            self.assertFalse(json.loads((peer.root/lookup['file']).read_text())['client_apply_acknowledged'])
            with self.assertRaises(RuntimeError):peer.publish('new-note','peer-note.md',b'changed source')
            self.assertEqual(len(calls),original_count)

    def test_unknown_committed_revision_is_found_by_actual_changes_without_another_POST(self):
        with tempfile.TemporaryDirectory() as directory:
            peer,calls,_=self.peer(Path(directory),lose_commit=True)
            with self.assertRaises(TimeoutError):peer.publish('original','peer-note.md',b'original')
            receipt=peer.publish('original','peer-note.md',b'original')
            self.assertEqual(sum(m=='POST' and p.endswith('/revisions') for m,p,_ in calls),1)
            self.assertEqual(receipt['sequence'],1);self.assertEqual(calls[-1][0],'GET')

    def test_unknown_missing_revision_requires_explicit_same_body_retry_after_GET(self):
        with tempfile.TemporaryDirectory() as directory:
            peer,calls,_=self.peer(Path(directory),lose_commit=True,retain_commit=False)
            with self.assertRaises(TimeoutError):peer.publish('original','peer-note.md',b'original')
            first=[body.copy() for method,path,body in calls if method=='POST' and path.endswith('/revisions')][0]
            with self.assertRaisesRegex(RuntimeError,'COMMIT_UNKNOWN'):peer.publish('original','peer-note.md',b'original')
            self.assertEqual(calls[-1][0],'GET')
            receipt=peer.publish('original','peer-note.md',b'original',retry_original=True)
            bodies=[body for method,path,body in calls if method=='POST' and path.endswith('/revisions')]
            self.assertEqual(bodies,[first,first]);self.assertEqual(receipt['operation_id'],first['operation_id'])
            self.assertEqual(sum(method=='PUT' for method,_,_ in calls),1)

    def test_unknown_upload_allocation_cannot_create_another_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            peer,_,_=self.peer(Path(directory));peer.request.side_effect=lambda method,path,body=None: (
                {'status':200,'body':{'items':[],'cursor':0,'boundary':0,'has_more':False}} if method=='GET' else (_ for _ in ()).throw(TimeoutError()))
            with self.assertRaises(TimeoutError):peer.publish('upload','peer-note.md',b'original')
            with self.assertRaisesRegex(RuntimeError,'UPLOAD_RESULT_UNKNOWN'):peer.publish('upload','peer-note.md',b'original',retry_original=True)
            self.assertEqual(sum(call.args[0]=='POST' for call in peer.request.call_args_list),1)

    def test_changed_issuing_device_or_vault_cannot_be_reconciled_as_original(self):
        with tempfile.TemporaryDirectory() as directory:
            peer,calls,remote=self.peer(Path(directory),lose_commit=True)
            with self.assertRaises(TimeoutError):peer.publish('original','peer-note.md',b'original')
            remote[0]['device_id']='4'*32
            with self.assertRaisesRegex(RuntimeError,'RECEIPT_CHANGED'):peer.publish('original','peer-note.md',b'original')
            self.assertEqual(sum(m=='POST' and p.endswith('/revisions') for m,p,_ in calls),1)

    def test_route_and_fixture_path_guards_refuse_before_network(self):
        peer=Peer.__new__(Peer);peer.prefix='/sync/v1/vaults/'+'1'*32
        with patch('android_sync_peer.http.client.HTTPSConnection') as connection:
            for method,path in (('GET','https://elsewhere/'),('POST',peer.prefix+'/auth/sessions'),('DELETE',peer.prefix+'/uploads/'+'2'*32)):
                with self.assertRaisesRegex(RuntimeError,'ROUTE_INVALID'):peer.request(method,path)
            connection.assert_not_called()


if __name__=='__main__':unittest.main()
