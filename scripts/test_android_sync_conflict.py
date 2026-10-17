from copy import deepcopy
import hashlib,json,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from android_sync_conflict import LOCAL,DIRTY,REMOTE,COPY,IDS,ConflictMixin,validate_retained_conflict,validate_original_copy


class ConflictOriginals(unittest.TestCase):
    def conflict(self):
        original={'operation_id':'uploaded-original','file_id':'original-file','path':'runtime-note.md','state':'acked','remote_revision':1}
        first={'jobs':[original],'heads':[{'file_id':'original-file','revision':1,'path':'runtime-note.md','hash':'base'}],
            'attempts':[{'original':'attempt'}],'issuers':[{'device_id':'android'}],'association':{'frozen':'original'}}
        receipt={'vault_id':'1'*32,'sequence':2,'base_revision':1,'operation_id':'peer-original','file_id':'original-file',
            'path':'runtime-note.md','hash':hashlib.sha256(REMOTE).hexdigest(),'size':len(REMOTE),'device_id':'peer','operation':'put','fingerprint':'3'*64}
        native={**deepcopy(first),'binding':{'cursor':2,'remote_vault':'1'*32},'decisions':[],
            'conflicts':[{'sequence':2,'file_id':'original-file','local_path':'runtime-note.md','local_hash':hashlib.sha256(LOCAL).hexdigest(),'state':'open'}],
            'heads':[{'file_id':'original-file','revision':2,'path':'runtime-note.md','hash':receipt['hash']}],
            'inbox':[{'sequence':2,'revision':json.dumps(receipt),'state':'conflict'}]}
        native['jobs'].append({'operation_id':'saved-local-original','file_id':'original-file','path':'runtime-note.md',
            'hash':hashlib.sha256(LOCAL).hexdigest(),'size':len(LOCAL),'state':'conflict'})
        provider={'vault':{'sequence':2,'id':'1'*32},'rows':{'revisions':[{'original':'upload'},{k:v for k,v in receipt.items() if k!='vault_id'}]}}
        return first,native,provider,receipt

    def test_conflict_retains_saved_local_job_original_remote_head_and_distinct_incoming(self):
        first,native,provider,receipt=self.conflict()
        self.assertEqual(validate_retained_conflict(first,native,provider,receipt)['operation_id'],'saved-local-original')
        for key,value in (('file_id','replacement'),('base_revision',0),('device_id','android'),('hash','0'*64),('size',0)):
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                validate_retained_conflict(first,native,provider,dict(receipt,**{key:value}))

    def test_a_generated_remote_page_or_silently_accepted_local_job_is_not_a_conflict(self):
        first,native,provider,receipt=self.conflict()
        for field,value in (('heads',first['heads']),('inbox',[]),('decisions',[{'unexpected':'approval'}]),('conflicts',[])):
            changed=deepcopy(native);changed[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):validate_retained_conflict(first,changed,provider,receipt)
        changed=deepcopy(native);changed['jobs'][1]['state']='acked'
        with self.assertRaises(RuntimeError):validate_retained_conflict(first,changed,provider,receipt)
        changed=deepcopy(native);changed['jobs'][1]['hash']=hashlib.sha256(DIRTY).hexdigest()
        with self.assertRaises(RuntimeError):validate_retained_conflict(first,changed,provider,receipt)

    def test_local_capture_after_inbox_can_remain_pending_but_requires_the_exact_open_conflict(self):
        first,native,provider,receipt=self.conflict()
        native['jobs'][1].update(state='pending',remote_revision=None)
        self.assertEqual(validate_retained_conflict(first,native,provider,receipt),native['jobs'][1])
        for change in ({'conflicts':[]},{'inbox':[]},{'heads':first['heads']}):
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                validate_retained_conflict(first,dict(native,**change),provider,receipt)
        for state in ('uploading','committing','acked','archived'):
            changed=deepcopy(native);changed['jobs'][1]['state']=state
            with self.subTest(state=state),self.assertRaises(RuntimeError):
                validate_retained_conflict(first,changed,provider,receipt)
        changed=deepcopy(native);changed['jobs'][1]['remote_revision']=2
        with self.assertRaises(RuntimeError):validate_retained_conflict(first,changed,provider,receipt)

    def resolved(self):
        first,native,provider,receipt=self.conflict()
        frozen={'sequence':2,'choice':'copy','destination':COPY,'fingerprint':'4'*64}
        decision={**frozen,'expected_hash':hashlib.sha256(LOCAL).hexdigest(),'state':'completed',**{key:str(uuid.uuid4()) for key in IDS}}
        native['decisions']=[decision];native['conflicts'][0]['state']='resolved'
        return native,frozen,receipt

    def test_original_copy_requires_all_five_native_ids_and_exact_frozen_content(self):
        native,frozen,receipt=self.resolved();value=validate_original_copy(native,frozen,receipt)
        self.assertEqual(len({value[key] for key in IDS}),5)
        for key,new in (('choice','remote'),('destination','replacement.md'),('expected_hash',hashlib.sha256(DIRTY).hexdigest()),
                ('fingerprint','5'*64),('state','pending'),('sequence',3),('copy_id','not-an-id')):
            changed=deepcopy(native);changed['decisions'][0][key]=new
            with self.subTest(key=key),self.assertRaises(RuntimeError):validate_original_copy(changed,frozen,receipt)
        changed=deepcopy(native);changed['decisions'][0]['copy_id']=changed['decisions'][0]['operation_id']
        with self.assertRaises(RuntimeError):validate_original_copy(changed,frozen,receipt)

    def test_confirmed_copy_requires_original_resolved_head_not_only_a_completed_receipt(self):
        native,frozen,receipt=self.resolved()
        for field,value in (('heads',[]),('conflicts',[]),('binding',{'cursor':1})):
            changed=deepcopy(native);changed[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):validate_original_copy(changed,frozen,receipt)
        changed=deepcopy(native);changed['heads'][0]['hash']=hashlib.sha256(LOCAL).hexdigest()
        with self.assertRaises(RuntimeError):validate_original_copy(changed,frozen,receipt)

    def test_unowned_device_and_unrelated_driver_record_are_refused_before_shell_or_UI(self):
        probe=ConflictMixin();probe.args=SimpleNamespace(serial='physical-phone')
        with patch.object(ConflictMixin,'host_snapshot',create=True) as snapshot:
            with self.assertRaises(RuntimeError):probe.conflict_copy(None,None,None)
            snapshot.assert_not_called()
        with patch.object(ConflictMixin,'shell',create=True) as shell:
            with self.assertRaises(RuntimeError):probe.original_conflict_driver('unrelated-private-input')
            shell.assert_not_called()

    def editing_probe(self):
        probe=ConflictMixin();events=[];active={'ime':False}
        probe.args=SimpleNamespace(serial='emulator-5562');probe.owner=Path('owned-unit');probe.output=Path('owned-unit')
        probe.host_snapshot=lambda label:self.conflict()[0]
        probe.snapshot=lambda label:{'own':'auth'}
        probe.ciphertext_fingerprints=lambda:{'own':'ciphertext'}
        def enable():events.append('enable');active['ime']=True
        def restore():events.append('restore');active['ime']=False
        def phase(label):
            self.assertTrue(active['ime'],'Separate test IME must be selected before actual conflict editing')
            events.append(label)
        probe.enable_test_ime=enable;probe.restore_test_ime=restore;probe.phase=phase
        probe.original=lambda name:LOCAL
        probe.draft_rows=lambda:[['runtime-note.md',DIRTY.decode(),2,1]]
        return probe,events

    def test_each_conflict_editor_phase_selects_and_restores_its_own_IME(self):
        probe,events=self.editing_probe()
        class ReachedPeer(Exception):pass
        with patch('android_sync_conflict.require_hosted_emulator'),patch('android_sync_conflict.atomic_json'),\
             patch('android_sync_conflict.Peer',side_effect=ReachedPeer):
            with self.assertRaises(ReachedPeer):probe.conflict_copy(None,None,None)
        self.assertEqual(events,['enable','conflict-local-save','restore','enable','conflict-dirty-draft','restore'])

    def test_IME_enable_or_editor_failure_restores_once_without_retry_or_peer_write(self):
        for failure in ('enable','phase'):
            probe,events=self.editing_probe()
            def fail(*args):
                events.append('original-failure');raise RuntimeError('original controlled editor failure')
            if failure=='enable':probe.enable_test_ime=fail
            else:probe.phase=fail
            with self.subTest(failure=failure),patch('android_sync_conflict.require_hosted_emulator'),\
                 patch('android_sync_conflict.atomic_json'),patch('android_sync_conflict.Peer') as peer:
                with self.assertRaisesRegex(RuntimeError,'^original controlled editor failure$'):
                    probe.conflict_copy(None,None,None)
                peer.assert_not_called()
            self.assertEqual(events,['original-failure','restore'] if failure=='enable' else ['enable','original-failure','restore'])

    def test_conflict_edit_helper_refuses_unowned_devices_and_unrelated_phases_before_IME(self):
        probe=ConflictMixin();probe.args=SimpleNamespace(serial='physical-phone')
        with patch.object(probe,'enable_test_ime',create=True) as enable:
            with self.assertRaises(RuntimeError):probe.conflict_edit_phase('conflict-local-save')
            with self.assertRaises(RuntimeError):probe.conflict_edit_phase('unrelated')
            enable.assert_not_called()


if __name__=='__main__':unittest.main()
