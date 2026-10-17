"""Actual saved conflict, explicit original copy decision and retained dirty draft."""
import hashlib,json,re,uuid
from android_auth_fixture import atomic_json
from android_runtime_probe import DRIVER,require,require_hosted_emulator
from android_sync_peer import Peer

LOCAL='# Android 本机并发修改 😀\n\n已确认保存的本地版本。\n'.encode()
DIRTY=LOCAL+'未保存的独立草稿，不能自动上传或丢弃。\n'.encode()
REMOTE='# 独立客户端并发修改 😀\n\n明确审核后才采用的远端版本。\n'.encode()
COPY='runtime-conflict-copy.md'
IDS=('operation_id','rename_id','copy_id','retire_id','restore_id')


def validate_retained_conflict(first,native,provider,receipt):
    original=first['jobs'][0];rows=provider['rows']['revisions']
    require(receipt['sequence']==native['binding']['cursor']==provider['vault']['sequence']==2
        and receipt['vault_id']==native['binding']['remote_vault']==provider['vault']['id']
        and len(rows)==2 and rows[-1]['operation_id']==receipt['operation_id']
        and all(rows[-1][key]==receipt[key] for key in ('sequence','base_revision','operation_id','file_id','path','hash','size','device_id','operation','fingerprint'))
        and receipt['file_id']==original['file_id'] and receipt['base_revision']==1
        and receipt['path']=='runtime-note.md' and receipt['hash']==hashlib.sha256(REMOTE).hexdigest()
        and receipt['size']==len(REMOTE) and receipt['device_id']!=first['issuers'][0]['device_id'],
        'Actual saved conflict does not use the original file/base/independent remote bytes')
    require(native['association']==first['association']
        and native['heads']==[{'file_id':receipt['file_id'],'revision':2,'path':receipt['path'],'hash':receipt['hash']}]
        and native['attempts']==first['attempts'] and native['issuers']==first['issuers']
        and len(native['jobs'])==2 and native['jobs'][0]==original and not native['decisions'],
        'Incoming conflict replaced original acknowledged data or created a decision')
    local=native['jobs'][1];conflicts=native['conflicts']
    require(local['file_id']==original['file_id'] and local['path']=='runtime-note.md'
        and local['hash']==hashlib.sha256(LOCAL).hexdigest() and local['size']==len(LOCAL)
        and local['state']=='conflict' and local['operation_id']!=original['operation_id']
        and len(conflicts)==1 and conflicts[0]=={'sequence':2,'file_id':original['file_id'],
            'local_path':'runtime-note.md','local_hash':local['hash'],'state':'open'},
        'Actual local saved version or unresolved conflict was not retained')
    inbox=[r for r in native['inbox'] if r['sequence']==2]
    require(len(inbox)==1 and inbox[0]['state']=='conflict'
        and all(json.loads(inbox[0]['revision'])[k]==receipt[k] for k in
            ('operation_id','file_id','path','base_revision','hash','size','sequence')),
        'Actual conflict inbox differs from the original incoming revision')
    return local


def validate_original_copy(native,frozen,receipt):
    require(len(native['decisions'])==1,'Expected one original conflict decision')
    decision=native['decisions'][0]
    require(decision['sequence']==receipt['sequence']==frozen['sequence']==2 and decision['choice']==frozen['choice']=='copy'
        and decision['destination']==frozen['destination']==COPY and decision['expected_hash']==hashlib.sha256(LOCAL).hexdigest()
        and decision['fingerprint']==frozen['fingerprint'] and re.fullmatch(r'[0-9a-f]{64}',decision['fingerprint'])
        and decision['state']=='completed','Original confirmed choice/target/hash/review fingerprint differs')
    values=[decision[key] for key in IDS]
    require(len(set(values))==5 and all(isinstance(value,str) and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',value)
        and str(uuid.UUID(value))==value for value in values),
        'Original conflict IDs were replaced, duplicated or noncanonical')
    require(len(native['conflicts'])==1 and native['conflicts'][0]['state']=='resolved'
        and native['binding']['cursor']==2,'Original local decision did not resolve only its received boundary')
    heads=[row for row in native['heads'] if row['file_id']==receipt['file_id']]
    require(len(heads)==1 and heads[0]['revision']==2 and heads[0]['path']==receipt['path']
        and heads[0]['hash']==receipt['hash'],'Actual selected incoming head differs from reviewed original')
    return decision


class ConflictMixin:
    def original_conflict_driver(self,kind):
        require(kind in ('frozen','confirmation','receipt'),'Unrelated original conflict driver record')
        result=self.shell('run-as',DRIVER,'cat','no_backup/owned-sync-conflict-copy-'+kind+'-intent.json')
        require(0<len(result.stdout)<=4096,'Original conflict driver record exceeds bound')
        value=json.loads(result.stdout)
        atomic_json(self.output/('conflict-original-driver-'+kind+'.json'),value,exclusive=True)
        return value

    def conflict_copy(self,setup,prepared,device):
        require_hosted_emulator(self.args.serial)
        first=self.host_snapshot('conflict-initial');require(len(first['jobs'])==1,'Original upload prerequisite ambiguous')
        uploaded=first['jobs'][0];auth=self.snapshot('conflict-initial');sealed=self.ciphertext_fingerprints()
        atomic_json(self.output/'conflict-owned-edit-original-intent.json',{'driver_intent_id':str(uuid.uuid4()),
            'original_uploaded_operation_id':uploaded['operation_id'],'file_id':uploaded['file_id'],
            'saved_sha256':hashlib.sha256(LOCAL).hexdigest(),'dirty_sha256':hashlib.sha256(DIRTY).hexdigest(),
            'native_or_server_new_operation_id':False},exclusive=True)
        self.phase('conflict-local-save')
        require(self.original('runtime-note.md')==LOCAL,'Actual local Save differs from original edit')
        self.phase('conflict-dirty-draft')
        rows=[r for r in self.draft_rows() if r[0]=='runtime-note.md']
        require(len(rows)==1 and rows[0][1].encode()==DIRTY and rows[0][2]>rows[0][3],
            'Actual unsaved original draft is not durably retained')
        atomic_json(self.output/'conflict-original-dirty-draft.json',{'rows':rows,'actual_unsaved':True},exclusive=True)
        receipt=Peer(self.owner).publish('concurrent-original','runtime-note.md',REMOTE,
            file_id=uploaded['file_id'],base_revision=uploaded['remote_revision'])
        atomic_json(self.output/'conflict-peer-original-receipt.json',receipt,exclusive=True)
        self.sync_phase('cycle-conflict')
        conflicted=self.host_snapshot('conflict-retained');provider=self.observe('conflict-retained')
        local=validate_retained_conflict(first,conflicted,provider,receipt)
        require(self.original('runtime-note.md')==LOCAL and [r for r in self.draft_rows() if r[0]=='runtime-note.md']==rows,
            'Incoming conflict overwrote original saved local bytes or dirty draft')
        self.sync_phase('conflict-copy')
        frozen=self.original_conflict_driver('frozen');confirmed=self.original_conflict_driver('confirmation')
        require(frozen==confirmed,'The canceled frozen review was replaced before the one original confirmation')
        seen=self.original_conflict_driver('receipt');native=self.host_snapshot('conflict-resolved')
        decision=validate_original_copy(native,frozen,receipt)
        require(seen['sequence']==2 and seen['fingerprint']==decision['fingerprint'] and seen['read_only_original_query']
            and set(seen['original_native_ids'])=={decision[key] for key in IDS},'Actual readonly UI receipt changed five original IDs')
        require(self.original('runtime-note.md')==REMOTE and self.original(COPY)==LOCAL
            and [r for r in self.draft_rows() if r[0]=='runtime-note.md']==rows,
            'Explicit local copy/incoming apply lost original saved bytes or dirty draft')
        after=self.observe('conflict-local-decision')
        require(after['vault']==provider['vault'] and after['rows']==provider['rows'] and after['delivery']==provider['delivery'],
            'Local review/decision unexpectedly contacted a transfer route')
        self.sync_phase('cycle-conflict-copy')
        pushed=self.host_snapshot('conflict-copy-uploaded');after=self.observe('conflict-copy-uploaded')
        require(pushed['decisions']==native['decisions'] and after['vault']['sequence']==pushed['binding']['cursor']==3
            and len(after['rows']['revisions'])==3,'Confirmed copy upload replaced the original decision or boundary')
        revision=after['rows']['revisions'][-1]
        require(revision['operation_id']==decision['copy_id'] and revision['path']==COPY and revision['base_revision']==0
            and revision['device_id']==device and revision['hash']==hashlib.sha256(LOCAL).hexdigest()
            and revision['size']==len(LOCAL) and revision['file_id']!=uploaded['file_id'],
            'Actual copy upload changed its original native copy ID, identity or saved bytes')
        jobs=[r for r in pushed['jobs'] if r['operation_id']==decision['copy_id']]
        require(len(jobs)==1 and jobs[0]['state']=='acked' and jobs[0]['remote_revision']==3 and jobs[0]['error'] is None,
            'Original copy upload lacks actual native acknowledgement')
        require(any(r['operation_id']==local['operation_id'] and r['state']=='archived' for r in pushed['jobs'])
            and not any(r['hash']==hashlib.sha256(DIRTY).hexdigest() for r in after['rows']['revisions']),
            'An unresolved local job or unsaved draft was uploaded instead of the reviewed copy')
        self.phase('conflict-draft-reopen')
        cold=self.host_snapshot('conflict-cold');require(cold==pushed,'Cold draft presentation changed Sync originals')
        require([r for r in self.draft_rows() if r[0]=='runtime-note.md']==rows and self.original('runtime-note.md')==REMOTE
            and self.original(COPY)==LOCAL and self.snapshot('conflict-final')==auth and self.ciphertext_fingerprints()==sealed,
            'Cold reviewed conflict damaged original draft/source/copy/auth')
        from android_sync_runtime import read_original_object
        for row,body in ((receipt,REMOTE),(revision,LOCAL)):
            require(read_original_object(setup.endpoint,prepared['vault']['vault_id'],row['hash'],setup.session()['access_token'])==body,
                'Actual S3-backed original conflict object differs')
        atomic_json(self.output/'sync-conflict-proof.json',{'passed':True,'source_commit':self.args.commit,
            'runtime_tools_commit':self.args.tools_commit,'APK_sha256':self.binding['sha256'],'phases':self.phases,
            'original_incoming_revision':receipt,'original_native_decision':decision,'original_copy_upload':revision,
            'actual_UI_saved_local_and_independent_remote_conflict':True,'actual_UI_diff_frozen_cancel_and_same_original_confirm':True,
            'actual_UI_readonly_five_original_IDs':True,'actual_dirty_draft_cold_bytes_retained_not_uploaded':True,
            'actual_S3_original_saved_and_copy_bytes_equal':True,
            'unknown_decision_reply_or_stale_review_rejection_verified':False,
            'actual_cold_completed_decision_UI_entry_verified':False,
            'desktop_phone_upgrade_signing_release_verified':False},exclusive=True)
