"""One owned offline original, cold recovery and dropped commit acknowledgement.

Only the exclusive hosted emulator's saved connectivity is changed. The service
still executes its original request once; a changes read reconciles the original
commit before any upload may continue. This is not a desktop or phone claim.
"""
import hashlib,json,re,time,uuid
from android_auth_fixture import atomic_json,read_json
from android_runtime_probe import APP,DRAFT,require,require_hosted_emulator
from android_sync_fixture import arm_control,control_root


def network_observation(settings,connectivity):
    require(set(settings)=={'airplane_mode_on','wifi_on','mobile_data'}
        and all(value in ('0','1') for value in settings.values()),'Original emulator connectivity settings unavailable')
    values=re.findall(r'^\s*Active default network:\s*(none|[0-9]+)\s*$',connectivity,re.M)
    require(len(values)==1,'Actual emulator default network is ambiguous')
    return {'settings':dict(settings),'default_network_present':values[0]!='none'}


def validate_offline_original(before,native,provider_before,provider_after):
    require(native==before,'Offline/cold recovery changed original durable Sync records')
    require(provider_after['vault']==provider_before['vault']
        and provider_after['rows']==provider_before['rows']
        and provider_after['delivery']==provider_before['delivery'],
        'An offline attempt reached the service or changed original provider data')
    require(before['binding']['cursor']==0 and not any(before[key] for key in ('jobs','attempts','issuers','heads','inbox','conflicts')),
        'Offline first-upload case already has an earlier transfer')
    pending=[r for r in before['outbox'] if r['state']=='pending']
    require(len(pending)==1 and pending[0]['path']=='runtime-note.md' and pending[0]['operation']=='put'
        and pending[0]['hash']==hashlib.sha256(DRAFT.encode()).hexdigest(),
        'The actual original pending note is not unique')
    return pending[0]


def validate_unknown_original(original,native,provider,device,control,marker):
    require(marker=={'operation_id':control['operation_id'],'mode':'drop_revision','revision':control['result_revision'],
        'action':'revision','service_status':200,'service_response_complete':True,'raw_body_recorded':False},
        'The original successful commit acknowledgement was not actually dropped')
    revisions=provider['rows']['revisions'];jobs=native['jobs'];attempts=native['attempts']
    require(len(revisions)==len(jobs)==len(attempts)==len(native['issuers'])==1
        and provider['vault']['sequence']==1 and native['binding']['cursor']==0
        and not native['heads'] and not native['inbox'] and not native['conflicts'],
        'Unknown original was duplicated, acknowledged or applied before recovery')
    receipt=revisions[0];job=jobs[0];attempt=attempts[0]
    for key in ('operation_id','file_id','path','hash'):
        require(receipt[key]==job[key]==original[key],'Unknown original operation/body changed')
    require(receipt['device_id']==device and receipt['base_revision']==0 and receipt['operation']=='put'
        and re.fullmatch(r'[0-9a-f]{64}',receipt['fingerprint'])
        and receipt['size']==job['size']==len(DRAFT.encode()) and receipt['sequence']==1
        and job['state']=='committing' and job['remote_revision'] is None
        and attempt['operation_id']==original['operation_id'] and attempt['attempts']==1
        and attempt['outcome']=='failed' and bool(attempt['error'])
        and native['issuers']==[{'operation_id':original['operation_id'],'device_id':device}],
        'Actual native unknown commit/attempt/issuer differs')
    require(provider['rows']['objects']==[{'hash':receipt['hash'],'size':receipt['size']}]
        and not provider['rows']['uploads'] and provider['rows']['files']==[{'file_id':receipt['file_id'],
            'sequence':receipt['sequence'],'path_key':receipt['path'],'deleted':0}],
        'Unknown original provider object/file/upload state differs')
    events=[r for r in provider['delivery']['events'] if r['fault_operation']==control['operation_id']]
    require(events==[{'action':'revision','service_status':200,'delivery_status':503,'fault_operation':control['operation_id']}]
        and provider['delivery']['counts']['revision:200']==1,
        'The actual service original was not committed exactly once before delivery loss')
    current=[r for r in native['outbox'] if r['operation_id']==original['operation_id']]
    require(len(current)==1 and all(current[0][k]==v for k,v in original.items() if k not in ('state','payload_size','payload_sha256'))
        and current[0]['state']=='queued','Unknown commit replaced its original outbox')
    return receipt


def validate_reconciled_original(before,native,provider_before,provider_after,receipt):
    require(provider_after['vault']==provider_before['vault']
        and all(provider_after['rows'][k]==provider_before['rows'][k] for k in ('revisions','files','objects','uploads'))
        and {k:v for k,v in provider_after['delivery']['counts'].items() if not k.startswith('changes:')}
            =={k:v for k,v in provider_before['delivery']['counts'].items() if not k.startswith('changes:')},
        'Original reconciliation issued another revision or object write')
    require(native['binding']['id']==before['binding']['id'] and native['binding']['cursor']==receipt['sequence']
        and len(native['jobs'])==len(native['heads'])==len(native['inbox'])==1 and not native['conflicts']
        and native['attempts']==before['attempts'] and native['issuers']==before['issuers']
        and native['association']==before['association'],
        'Original read recovery changed attempt/issuer/association or did not apply the confirmed boundary')
    job=native['jobs'][0];old=before['jobs'][0]
    require(all(job[k]==v for k,v in old.items() if k not in ('state','remote_revision','error'))
        and job['state']=='acked' and job['remote_revision']==receipt['sequence'] and job['error'] is None,
        'Original native acknowledgement was not reconciled')
    head=native['heads'][0];inbox=native['inbox'][0]
    keys=('operation_id','file_id','base_revision','path','operation','hash','size','sequence')
    revision=json.loads(inbox['revision'])
    require(head=={'file_id':receipt['file_id'],'revision':receipt['sequence'],'path':receipt['path'],'hash':receipt['hash']}
        and inbox['sequence']==receipt['sequence'] and inbox['state']=='applied'
        and set(revision)==set(keys)|{'vault_id'} and revision['vault_id']==native['binding']['remote_vault']
        and all(revision[k]==receipt[k] for k in keys),'Original native head/inbox bytes differ')
    old_outbox={r['operation_id']:r for r in before['outbox']};current={r['operation_id']:r for r in native['outbox']}
    require(len(current)==len(native['outbox']) and set(current)==set(old_outbox)
        and all(current[k]==v for k,v in old_outbox.items() if k!=receipt['operation_id'])
        and current[receipt['operation_id']]==dict(old_outbox[receipt['operation_id']],state='acked'),
        'Reconciliation replaced an outbox original')


class RecoveryMixin:
    def emulator_connectivity(self):
        settings={key:self.shell('settings','get','global',key).stdout.decode().strip()
            for key in ('airplane_mode_on','wifi_on','mobile_data')}
        value=self.shell('dumpsys','connectivity').stdout
        require(len(value)<=4*1024*1024,'Owned connectivity observation exceeds bound')
        return network_observation(settings,value.decode('utf-8','replace'))

    def wait_connectivity(self,settings,connected):
        deadline=time.monotonic()+30
        while True:
            try:value=self.emulator_connectivity()
            except RuntimeError:value=None
            if value is not None and value['settings']==settings and value['default_network_present'] is connected:return value
            require(time.monotonic()<deadline,'Owned original connectivity did not settle');time.sleep(.2)

    def restore_connectivity(self,original):
        # Each command is sent once for the saved original state; no host or
        # physical-device network is touched and no guessed defaults are used.
        results=[self.shell('cmd','connectivity','airplane-mode','enable' if original['airplane_mode_on']=='1' else 'disable',check=False),
            self.shell('svc','wifi','enable' if original['wifi_on']=='1' else 'disable',check=False),
            self.shell('svc','data','enable' if original['mobile_data']=='1' else 'disable',check=False)]
        require(all(r.returncode==0 for r in results),'Original emulator network restore needs checking')
        value=self.wait_connectivity(original,True)
        atomic_json(self.output/'offline-network-restored.json',value,exclusive=True)

    def recover_original(self,setup,prepared,device):
        require_hosted_emulator(self.args.serial)
        self.sync_phase('status')
        before=self.host_snapshot('offline-before',include_outbox=True);provider_before=self.observe('offline-before')
        original=validate_offline_original(before,before,provider_before,provider_before)
        auth=self.snapshot('offline-before');sealed=self.ciphertext_fingerprints();note=self.original('runtime-note.md')
        original_network=self.emulator_connectivity()
        require(original_network['default_network_present'] and original_network['settings']['airplane_mode_on']=='0',
            'Owned emulator was not actually connected before offline case')
        atomic_json(self.output/'offline-network-original-intent.json',{'serial':self.args.serial,**original_network,
            'physical_or_host_network_changed':False},exclusive=True)
        attempted=False;restore_attempted=False
        try:
            attempted=True
            self.shell('cmd','connectivity','airplane-mode','enable');self.shell('svc','wifi','disable');self.shell('svc','data','disable')
            disconnected=self.wait_connectivity({'airplane_mode_on':'1','wifi_on':'0','mobile_data':'0'},False)
            atomic_json(self.output/'offline-network-disconnected.json',disconnected,exclusive=True)
            self.sync_phase('cycle-offline')
            offline=self.host_snapshot('offline-failed',include_outbox=True);provider=self.observe('offline-failed')
            validate_offline_original(before,offline,provider_before,provider)
            require(self.snapshot('offline-failed')==auth and self.ciphertext_fingerprints()==sealed
                and self.original('runtime-note.md')==note,'Offline failure damaged original auth or saved bytes')
            self.shell('am','force-stop',APP)
            require(not self.shell('pidof',APP,check=False).stdout.strip(),'Owned app process survived explicit force-stop')
            killed=self.host_snapshot('offline-killed',include_outbox=True)
            validate_offline_original(before,killed,provider_before,self.observe('offline-killed'))
            require(self.snapshot('offline-killed')==auth and self.ciphertext_fingerprints()==sealed,
                'Cold original auth/ciphertext differs after actual termination')
            restore_attempted=True;self.restore_connectivity(original_network['settings'])
        finally:
            if attempted and not restore_attempted:
                restore_attempted=True;self.restore_connectivity(original_network['settings'])

        # This is a new explicit online cycle of the same pending original. It
        # is not an automatic retry of the failed offline UI action.
        root=control_root(self.owner);current=read_json(root/'control.json');control_id=str(uuid.uuid4())
        atomic_json(self.output/'recovery-drop-original-control-intent.json',{'operation_id':control_id,'mode':'drop_revision',
            'expected_revision':current['revision'],'native_or_server_operation_id':False},exclusive=True)
        control=arm_control(self.owner,control_id,'drop_revision',current['revision'])
        atomic_json(self.output/'recovery-drop-control-receipt.json',control,exclusive=True)
        self.sync_phase('cycle-dropped-upload')
        unknown=self.host_snapshot('commit-unknown',include_outbox=True);provider_unknown=self.observe('commit-unknown')
        marker=read_json(root/'fault-events'/(control_id+'.json'))
        atomic_json(self.output/'recovery-original-dropped-response.json',marker,exclusive=True)
        receipt=validate_unknown_original(original,unknown,provider_unknown,device,control,marker)
        require(self.snapshot('commit-unknown')==auth and self.ciphertext_fingerprints()==sealed
            and self.original('runtime-note.md')==note,'Unknown original changed auth or saved source')
        # Read the original queue before force-stop, and preserve its unknown
        # original rather than pretending that the initial POST succeeded.
        self.sync_phase('status')
        require(self.host_snapshot('unknown-after-readonly',include_outbox=True)==unknown,'Read-only original check mutated transfer data')
        self.shell('am','force-stop',APP)
        require(not self.shell('pidof',APP,check=False).stdout.strip(),'Owned app process survived original recovery force-stop')
        require(self.host_snapshot('unknown-killed',include_outbox=True)==unknown,'Cold restart changed unknown original')
        self.sync_phase('cycle-reconcile-original')
        native=self.host_snapshot('original-reconciled',include_outbox=True);provider_after=self.observe('original-reconciled')
        validate_reconciled_original(unknown,native,provider_unknown,provider_after,receipt)
        from android_sync_runtime import read_original_object
        require(self.original('runtime-note.md')==note==read_original_object(setup.endpoint,prepared['vault']['vault_id'],
            receipt['hash'],setup.session()['access_token']) and self.snapshot('original-reconciled')==auth
            and self.ciphertext_fingerprints()==sealed,'Original cold recovery source/object/auth differs')
        atomic_json(self.output/'sync-recovery-proof.json',{'passed':True,'source_commit':self.args.commit,
            'runtime_tools_commit':self.args.tools_commit,'APK_sha256':self.binding['sha256'],'phases':self.phases,
            'original_offline_outbox':original,'original_confirmed_revision':receipt,'original_drop_control':control,
            'actual_hosted_emulator_offline_no_service_request':True,'original_network_restored':True,
            'actual_force_stop_preserved_pending_and_unknown_originals':True,'original_POST_once_reconciled_by_changes_read':True,
            'failed_original_attempt_retained_not_relabelled_success':True,'actual_S3_native_original_bytes_equal':True,
            'desktop_phone_conflict_upgrade_signing_release_verified':False},exclusive=True)
