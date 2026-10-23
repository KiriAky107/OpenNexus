"""Independent signed incoming peer rename/delete; no desktop or outbound GUI claim."""
import hashlib
import json
import os
import re
import shlex

from android_auth_runtime import active_intent
from android_release_auth import main
from android_release_observation import APP, DATABASES, StoppedReleaseObserver, exclusive
from android_release_sync import ReleaseSync, ReleasePeer, PEER_NOTE, PEER_CANVAS, UUID, validate_association, validate_incoming, read_original_object
from android_release_sync_read import ReleaseSyncRead
from android_release_sync_mutation_snapshot import ASSET, PEER, RENAMED, live_paths, absent_paths, project_mutations, preserve_cold_mutation
from android_runtime_probe import IMAGE_BYTES, require
from android_sync_host_touch import run_hosted_mutation_cycle
from android_sync_setup import Setup
from release_plan import atomic_json, digest

MUTATION_PHASES = ('cycle-rename', 'cycle-delete')


class MutationPeer(ReleasePeer):
    paths = frozenset(PEER + (ASSET, RENAMED))


def validate_mutation(before, after, receipt, prior, provider, device, *, deleted):
    require(type(deleted) is bool and receipt['operation'] == ('delete' if deleted else 'put')
            and receipt['file_id'] == prior['file_id'] and receipt['base_revision'] == prior['sequence']
            and receipt['path'] == RENAMED and receipt['device_id'] == prior['device_id'] != device
            and receipt['operation_id'] != prior['operation_id'] and receipt['sequence'] > before['native']['binding']['cursor'],
            'RELEASE_SYNC_MUTATION_ORIGINAL_CAS_CHANGED')
    require(receipt['hash'] == (None if deleted else prior['hash']) and receipt['size'] == (0 if deleted else prior['size']),
            'RELEASE_SYNC_MUTATION_CONTENT_CHANGED')
    a,b=before['native'],after['native']
    require({k:v for k,v in a['binding'].items() if k!='cursor'} == {k:v for k,v in b['binding'].items() if k!='cursor'}
            and b['binding']['cursor'] == receipt['sequence'] == provider['vault']['sequence']
            and receipt['vault_id'] == prior['vault_id'] == provider['vault']['id'] == b['binding']['remote_vault']
            and all(a[k] == b[k] for k in ('association','outbox','jobs','attempts','issuers','conflicts')),
            'RELEASE_SYNC_MUTATION_CHANGED_BINDING_OR_LOCAL_WRITES')
    remote=[v for v in provider['rows']['revisions'] if v['operation_id']==receipt['operation_id']]
    inbox=[v for v in b['inbox'] if v['sequence']==receipt['sequence']]
    head=[v for v in b['heads'] if v['file_id']==receipt['file_id']]
    require(len(remote)==len(inbox)==len(head)==1 and inbox[0]['state']=='applied'
            and all(remote[0][k]==receipt[k] for k in ('file_id','base_revision','path','operation','hash','size','sequence','device_id','fingerprint')),
            'RELEASE_SYNC_MUTATION_PROVIDER_OR_APPLY_MISSING')
    original=json.loads(inbox[0]['revision'])
    keys=('operation_id','file_id','base_revision','path','operation','hash','size','sequence','vault_id')
    require(set(original)==set(keys) and all(original[k]==receipt[k] for k in keys)
            and re.fullmatch(UUID,inbox[0]['operation_id']) and re.fullmatch(UUID,inbox[0]['rename_id'])
            and head[0]==dict(file_id=receipt['file_id'],revision=receipt['sequence'],path=RENAMED,hash='' if deleted else receipt['hash']),
            'RELEASE_SYNC_MUTATION_ORIGINAL_INBOX_OR_HEAD_CHANGED')
    require(len(b['inbox'])==len(a['inbox'])+1 and all(v in b['inbox'] for v in a['inbox'])
            and [v for v in a['heads'] if v['file_id']!=receipt['file_id']] == [v for v in b['heads'] if v['file_id']!=receipt['file_id']],
            'RELEASE_SYNC_MUTATION_REPLACED_UNRELATED_RECORDS')
    previous_rows=[v for v in before['files'] if v['id']==receipt['file_id']]
    current_rows=[v for v in after['files'] if v['id']==receipt['file_id']]
    require(len(previous_rows)==len(current_rows)==1,'RELEASE_SYNC_MUTATION_LOCAL_IDENTITY_MISSING')
    previous,current=previous_rows[0],current_rows[0]
    require(previous['path']==(RENAMED if deleted else PEER[0]) and previous['deleted']==0
            and current['path']==RENAMED and current['hash']==previous['hash']
            and current['deleted']==int(deleted) and current['revision']>previous['revision']
            and [v for v in before['files'] if v['id']!=receipt['file_id']] == [v for v in after['files'] if v['id']!=receipt['file_id']],
            'RELEASE_SYNC_MUTATION_LOCAL_IDENTITY_OR_TOMBSTONE_CHANGED')
    files=[v for v in provider['rows']['files'] if v['file_id']==receipt['file_id']]
    progresses=[v for v in provider['rows']['device_progress'] if v['device_id']==device]
    require(len(files)==len(progresses)==1,'RELEASE_SYNC_MUTATION_PROVIDER_IDENTITY_MISSING')
    file,progress=files[0],progresses[0]
    require(file['path_key']==RENAMED.casefold() and file['sequence']==receipt['sequence'] and file['deleted']==int(deleted)
            and progress['offered_sequence']==receipt['sequence'], 'RELEASE_SYNC_MUTATION_REMOTE_TOMBSTONE_OR_OFFERED_CHANGED')
    for path in (PEER[1],ASSET):
        require(before['raw_sha256']['vault/'+path]==after['raw_sha256']['vault/'+path], 'RELEASE_SYNC_MUTATION_UNRELATED_BYTES_CHANGED')


class ReleaseSyncMutations(ReleaseSyncRead):
    allowed_phases = ReleaseSync.allowed_phases + MUTATION_PHASES

    def phase(self, phase, label):
        require(phase in self.allowed_phases, 'RELEASE_SYNC_MUTATION_CLOSED_PHASE')
        if phase not in MUTATION_PHASES: return super().phase(phase,label)
        require(self.driver_entry=='ReleaseSyncRuntimeProbe', 'RELEASE_SYNC_MUTATION_DRIVER_CHANGED')
        self.driver_entry='ReleaseSyncMutationRuntimeProbe'
        try: return run_hosted_mutation_cycle(self,phase,label)
        finally: self.driver_entry='ReleaseSyncRuntimeProbe'

    def observe_absence(self, observer, root, stage, label, when):
        observer.require_stopped();observed=[]
        for index,path in enumerate(absent_paths(stage)):
            name=root+'vault/'+path;parent=root+'vault'
            script=shlex.join(('test','!','-e',name))+' && '+shlex.join(('test','!','-L',name))+' && '+shlex.join(('test','-d',parent))+' && '+shlex.join(('test','!','-L',parent))+' && '+shlex.join(('realpath',parent))
            result=self.original_command(label+'-absence-'+when+'-'+str(index),('shell','-T',script))
            canonical=result.stdout.decode('ascii').strip()
            require(canonical in (parent,parent.replace('/data/user/0/','/data/data/',1)), 'RELEASE_SYNC_MUTATION_ABSENCE_PARENT_ALIAS')
            observed.append(dict(path=path,absent_and_no_symlink=True,parent_canonical=canonical))
        return observed

    def mutation_snapshot(self,label,stage):
        paths=live_paths(stage);documents=[p for p in paths if p!=ASSET]
        observer=StoppedReleaseObserver(self.device,self.args.serial,self.output/'content-originals'/label,
            documents or list(PEER),attachment=ASSET if ASSET in paths else None)
        if not paths:observer.allowed={db+s for db in DATABASES for s in ('','-wal','-shm')}
        observer.identity=self.observer.identity
        require(observer.observe_identity()==self.identity,'RELEASE_SYNC_MUTATION_OWNER_CHANGED')
        root=observer.discover_data_root();before=self.observe_absence(observer,root,stage,label,'before')
        observer.copy_stopped(root,'snapshot');after=self.observe_absence(observer,root,stage,label,'after')
        require(before==after,'RELEASE_SYNC_MUTATION_ABSENCE_CHANGED')
        atomic_json(self.output/(label+'-absence-proof.json'),dict(stage=stage,before=before,after=after,
            stopped_before_and_after=True,original_command_results_retained=True))
        snapshot=observer.output/'snapshot';value=project_mutations(snapshot,self.tools/('mutation-working-'+label),stage=stage)
        atomic_json(self.output/(label+'-content-projection.json'),value)
        return value,{path:(snapshot/'vault'/path).read_bytes() for path in paths}

    def run(self):
        self.prepare();self.phase('login-a','mutation-login');auth=self.snapshot('mutation-login')
        require(len(auth['intents'])==1,'RELEASE_SYNC_MUTATION_ONE_LOGIN');login=auth['intents'][0];active_intent(auth,'login',login['device_id'])
        empty,_=self.mutation_snapshot('empty','empty');require(empty['native']['binding'] is None,'RELEASE_SYNC_MUTATION_FRESH_EMPTY')
        setup=Setup(self.owner);prepared=setup.run();atomic_json(self.output/'independent-setup.json',prepared)
        require(not prepared['provider']['rows']['revisions'],'RELEASE_SYNC_MUTATION_FRESH_REMOTE')
        self.stage_sync_input(prepared['vault']);self.phase('associate','mutation-association');frozen=self.original_association()
        associated,_=self.mutation_snapshot('associated','associated');validate_association(frozen,associated['native'],prepared,login)
        require(not self.sync_provider('associated')['rows']['revisions'],'RELEASE_SYNC_MUTATION_ASSOCIATION_TRANSFERRED_CONTENT')
        peer=MutationPeer(self.owner);receipts=[]
        bodies={ASSET:IMAGE_BYTES,PEER[0]:PEER_NOTE,PEER[1]:PEER_CANVAS}
        for label,path in (('mutation-peer-png',ASSET),('mutation-peer-note',PEER[0]),('mutation-peer-canvas',PEER[1])):
            receipt=peer.publish(label,path,bodies[path]);receipts.append(receipt);atomic_json(self.output/(label+'-receipt.json'),receipt)
        self.phase('cycle-incoming','mutation-download')
        downloaded,content=self.mutation_snapshot('downloaded','downloaded');provider=self.sync_provider('downloaded')
        validate_incoming(receipts,provider,associated['native'],downloaded['native'],login['device_id'])
        require(content==bodies,'RELEASE_SYNC_MUTATION_INITIAL_BYTES')
        for path,body in bodies.items():
            remote=read_original_object(setup.endpoint,prepared['vault']['vault_id'],hashlib.sha256(body).hexdigest(),setup.session()['access_token'])
            require(remote==body,'RELEASE_SYNC_MUTATION_S3_BYTES')
            target=self.output/'remote-originals'/path;target.parent.mkdir(parents=True,exist_ok=True);exclusive(target,remote)
        prior=next(v for v in receipts if v['path']==PEER[0]);before=downloaded;mutation_receipts=[]
        for deleted,phase,label,stage in ((False,'cycle-rename','peer-rename','renamed'),(True,'cycle-delete','peer-delete','deleted')):
            receipt=peer.publish(label,RENAMED,b'' if deleted else PEER_NOTE,file_id=prior['file_id'],base_revision=prior['sequence'],operation='delete' if deleted else 'put')
            atomic_json(self.output/(label+'-receipt.json'),receipt);mutation_receipts.append(receipt)
            self.phase(phase,'mutation-'+stage);after,body=self.mutation_snapshot(stage,stage);provider=self.sync_provider(stage)
            validate_mutation(before,after,receipt,prior,provider,login['device_id'],deleted=deleted)
            expected={PEER[1]:PEER_CANVAS,ASSET:IMAGE_BYTES}
            if not deleted:expected[RENAMED]=PEER_NOTE
            require(body==expected,'RELEASE_SYNC_MUTATION_ACTUAL_LIVE_BYTES');before=after;prior=receipt
        previous_pid=self.phases[-1]['pid'];self.phase('status','mutation-cold')
        require(self.phases[-1]['pid']!=previous_pid,'RELEASE_SYNC_MUTATION_NEW_COLD_PID')
        cold,body=self.mutation_snapshot('cold','cold');preserve_cold_mutation(before,cold)
        require(body=={PEER[1]:PEER_CANVAS,ASSET:IMAGE_BYTES} and self.sync_provider('cold')==provider
                and self.snapshot('mutation-final')==auth,'RELEASE_SYNC_MUTATION_COLD_AUTH_PROVIDER_BYTES_CHANGED')
        result=dict(schema=1,passed=True,kind='android-signed-release-peer-mutations',package=APP,abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'],run_id=int(os.environ['GITHUB_RUN_ID']),compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs/'runtime-inputs.json'),installed_binary=self.identities['successor'],phases=self.phases,
            original_login_id=login['operation_id'],Android_device=login['device_id'],independent_setup_device=prepared['device_id'],
            original_association=frozen,original_peer_receipts=receipts,original_mutation_receipts=mutation_receipts,
            same_file_id_rename_delete_and_cold_tombstone_verified=True,actual_S3_and_Android_bytes_verified=True,
            local_Save_or_upload_replayed=False,decoded_image_verified=False,Native_RAM_invocation_id_observed=False,
            Android_outbound_mutations_verified=False,actual_desktop_product_pair_verified=False,physical_phone_verified=False,
            offline_conflict_Stop_worker_recovery_verified=False,whole_Sync_A4_complete=False,published=False)
        atomic_json(self.output/'sync-mutations-runtime-proof.json',result);return result


if __name__=='__main__':raise SystemExit(main(ReleaseSyncMutations))
