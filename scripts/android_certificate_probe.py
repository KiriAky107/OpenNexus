"""A new owned untrusted TLS endpoint; no trust-store change or HTTP error stub."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import hashlib,json,os,re,shlex,socket,sqlite3,ssl,subprocess,tempfile,threading,time,uuid
from android_auth_fixture import atomic_json,read_json,require
from android_runtime_probe import APP,DRIVER,DRAFT,require_hosted_emulator
ENDPOINT='https://10.0.2.2:19074'


class UntrustedOrigin:
    def __init__(self,owner):
        from android_auth_fixture import owned_root
        require(owner.resolve()==owned_root(),'OWNED_CERTIFICATE_ROOT_INVALID')
        self.root=owner/'untrusted-certificate';require(not self.root.exists(),'OWNED_CERTIFICATE_ALREADY_PREPARED')
        self.root.mkdir(mode=0o700);self.stop=threading.Event();self.lock=threading.Lock()
        self.counts=dict(connections=0,verified_handshakes=0,rejected_handshakes=0,http_bytes_observed=0)
        self.failures=[];self.listener=None;self.thread=None

    def prepare(self):
        key=self.root/'key.pem';certificate=self.root/'certificate.pem'
        old=os.umask(0o077)
        try:
            reply=subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-sha256','-nodes',
                '-keyout',str(key),'-out',str(certificate),'-days','1','-subj','/CN=owned-android-untrusted',
                '-addext','subjectAltName=IP:10.0.2.2,IP:127.0.0.1'],capture_output=True,timeout=30)
        finally:os.umask(old)
        require(reply.returncode==0 and key.is_file() and certificate.is_file(),'OWNED_CERTIFICATE_PREPARE_FAILED')
        der=ssl.PEM_cert_to_DER_cert(certificate.read_text('ascii'));self.fingerprint=hashlib.sha256(der).hexdigest()
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate,key)
        self.listener=socket.socket();self.listener.bind(('127.0.0.1',19074));self.listener.listen(4);self.listener.settimeout(.2)
        self.thread=threading.Thread(target=self.serve,args=(context,),daemon=True);self.thread.start()
        # Preserve this independent runner rejection; never reset its counts or
        # confuse it with the later original Android connection.
        try:
            with socket.create_connection(('127.0.0.1',19074),timeout=5) as connection:
                ssl.create_default_context().wrap_socket(connection,server_hostname='10.0.2.2')
            raise RuntimeError('OWNED_CERTIFICATE_UNEXPECTED_TRUST')
        except ssl.SSLCertVerificationError as failure:
            require(failure.verify_code==18,'OWNED_CERTIFICATE_NOT_SELF_SIGNED_REJECTION')
        deadline=time.monotonic()+5
        while self.snapshot()['counts']['rejected_handshakes']<1:
            require(time.monotonic()<deadline,'OWNED_CERTIFICATE_READINESS_TIMEOUT');time.sleep(.05)
        before=self.snapshot();require(before['counts']==dict(connections=1,verified_handshakes=0,rejected_handshakes=1,http_bytes_observed=0),
            'OWNED_CERTIFICATE_READINESS_CHANGED')
        return dict(certificate_sha256=self.fingerprint,SAN_IPs=['10.0.2.2','127.0.0.1'],
            fresh_self_signed=True,runner_default_trust_rejected=True,trust_store_modified=False,baseline=before)

    def serve(self,context):
        while not self.stop.is_set():
            try:connection,_=self.listener.accept()
            except socket.timeout:continue
            except OSError:break
            with self.lock:self.counts['connections']+=1
            connection.settimeout(5)
            try:
                with context.wrap_socket(connection,server_side=True) as stream:
                    with self.lock:self.counts['verified_handshakes']+=1
                    # Never collect HTTP headers, bodies or credentials. Any
                    # application byte is an unexpected failure, not success.
                    body=stream.recv(1)
                    with self.lock:self.counts['http_bytes_observed']+=len(body)
            except ssl.SSLError as error:
                reason=error.reason if type(error.reason) is str and re.fullmatch(r'[A-Z0-9_]{1,100}',error.reason) else 'OTHER_TLS_ERROR'
                with self.lock:self.counts['rejected_handshakes']+=1;self.failures.append(reason)
            except OSError:
                with self.lock:self.failures.append('PEER_IO_FAILURE')
            finally:connection.close()

    def snapshot(self):
        with self.lock:return dict(counts=dict(self.counts),tls_reasons=list(self.failures),raw_request_recorded=False)

    def close(self):
        self.stop.set()
        if self.listener:self.listener.close()
        if self.thread:self.thread.join(7);require(not self.thread.is_alive(),'OWNED_CERTIFICATE_STOP_UNCONFIRMED')


def validate_rejected_handshake(before,after):
    require(before['raw_request_recorded'] is False and after['raw_request_recorded'] is False,'Certificate observer collected request data')
    expected=dict(before['counts']);expected['connections']+=1;expected['rejected_handshakes']+=1
    require(after['counts']==expected and before['counts']['verified_handshakes']==before['counts']['http_bytes_observed']==0,
        'One original Android TLS rejection without HTTP delivery is absent')
    require(len(after['tls_reasons'])==len(before['tls_reasons'])+1 and after['tls_reasons'][:-1]==before['tls_reasons']
        and after['tls_reasons'][-1] in ('TLSV1_ALERT_UNKNOWN_CA','SSLV3_ALERT_CERTIFICATE_UNKNOWN','SSLV3_ALERT_BAD_CERTIFICATE'),
        'The actual peer rejection is not a certificate alert')


def validate_certificate_intent(native,account):
    require(len(native['intents'])==1 and not native['rejections'],'TLS failure was fabricated as a server HTTP rejection')
    intent=native['intents'][0];operation=intent['operation_id'];selection=native['selection']
    require(str(uuid.UUID(operation))==operation and intent['kind']=='login' and intent['phase']=='login_pending'
        and intent['device_id'] is None and intent['previous_operation'] is None and intent['generation']==1
        and intent['endpoint'].rstrip('/')==ENDPOINT and intent['account']==account,
        'Original untrusted login activated or replaced an identity')
    require(selection is not None and selection['operation_id']==operation and selection['generation']==1
        and selection['revision']==1 and selection['endpoint']==intent['endpoint'] and selection['account']==account,
        'Original unknown selection was replaced')
    return operation


def validate_cold_certificate_intent(before,after):
    require(type(before) is dict and len(before.get('intents',[]))==1
        and before['intents'][0].get('phase')=='login_pending','Original certificate pending boundary missing')
    expected=deepcopy(before)
    expected['intents'][0]['phase']='unknown'
    require(after==expected,'Cold certificate recovery replaced an original identity or activated a session')
    return expected['intents'][0]['operation_id']


def local_snapshot(probe,label):
    path='runtime-note.md';value={}
    for key,table,columns in [('drafts','drafts','id,file_id,path,base_hash,generation,saved_generation,content'),
            ('saves','save_intents','operation_id,draft_id,generation,file_id,path,expected,content,state,error')]:
        value[key]=[dict(zip(columns.split(','),row)) for row in probe.database_rows('SELECT '+columns+' FROM '+table+' WHERE path=? ORDER BY rowid',(path,))]
    require(len(value['drafts'])==len(value['saves'])==1 and value['saves'][0]['state']=='committed','Certificate original note is not uniquely saved')
    folder=Path(tempfile.mkdtemp(prefix='owned-certificate-note-',dir=probe.tools));database=folder/'host.sqlite3'
    for suffix in ('','-wal','-shm'):
        reply=probe.private(probe.root+'vault/.ainote/host.sqlite3'+suffix,check=False)
        if reply.returncode==0:Path(str(database)+suffix).write_bytes(reply.stdout)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'Certificate Native metadata integrity failed')
        for key,columns in [('journal','operation_id,file_id,path,expected,state'),('outbox','operation_id,file_id,revision,path,hash,operation,state'),('files','id,path,hash,revision,deleted')]:
            value[key]=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM '+key+' WHERE path=? ORDER BY rowid LIMIT 17',(path,))]
        ids=[value['saves'][0]['operation_id']]
        for key,columns in [('operations','operation_id,fingerprint,state,result'),('payloads','operation_id,hash,size')]:
            value[key]=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM '+key+' WHERE operation_id=? ORDER BY rowid LIMIT 17',ids)]
        require(all(len(value[key])==1 for key in ('files','outbox','operations')),'Certificate original Native identity differs')
        require(db.execute('SELECT COUNT(*) FROM sync_bindings').fetchone()==(0,),'Certificate fixture unexpectedly associated a vault')
    atomic_json(probe.output/(label+'-local.json'),value,exclusive=True);return value


def run_certificate(probe):
    require(probe.args.scope=='certificate','Explicit certificate scope required');require_hosted_emulator(probe.args.serial)
    probe.boot();probe.enable_test_ime();probe.phase('ime-seed');probe.restore_test_ime()
    require(probe.original('runtime-note.md')==DRAFT.encode(),'Certificate original UI note differs')
    local=local_snapshot(probe,'certificate-before');provider=probe.observe('certificate-before')
    digest=hashlib.sha256(DRAFT.encode()).hexdigest();saved=local['saves'][0];file=local['files'][0]
    require(file['hash']==digest and file['id']==local['drafts'][0]['file_id'] and file['revision']==1
        and local['operations'][0]['operation_id']==local['outbox'][0]['operation_id']==saved['operation_id']
        and local['operations'][0]['state']=='committed'
        and local['payloads']==[dict(operation_id=saved['operation_id'],hash=digest,size=len(DRAFT.encode()))],
        'Certificate original file/Save/spool identity differs')
    spool=probe.private(probe.root+'vault/.ainote/sync-spool/'+digest).stdout
    require(spool==DRAFT.encode(),'Certificate original spool bytes differ')
    require(all(not u['devices'] and not u['session_counts'] for u in provider['users'])
        and provider['requests']==dict(counts={},events=[]),'Certificate provider was not a fresh owned account boundary')
    sealed=probe.ciphertext_fingerprints();require(not sealed,'Fresh certificate fixture already had sealed credentials')
    server=UntrustedOrigin(probe.owner)
    try:
        origin=server.prepare();atomic_json(probe.output/'certificate-origin.json',origin,exclusive=True)
        configuration=read_json(probe.owner/'device-private.json')
        value=dict(configuration,endpoint=ENDPOINT,scope='owned-untrusted-certificate',certificate_sha256=origin['certificate_sha256'])
        probe.shell('run-as',DRIVER,'mkdir','-p','no_backup')
        probe.device('shell','-T','run-as '+DRIVER+' sh -c '+shlex.quote('umask 077; test ! -e no_backup/owned-auth.json && cat > no_backup/owned-auth.json'),input=json.dumps(value).encode())
        atomic_json(probe.output/'certificate-login-original-intent.json',dict(driver_dispatch_id=str(uuid.uuid4()),
            native_operation_id_assigned_by_driver=False,certificate_sha256=origin['certificate_sha256'],automatic_retry=False,
            fresh_rejected_login_is_setup_for_remaining_local_cold=True),exclusive=True)
        probe.auth_phase('invalid-certificate')
        after=server.snapshot();validate_rejected_handshake(origin['baseline'],after)
        atomic_json(probe.output/'certificate-after-login.json',after,exclusive=True)
        native=probe.snapshot('certificate-rejected',include_rejections=True)
        operation=validate_certificate_intent(native,configuration['accounts'][0]['username'])
        require(probe.ciphertext_fingerprints()==sealed and probe.observe('certificate-after-login')==provider
            and local_snapshot(probe,'certificate-after-login')==local and probe.original('runtime-note.md')==DRAFT.encode(),
            'Rejected TLS changed provider, credentials or original local data')
        probe.auth_phase('current-unusable',cold=True)
        cold_native=probe.snapshot('certificate-cold',include_rejections=True)
        cold_local=local_snapshot(probe,'certificate-cold')
        cold_raw=probe.original('runtime-note.md')
        cold_provider=probe.observe('certificate-cold')
        cold_sealed=probe.ciphertext_fingerprints();cold_origin=server.snapshot()
        cold_spool=probe.private(probe.root+'vault/.ainote/sync-spool/'+digest).stdout
        atomic_json(probe.output/'certificate-cold-ciphertext-fingerprints.json',cold_sealed,exclusive=True)
        atomic_json(probe.output/'certificate-cold-origin.json',cold_origin,exclusive=True)
        (probe.output/'certificate-cold-original-spool.md').write_bytes(cold_spool)
        validate_cold_certificate_intent(native,cold_native)
        require(cold_local==local and cold_raw==DRAFT.encode() and cold_provider==provider
            and cold_sealed==sealed and cold_origin==after and cold_spool==spool,
            'Cold unusable read retried the original login or changed original data')
        (probe.output/'certificate-original-note.md').write_bytes(DRAFT.encode())
        atomic_json(probe.output/'certificate-runtime-proof.json',dict(passed=True,run_id=os.environ['GITHUB_RUN_ID'],
            tools_source=probe.args.tools_commit,compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],
            service_source=provider['source'],phases=probe.phases,original_native_operation_id=operation,
            original_file_id=file['id'],original_Save_operation_id=saved['operation_id'],
            actual_platform_untrusted_certificate_rejection=True,zero_HTTP_application_bytes_at_untrusted_server=True,
            no_service_sessions_or_credentials_activated=True,original_local_note_and_Native_records_preserved=True,
            original_spool_bytes_preserved=True,
            cold_original_pending_transition='login_pending_to_unknown_same_original_identity',
            declared_fresh_self_signed_SAN_valid_certificate=True,trust_store_or_validation_changed=False,
            fresh_rejected_login_is_setup_for_remaining_local_cold=True,
            raw_request_or_secrets_exported=False,physical_or_whole_A4_or_release_complete=False),exclusive=True)
    finally:
        server.close();atomic_json(probe.output/'certificate-server-cleanup.json',dict(stopped=True,final=server.snapshot(),owned_listener_only=True),exclusive=True)
