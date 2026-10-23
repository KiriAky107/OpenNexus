"""Actual local SQLite/CAS and original host recorder checks; no device/network."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shlex
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import android_release_sync_mutations as runtime
import android_release_sync_mutation_snapshot as snapshots
import android_release_sync_read_snapshot as download
import android_sync_host_touch as host
import test_android_release_sync_read as seed
import test_android_sync_host_touch as touch_fixture
from android_release_upgrade import ReleaseUpgrade

DEVICE='d'*32
PEER_DEVICE='e'*32
def uid(number):return '00000000-0000-4000-8000-'+str(number).zfill(12)


def receipts():
    bodies={runtime.ASSET:runtime.IMAGE_BYTES,runtime.PEER[0]:runtime.PEER_NOTE,runtime.PEER[1]:runtime.PEER_CANVAS}
    return [dict(operation_id=uid(10+i),file_id=uid(20+i),base_revision=0,path=path,operation='put',hash=hashlib.sha256(body).hexdigest(),
        size=len(body),sequence=i+1,vault_id='a'*32,device_id=PEER_DEVICE,fingerprint='f'*64) for i,(path,body) in enumerate(bodies.items())]


def mutation(prior,deleted):
    return dict(prior,operation_id=uid(40 if deleted else 30),base_revision=prior['sequence'],path=runtime.RENAMED,
        sequence=5 if deleted else 4,operation='delete' if deleted else 'put',hash=None if deleted else prior['hash'],size=0 if deleted else prior['size'])


def revision(receipt):
    return {k:receipt[k] for k in ('operation_id','file_id','base_revision','path','operation','hash','size','sequence','vault_id')}


def fixture(root,stage):
    raw=seed.fixture(root,stage not in ('empty','associated'),stage!='empty')
    originals=receipts();chain=list(originals);note=next(v for v in originals if v['path']==runtime.PEER[0])
    if stage in ('renamed','deleted','cold'):chain.append(mutation(note,False))
    if stage in ('deleted','cold'):chain.append(mutation(chain[-1],True))
    if stage not in ('empty','associated'):
        if stage in ('renamed','deleted','cold'):(raw/'vault'/runtime.PEER[0]).rename(raw/'vault'/runtime.RENAMED)
        if stage in ('deleted','cold'):(raw/'vault'/runtime.RENAMED).unlink()
        with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
            for receipt in originals:
                path=runtime.RENAMED if receipt['path']==runtime.PEER[0] and stage in ('renamed','deleted','cold') else receipt['path']
                dead=int(receipt['path']==runtime.PEER[0] and stage in ('deleted','cold'))
                db.execute('UPDATE files SET id=?,path=?,revision=?,deleted=? WHERE path=?',(receipt['file_id'],path,4 if dead else 3 if path==runtime.RENAMED else 1,dead,receipt['path']))
            latest={r['file_id']:r for r in chain}
            for receipt in latest.values():db.execute('INSERT INTO sync_heads VALUES(?,?,?,?,?)',(seed.original_fixture.ID,receipt['file_id'],receipt['sequence'],receipt['path'],receipt['hash'] or ''))
            for r in chain:db.execute('INSERT INTO sync_inbox VALUES(?,?,?,?,?,?)',(seed.original_fixture.ID,r['sequence'],json.dumps(revision(r)),uid(100+r['sequence']),uid(200+r['sequence']),'applied'))
            db.execute('UPDATE sync_bindings SET cursor=?',(len(chain),));db.commit()
    seed.metadata(raw);return raw,chain


def provider(chain):
    latest={r['file_id']:r for r in chain}
    return dict(vault=dict(id='a'*32,sequence=len(chain)),rows=dict(revisions=deepcopy(chain),
        files=[dict(file_id=r['file_id'],path_key=r['path'].casefold(),sequence=r['sequence'],deleted=int(r['operation']=='delete')) for r in latest.values()],
        device_progress=[dict(device_id=DEVICE,offered_sequence=len(chain),committed_sequence=None)]))


class MutationChecks(unittest.TestCase):
    def test_actual_SQLite_empty_download_rename_tombstone_and_cold_closed_sets(self):
        for stage in snapshots.STAGES:
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=fixture(root,stage);before={p.relative_to(raw).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.rglob('*') if p.is_file()}
                value=snapshots.project_mutations(raw,root/'copy',stage=stage)
                self.assertEqual(len(value['files']),0 if stage in ('empty','associated') else 3)
                self.assertEqual(value['all_clean_drafts'],[]);self.assertEqual(value['all_save_count'],0)
                self.assertNotIn('never-read-this',json.dumps(value));self.assertFalse(value['credentials_selected'])
                self.assertEqual(before,{p.relative_to(raw).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.rglob('*') if p.is_file()})
                if stage=='deleted':
                    self.assertEqual(value['absent_paths'],[runtime.PEER[0],runtime.RENAMED])
                    self.assertEqual(set(value['raw_sha256']) & {'vault/'+runtime.RENAMED,'vault/'+runtime.PEER[0]},set())
                    with self.assertRaises(RuntimeError):download.project_download(raw,root/'old-contract',downloaded=True)

    def test_real_original_CAS_same_identity_delete_head_and_cold_projection(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);values={};chains={}
            for stage in ('downloaded','renamed','deleted','cold'):
                base=root/stage;base.mkdir();raw,chain=fixture(base,stage);values[stage]=snapshots.project_mutations(raw,base/'copy',stage=stage);chains[stage]=chain
            runtime.validate_mutation(values['downloaded'],values['renamed'],chains['renamed'][-1],chains['renamed'][1],provider(chains['renamed']),DEVICE,deleted=False)
            runtime.validate_mutation(values['renamed'],values['deleted'],chains['deleted'][-1],chains['deleted'][-2],provider(chains['deleted']),DEVICE,deleted=True)
            snapshots.preserve_cold_mutation(values['deleted'],values['cold'])
            for mode in ('CAS','issuer','file-id','head','inbox','local-deleted','provider-tombstone','unrelated-bytes'):
                after=deepcopy(values['deleted']);receipt=deepcopy(chains['deleted'][-1]);pg=provider(chains['deleted'])
                if mode=='CAS':receipt['base_revision']=2
                elif mode=='issuer':receipt['device_id']=DEVICE
                elif mode=='file-id':receipt['file_id']=uid(999)
                elif mode=='head':after['native']['heads'][-1]['hash']='wrong'
                elif mode=='inbox':after['native']['inbox'][-1]['state']='pending'
                elif mode=='local-deleted':next(v for v in after['files'] if v['path']==runtime.RENAMED)['deleted']=0
                elif mode=='provider-tombstone':next(v for v in pg['rows']['files'] if v['file_id']==receipt['file_id'])['deleted']=0
                elif mode=='unrelated-bytes':after['raw_sha256']['vault/'+runtime.ASSET]='f'*64
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):runtime.validate_mutation(values['renamed'],after,receipt,chains['deleted'][-2],pg,DEVICE,deleted=True)

    def test_unapproved_draft_Save_outbox_false_tombstone_and_wrong_content_rejected(self):
        for mode in ('draft','Save','outbox','not-deleted','wrong-dead-hash','foreign-file','not-stage'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);raw,_=fixture(root,'deleted')
                if mode in ('draft','Save'):
                    with closing(sqlite3.connect(raw/snapshots.DATABASES[0])) as db:
                        if mode=='draft':db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',('unexpected','id','foreign.md','x',0,0,'content'))
                        else:db.execute('INSERT INTO save_intents(operation_id,path,state) VALUES(?,?,?)',('save','foreign.md','prepared'))
                        db.commit()
                else:
                    with closing(sqlite3.connect(raw/snapshots.DATABASES[1])) as db:
                        if mode=='outbox':db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?)',('id','file',1,runtime.RENAMED,'x','delete','pending'))
                        if mode=='not-deleted':db.execute('UPDATE files SET deleted=0 WHERE path=?',(runtime.RENAMED,))
                        if mode=='wrong-dead-hash':db.execute('UPDATE files SET hash=? WHERE path=?',('wrong',runtime.RENAMED))
                        if mode=='foreign-file':db.execute('INSERT INTO files VALUES(?,?,?,?,?)',('foreign','foreign.md','x',1,0))
                        db.commit()
                seed.metadata(raw)
                with self.assertRaises(RuntimeError):snapshots.project_mutations(raw,root/'copy',stage='foreign' if mode=='not-stage' else 'deleted')

    def test_exact_driver_phase_matrix_keeps_original_default_closed(self):
        for entry in ('ReleaseSyncRuntimeProbe','ReleaseSyncMutationRuntimeProbe','RuntimeProbe','ReleaseSyncRuntimeProbe-extra',''):
            for phase in ('cycle','cycle-incoming','cycle-rename','cycle-delete','cycle-stop','logout',None):
                expected=entry=='ReleaseSyncRuntimeProbe' and phase in ('cycle','cycle-incoming') or entry=='ReleaseSyncMutationRuntimeProbe' and phase in runtime.MUTATION_PHASES
                self.assertEqual(host.admitted_phase(entry,phase),expected)
        value=touch_fixture.ready();value['phase']='cycle-rename'
        with self.assertRaises(RuntimeError):host.validate_ready(value,'cycle-rename',1100)

    def test_new_mutation_touch_uses_actual_recorder_once_and_unknown_never_finishes(self):
        for phase in runtime.MUTATION_PHASES:
            for unknown in (False,True):
                with self.subTest(phase=phase,unknown=unknown),tempfile.TemporaryDirectory() as folder:
                    subject,_=touch_fixture.HostTouch().subject(folder);subject.driver_entry='ReleaseSyncMutationRuntimeProbe'
                    ready=touch_fixture.ready();ready['phase']=phase
                    original_shell=subject.shell
                    def shell(*args,**kwargs):
                        if args[:3]==('run-as',host.DRIVER,'sh'):return SimpleNamespace(returncode=0,stdout=json.dumps(ready).encode())
                        return original_shell(*args,**kwargs)
                    subject.shell=shell;subject.original_command=ReleaseUpgrade.original_command.__get__(subject)
                    def device(*args,**kwargs):
                        if unknown and args[:2]==('shell','-T') and args[2].startswith('input touchscreen'):
                            raise host.subprocess.TimeoutExpired(args,10,output=b'original-static')
                        return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
                    subject.device.side_effect=device;process=touch_fixture.Process()
                    process.communicate=Mock(side_effect=lambda timeout:(setattr(process,'returncode',process.returncode or 0) or ('OPENNEXUS_RELEASE_SYNC_PHASE_OK '+phase+'\n').encode(),b''))
                    with patch.object(host,'require_hosted_emulator'),patch.object(host.subprocess,'Popen',return_value=process),patch.object(host.ReleaseAuthBackground,'app_is_resumed',return_value=True):
                        if unknown:
                            with self.assertRaises(host.subprocess.TimeoutExpired):host.run_hosted_mutation_cycle(subject,phase,'mutation')
                        else:host.run_hosted_mutation_cycle(subject,phase,'mutation')
                    touches=[v for v in subject.device.call_args_list if v.args[:2]==('shell','-T') and v.args[2].startswith('input touchscreen')]
                    self.assertEqual(len(touches),1)
                    self.assertEqual((Path(folder)/'mutation-host-touch-finish-intent.json').exists(),not unknown)
                    self.assertIn('/.ReleaseSyncMutationRuntimeProbe',json.loads((Path(folder)/'mutation-instrument-intent.json').read_bytes())['arguments'][-1])

    def test_wrong_driver_rejected_before_launch_and_runtime_restores_identity_after_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,commands=touch_fixture.HostTouch().subject(folder)
            with self.assertRaises(RuntimeError):host.run_hosted_mutation_cycle(subject,'cycle-rename','mutation')
            subject.stop.assert_not_called();self.assertFalse(commands)
        subject=runtime.ReleaseSyncMutations.__new__(runtime.ReleaseSyncMutations)
        with patch.object(runtime,'run_hosted_mutation_cycle',side_effect=TimeoutError('unknown')) as command:
            with self.assertRaises(TimeoutError):subject.phase('cycle-delete','delete')
            command.assert_called_once();self.assertEqual(subject.driver_entry,'ReleaseSyncRuntimeProbe')

    def test_absence_is_exact_stopped_non_symlink_parent_metadata_not_a_failed_cat(self):
        subject=runtime.ReleaseSyncMutations.__new__(runtime.ReleaseSyncMutations);observer=SimpleNamespace(require_stopped=Mock())
        subject.original_command=Mock(return_value=SimpleNamespace(stdout=b'/data/user/0/cc.kronecker.opennexus/files/vault\n'))
        root='/data/user/0/cc.kronecker.opennexus/files/'
        result=subject.observe_absence(observer,root,'deleted','deleted','before')
        self.assertEqual([v['path'] for v in result],[runtime.PEER[0],runtime.RENAMED]);self.assertEqual(subject.original_command.call_count,2)
        for call in subject.original_command.call_args_list:
            commands=[shlex.split(v) for v in call.args[1][-1].split(' && ')]
            self.assertEqual(commands[0][:3],['test','!','-e']);self.assertEqual(commands[1][:3],['test','!','-L'])
            self.assertEqual(commands[-1],['realpath',root+'vault']);self.assertNotIn('cat',[v[0] for v in commands])
        subject.original_command.return_value.stdout=b'/outside\n'
        with self.assertRaises(RuntimeError):subject.observe_absence(observer,root,'deleted','deleted','after')


if __name__=='__main__':unittest.main()
