"""Real owned service revocation and exact original rejection data contracts.

Only the isolated hosted setup actor can revoke its actual Android peer. An
unknown DELETE is reconciled by GET, never automatically repeated. This module
does not disable TLS, modify provider SQL or execute an Android UI by itself.
"""
from copy import deepcopy
import hashlib,http.client,json,re,ssl,uuid
from urllib.parse import urlsplit
from android_auth_fixture import atomic_json,owned_root,read_json
from android_runtime_probe import require


class OriginalRevocation:
    def __init__(self,setup):
        require(setup.owner.resolve()==owned_root(),'Owned revocation root differs')
        require(re.fullmatch(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com',setup.endpoint),
            'Owned revocation endpoint differs')
        self.setup=setup;self.root=setup.owner/'sync-revocation'
        require(not self.root.is_symlink(),'Owned revocation root is a link')
        self.root.mkdir(mode=0o700,exist_ok=True)

    def request(self,method,path,token):
        require((method,path)==('GET','/sync/v1/devices') or method=='DELETE'
            and re.fullmatch(r'/sync/v1/devices/[0-9a-f]{32}',path),'Unexpected revocation route')
        require(isinstance(token,str) and re.fullmatch(r'[A-Za-z0-9_-]{32,256}',token),'Owned actor session invalid')
        connection=http.client.HTTPSConnection(urlsplit(self.setup.endpoint).hostname,timeout=25,context=ssl.create_default_context())
        try:
            connection.request(method,path,headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
            response=connection.getresponse();raw=response.read(65537)
            require(len(raw)<=65536,'Owned revocation response exceeds limit')
            return response.status,raw
        finally:connection.close()

    def devices(self,token,target,actor):
        status,raw=self.request('GET','/sync/v1/devices',token)
        require(status==200,'Owned device read failed; no DELETE is repeated')
        value=json.loads(raw);items=value.get('items')
        require(isinstance(items,list) and 2<=len(items)<=16,'Owned device list differs')
        require(all(isinstance(item,dict) and isinstance(item.get('id'),str)
            and re.fullmatch(r'[0-9a-f]{32}',item['id']) and type(item.get('revoked')) is int
            and item['revoked'] in (0,1) for item in items),'Owned device metadata differs')
        selected={item['id']:dict(id=item['id'],revoked=item['revoked']) for item in items}
        require(len(selected)==len(items) and target in selected and actor in selected
            and not selected[actor]['revoked'],'Target is not this active setup actor\'s distinct device')
        return selected

    def revoke(self,target):
        session=self.setup.session();actor=session['device_id'];token=session['access_token']
        require(isinstance(target,str) and re.fullmatch(r'[0-9a-f]{32}',target)
            and isinstance(actor,str) and re.fullmatch(r'[0-9a-f]{32}',actor) and target!=actor,
            'Revocation target must be the distinct actual Android device')
        intent_path=self.root/'revoke-intent.json';receipt_path=self.root/'revoke-receipt.json'
        path='/sync/v1/devices/'+target
        fingerprint=hashlib.sha256(('DELETE\n'+path).encode()).hexdigest()
        current=self.devices(token,target,actor)
        fresh=not intent_path.exists()
        if fresh:
            require(not current[target]['revoked'],'Fresh revocation target was already revoked')
            atomic_json(self.root/'revoke-before.json',current,exclusive=True)
            intent=dict(operation_id=str(uuid.uuid4()),device_id=target,actor_device_id=actor,
                request_sha256=fingerprint,method='DELETE',path=path,body=None,result='not_attempted',
                server_operation_id_available=False,automatic_retry=False)
            atomic_json(intent_path,intent,exclusive=True)
            try:
                status,raw=self.request('DELETE',path,token)
                intent.update(result='received',status=status,response_bytes=len(raw),response_sha256=hashlib.sha256(raw).hexdigest())
            except (OSError,http.client.HTTPException):
                intent['result']='unknown'
            atomic_json(intent_path,intent)
            current=self.devices(token,target,actor)
        else:
            intent=read_json(intent_path)
            require(intent['device_id']==target and intent['actor_device_id']==actor
                and intent['method']=='DELETE' and intent['path']==path and intent['body'] is None
                and intent['request_sha256']==fingerprint and not intent['automatic_retry'],
                'Original revocation intent changed; no DELETE is repeated')
            require(not read_json(self.root/'revoke-before.json')[target]['revoked'],
                'Original target was not an active actual device')
        require(current[target]['revoked']==1,'Original revocation not confirmed by actual GET; no DELETE is repeated')
        require(intent['result']=='unknown' or intent['result']=='received' and intent['status']==204
            and intent['response_bytes']==0,'Original DELETE was not accepted; preserve its actual response')
        receipt={key:intent[key] for key in ('operation_id','device_id','actor_device_id','request_sha256')}
        receipt.update(delivery='unknown_response_then_actual_GET' if intent['result']=='unknown' else 'original_204_then_actual_GET',
            server_operation_id_available=False,actual_GET_target_revoked=True,automatic_DELETE_retry=False)
        if receipt_path.exists():require(read_json(receipt_path)==receipt,'Original revocation receipt changed')
        else:atomic_json(receipt_path,receipt,exclusive=True)
        return receipt


def validate_revoked_accounts(before,after,target,actor):
    users=before.get('users');require(isinstance(users,list) and len(users)==2,'Owned account scope differs')
    devices=users[0]['devices'];ids=[device['id'] for device in devices]
    require(len(set(ids))==len(ids) and ids.count(target)==ids.count(actor)==1 and target!=actor,
        'Original Android/setup device identity differs')
    expected=deepcopy(before)
    selected=next(item for item in expected['users'][0]['devices'] if item['id']==target)
    require(selected['revoked']==0,'Original Android device was already revoked')
    selected['revoked']=1
    require({key:value for key,value in after.items() if key!='requests'}
        =={key:value for key,value in expected.items() if key!='requests'},
        'Revocation changed another device/account or original session counts')


def validate_rejected_sync(before,after,provider,remote,original,observed):
    require(isinstance(original,bytes) and original==observed and 0<len(original)<=1024*1024,
        'Rejected original local bytes changed')
    digest=hashlib.sha256(original).hexdigest();outbox=before['outbox']
    require(isinstance(outbox,list) and 1<=len(outbox)<=2,'Original saved-note outbox differs')
    pending=[row for row in outbox if row['state'] in ('pending','queued')]
    require(len(pending)==1,'Original saved-note pending identity differs');saved=pending[0]
    require(saved['path']=='runtime-note.md' and saved['operation']=='put' and saved['hash']==digest,'Original saved-note outbox differs')
    payload=dict(operation_id=saved['operation_id'],hash=digest,size=len(original))
    require(str(uuid.UUID(saved['operation_id']))==saved['operation_id'] and str(uuid.UUID(saved['file_id']))==saved['file_id']
        and type(saved['revision']) is int and saved['revision']>0,'Original saved-note identity differs')
    archived=[row for row in outbox if row is not saved]
    require(all(row['state']=='archived' and row['payload_size']==0
        and all(row[key]==saved[key] for key in ('file_id','revision','path','hash','operation'))
        and str(uuid.UUID(row['operation_id']))==row['operation_id'] for row in archived),
        'Original association archive identity differs')
    ids=[row['operation_id'] for row in outbox];require(len(set(ids))==len(ids),'Original outbox identities duplicated')
    payloads=before['payloads']
    require(isinstance(payloads,list) and len(payloads)<=len(outbox)
        and all(value==dict(operation_id=value['operation_id'],hash=digest,size=len(original))
            and value['operation_id'] in ids for value in payloads)
        and len({value['operation_id'] for value in payloads})==len(payloads),'Original payload identities differ')
    require(all(dict(operation_id=row['operation_id'],hash=digest,size=len(original)) in payloads for row in archived),
        'Original archived payload is missing')
    require(saved['payload_size']==len(original) and saved['state']=='pending'
        or saved['payload_size']==0 and payload in payloads,'Original saved-note outbox payload state differs')
    job={key:saved[key] for key in ('operation_id','file_id','path','hash')}
    job.update(size=len(original),state='pending',remote_revision=None,error=None)
    require(before['jobs'] in ([],[job])
        and not before['attempts'] and not before['issuers'],'Unexpected original transfer attempt already exists')
    expected=deepcopy(before);expected['jobs']=[job]
    if payload not in expected['payloads']:expected['payloads'].append(payload)
    expected['outbox'][outbox.index(saved)].update(state='queued',payload_size=0)
    require(after==before or after==expected,'Rejection changed original IDs/cursor/head or acknowledged/lost the queued payload')
    require(provider['vault']['id']==before['binding']['remote_vault']
        and provider['vault']['sequence']==before['binding']['cursor']==0,
        'Rejection case is not the new original empty remote boundary')
    require({key:value for key,value in remote.items() if key!='delivery'}
        =={key:value for key,value in provider.items() if key!='delivery'},'Rejected cycle wrote provider content or progress')
    counts=dict(provider['delivery']['counts']);counts['changes:401']=counts.get('changes:401',0)+1
    event=dict(action='changes',service_status=401,delivery_status=401,fault_operation=None)
    require(len(provider['delivery']['events'])<64 and remote['delivery']==dict(counts=counts,events=provider['delivery']['events']+[event]),
        'One actual service401 is absent or was substituted/delivered/repeated')
    return dict(operation_id=saved['operation_id'],file_id=saved['file_id'],sha256=digest,size=len(original),
        preserved_archived_operation_ids=[row['operation_id'] for row in archived],
        cursor=before['binding']['cursor'],original_pending_job_and_payload_retained=after['jobs']==[job],
        original_pending_outbox_and_payload_retained=True,
        actual_401_without_delivery_fault=True,no_provider_content_write=True)


def validate_refresh_local(before,after,original,observed):
    require(type(original) is bytes and original==observed and 0<len(original)<=1024*1024 and before==after,
        'Rejected refresh changed original local bytes or Native state')
    digest=hashlib.sha256(original).hexdigest();rows=before['outbox']
    require(len(rows)==2 and sorted(row['state'] for row in rows)==['archived','pending']
        and before['binding']['cursor']==0 and before['binding']['state']=='active'
        and all(before[key]==[] for key in ('jobs','attempts','heads','inbox','conflicts','issuers')),
        'Refresh-only original is not the unchanged pending association boundary')
    pending=next(row for row in rows if row['state']=='pending');archived=next(row for row in rows if row['state']=='archived')
    ids=[row['operation_id'] for row in rows]
    require(len(set(ids))==2 and str(uuid.UUID(pending['file_id']))==pending['file_id']
        and all(str(uuid.UUID(row['operation_id']))==row['operation_id'] and row['file_id']==pending['file_id']
            and row['path']=='runtime-note.md' and row['operation']=='put' and row['hash']==digest
            and row['revision']==1 and row['payload_size']==0 for row in rows),
        'Refresh-only original operation or file identity differs')
    require(before['payloads']==[dict(operation_id=row['operation_id'],hash=digest,size=len(original)) for row in rows],
        'Rejected refresh lost or replaced an original spool record')
    return dict(operation_id=pending['operation_id'],file_id=pending['file_id'],sha256=digest,size=len(original),
        cursor=0,preserved_archived_operation_ids=[archived['operation_id']],original_pending_outbox_and_payload_retained=True,
        original_pending_job_and_payload_retained=False,unchanged_native_boundary=True)


def validate_rejected_refresh(before,after,device):
    require(len(before['intents'])==1 and not before['rejections'] and len(after['intents'])==2,
        'Original login/refresh/rejection count differs')
    login=before['intents'][0];refresh=after['intents'][1]
    require(login['kind']=='login' and login['phase']=='active' and login['device_id']==device
        and after['intents'][0]==login and before['selection']['operation_id']==login['operation_id'],
        'Original active login was replaced')
    require(refresh['operation_id']!=login['operation_id'] and refresh['kind']=='refresh'
        and refresh['phase']=='unknown' and refresh['previous_operation']==login['operation_id']
        and refresh['device_id']==device and refresh['generation']==login['generation']+1
        and all(refresh[key]==login[key] for key in ('endpoint','account','device_name','server_logout_confirmed')),
        'Rejected refresh activated credentials or changed the original lineage')
    selected=after['selection'];prior=before['selection']
    require(selected['operation_id']==refresh['operation_id'] and selected['generation']==refresh['generation']
        and selected['revision']==prior['revision']+1 and selected['endpoint']==prior['endpoint']
        and selected['account']==prior['account'],'Original rejected refresh selection differs')
    require(after['rejections']==[dict(operation_id=refresh['operation_id'],status=401,code='SESSION_EXPIRED')],
        'Actual original Native401 rejection is missing or differs')
    return refresh['operation_id']


def original_driver_intent(probe,phase):
    from android_runtime_probe import DRIVER
    require(phase in ('cycle-revoked','refresh-revoked'),'Unexpected rejection UI intent')
    result=probe.shell('run-as',DRIVER,'cat','no_backup/owned-sync-'+phase+'-intent.json',check=False)
    require(result.returncode==0 and 0<len(result.stdout)<=2048,'Original rejection UI intent unavailable')
    value=json.loads(result.stdout);field='native_invocation_id_observed' if phase=='cycle-revoked' else 'native_operation_id_observed'
    require(set(value)=={'driver_intent_id',field,'action'}
        and str(uuid.UUID(value['driver_intent_id']))==value['driver_intent_id'] and value[field] is False
        and value['action']==('Sync one cycle now' if phase=='cycle-revoked' else 'Refresh session'),
        'Original rejection UI intent changed or contains private fields')
    return value


def failure_observations(probe,*,prefix='revocation'):
    from android_auth_fixture import observe_provider as observe_auth_provider
    require(prefix in ('revocation','expiration'),'Unknown rejection diagnostic scope')
    for label,read in (
        ('auth-provider',lambda:observe_auth_provider(probe.owner)),
        ('auth-native',lambda:probe.snapshot(prefix+'-failure',include_rejections=True)),
        ('host-native',lambda:probe.host_snapshot(prefix+'-failure',include_outbox=True,include_payloads=True)),
        ('cycle-intent',lambda:original_driver_intent(probe,'cycle-revoked')),
        ('refresh-intent',lambda:original_driver_intent(probe,'refresh-revoked'))):
        try:
            value=read()
            if label not in ('auth-native','host-native'):
                atomic_json(probe.output/(prefix+'-failure-'+label+'.json'),value,exclusive=True)
        except Exception as error:
            atomic_json(probe.output/(prefix+'-failure-'+label+'-unavailable.json'),
                dict(error_class=type(error).__name__,no_mutation_retried=True),exclusive=True)


def run_revocation(probe,setup,prepared,device):
    """A fresh owned revocation; the remaining refresh case excludes the cycle."""
    from android_auth_fixture import observe_provider as observe_auth_provider
    from android_runtime_probe import DRAFT
    def auth(label):
        value=observe_auth_provider(probe.owner)
        atomic_json(probe.output/(label+'-auth-provider.json'),value,exclusive=True)
        return value
    original=probe.original('runtime-note.md');require(original==DRAFT.encode(),'Fresh revocation note differs')
    refresh_only=getattr(probe.args,'revocation_refresh_only',False)
    native=probe.host_snapshot('revocation-before',include_outbox=True,include_payloads=True)
    provider=probe.observe('revocation-before');accounts=auth('revocation-before')
    login=probe.snapshot('revocation-login',include_rejections=True)
    sealed=probe.ciphertext_fingerprints();require(bool(sealed),'Actual Android login ciphertext is absent')
    actor=prepared['device_id'];require(device!=actor,'Setup actor was substituted for Android')
    atomic_json(probe.output/'revocation-scene-intent.json',dict(device_id=device,setup_actor_device_id=actor,
        original_outbox=native['outbox'],original_note_sha256=hashlib.sha256(original).hexdigest(),
        automatic_retry=False,refresh_only=refresh_only,
        scope='Real owned API revoke; remaining refresh/cold excludes the accepted cycle' if refresh_only
            else 'Real owned API revoke, single original cycle and refresh; readonly/cold preservation'),exclusive=True)
    receipt=OriginalRevocation(setup).revoke(device)
    atomic_json(probe.output/'revocation-original-API-receipt.json',receipt,exclusive=True)
    revoked=auth('revocation-after-API');validate_revoked_accounts(accounts,revoked,device,actor)
    require(probe.host_snapshot('revocation-after-API',include_outbox=True,include_payloads=True)==native,
        'Independent server revocation changed local original records before a cycle')
    cycle_intent=None
    if refresh_only:
        after=native
        preserved=validate_refresh_local(native,native,original,probe.original('runtime-note.md'))
    else:
        probe.sync_phase('cycle-revoked')
        cycle_intent=original_driver_intent(probe,'cycle-revoked')
        atomic_json(probe.output/'revocation-cycle-driver-intent.json',cycle_intent,exclusive=True)
        after=probe.host_snapshot('revocation-after-cycle',include_outbox=True,include_payloads=True)
        remote=probe.observe('revocation-after-cycle')
        preserved=validate_rejected_sync(native,after,provider,remote,original,probe.original('runtime-note.md'))
    payload=probe.private(probe.root+'vault/.ainote/sync-spool/'+preserved['sha256']).stdout
    require(payload==original,'Original immutable spool payload bytes were not retained')
    probe.sync_phase('refresh-revoked')
    refresh_intent=original_driver_intent(probe,'refresh-revoked')
    require(cycle_intent is None or refresh_intent['driver_intent_id']!=cycle_intent['driver_intent_id'],'Distinct original UI actions reused a driver intent')
    atomic_json(probe.output/'revocation-refresh-driver-intent.json',refresh_intent,exclusive=True)
    rejected=probe.snapshot('revocation-refresh-rejected',include_rejections=True)
    refresh=validate_rejected_refresh(login,rejected,device)
    final_accounts=auth('revocation-after-refresh');validate_revoked_accounts(accounts,final_accounts,device,actor)
    counts=dict(revoked['requests']['counts']);counts['refresh:401']=counts.get('refresh:401',0)+1
    event=dict(action='refresh',service_status=401,delivery_status=401,fault_operation=None)
    require(final_accounts['requests']==dict(counts=counts,events=revoked['requests']['events']+[event]),
        'Exactly one real rejected refresh is not observed')
    require(probe.ciphertext_fingerprints()==sealed,'Rejected refresh replaced original sealed credentials')
    if refresh_only:
        current=probe.host_snapshot('revocation-after-refresh-local',include_outbox=True,include_payloads=True)
        require(validate_refresh_local(native,current,original,probe.original('runtime-note.md'))==preserved,
            'Rejected refresh changed the original pending boundary')
        require(probe.observe('revocation-after-refresh-local')==provider,'Refresh-only case issued a Sync request or changed provider content')
    probe.auth_phase('current-unusable',cold=True)
    require(probe.snapshot('revocation-cold-original',include_rejections=True)==rejected,
        'Cold read replaced or activated the original rejected refresh')
    require(probe.host_snapshot('revocation-cold-original',include_outbox=True,include_payloads=True)==after
        and probe.original('runtime-note.md')==original
        and probe.private(probe.root+'vault/.ainote/sync-spool/'+preserved['sha256']).stdout==payload,
        'Cold auth recovery changed the original local note/queue/payload')
    (probe.output/'revocation-original-note.md').write_bytes(original)
    atomic_json(probe.output/('sync-revocation-refresh-proof.json' if refresh_only else 'sync-revocation-proof.json'),dict(passed=True,run_id=__import__('os').environ['GITHUB_RUN_ID'],
        tools_source=probe.args.tools_commit,compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],
        service_source=prepared['source'],provider='Actual owned fixed Sync0.8 PostgreSQL/S3 over platform HTTPS',
        phases=probe.phases,actual_original_revocation=receipt,original_local=preserved,original_refresh_operation_id=refresh,
        original_driver_intents=[refresh_intent] if refresh_only else [cycle_intent,refresh_intent],driver_ids_not_substituted_for_native_or_API_IDs=True,
        actual_access_and_refresh_401=not refresh_only,actual_refresh_401=True,accepted_access_cycle_excluded=refresh_only,
        actual_Native_known_rejection_record_and_cold_preservation=True,
        actual_original_queued_payload_bytes_equal=bool(after['jobs']),actual_original_spool_payload_bytes_equal=True,
        original_ciphertext_fingerprints_unchanged=True,
        expiry_quota_invalid_certificate_or_physical_phone_verified=False,whole_A4_or_release_complete=False),exclusive=True)
