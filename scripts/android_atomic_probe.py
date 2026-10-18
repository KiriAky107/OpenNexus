"""One hosted same-UID OS-entry interruption of an original GUI Save.

The observer never rewrites native code, arguments, results or callbacks. A failed
platform capability check leaves this requirement incomplete, without changing policy.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shlex
import sqlite3
import subprocess
import tempfile
import time
import uuid
from android_auth_fixture import atomic_json
from android_runtime_probe import APP, DRIVER, require, require_hosted_emulator

NOTE='atomic-area/original.md'
ORIGINAL='# 原始文件 😀\nAtomic replacement must retain this confirmed file.\n'.encode()
UPDATED=ORIGINAL+'写入过程中断 / recover this exact pending operation\n'.encode()
NDK='30.0.16248370'
UUID=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
def digest(body):return hashlib.sha256(body).hexdigest()

def start_time(raw):
    require(type(raw) is bytes and len(raw)<4096,'ATOMIC_STAT_BOUND')
    fields=raw.rsplit(b') ',1)
    require(len(fields)==2,'ATOMIC_STAT_FORMAT')
    fields=fields[1].split();require(len(fields)>=20 and fields[19].isdigit() and int(fields[19])>0,'ATOMIC_START_TIME')
    return int(fields[19])

def target_path(home,root):
    require(home in ('/data/user/0/'+APP,'/data/data/'+APP),'ATOMIC_OWN_USER0_HOME')
    require(type(root) is str and re.fullmatch(r'\./(?:[A-Za-z0-9_.-]+/)*',root)
        and all(p not in ('.','..') for p in root[2:].split('/')[:-1]),'ATOMIC_PRIVATE_ROOT')
    result=home+'/'+root[2:]+'vault/'+NOTE
    require(len(result.encode())<480,'ATOMIC_TARGET_BOUND');return result

def decode_event(raw):
    require(type(raw) is bytes and 0<len(raw)<=2048,'ATOMIC_TRACE_RAW_BOUND')
    def unique(pairs):
        require(len(pairs)==len({key for key,_ in pairs}),'ATOMIC_TRACE_DUPLICATE_KEY');return dict(pairs)
    return json.loads(raw.decode('utf-8','strict'),object_pairs_hook=unique,
        parse_constant=lambda value:require(False,'ATOMIC_TRACE_NONFINITE'))

def trace_counts(value,matched=False):
    require(type(value) is dict and set(value)=={'entries','renames','destinations','identity_checks'}
        and all(type(v) is int and 0<=v<=4000000 for v in value.values())
        and value['destinations']<=value['renames']<=value['entries']<=1000001,'ATOMIC_TRACE_COUNTS')
    if matched:require(value['destinations']==1 and value['identity_checks']>0 and value['entries']<=1000000,'ATOMIC_TRACE_MATCH_COUNTS')
    return value

def touch_marker(raw,nonce,stage):
    require(type(raw) is bytes and len(raw)<=256 and re.fullmatch(r'[0-9a-f]{32}',nonce) and stage in ('before','after'),'ATOMIC_TOUCH_MARKER_BOUND')
    value=decode_event(raw)
    require(type(value) is dict and set(value)=={'nonce','stage','uptime_ms'} and value['nonce']==nonce and value['stage']==stage
        and type(value['uptime_ms']) is int and 0<value['uptime_ms']<2**53,'ATOMIC_TOUCH_MARKER_IDENTITY')
    return value

def collect_touch_markers(probe,nonce):
    require_hosted_emulator(probe.args.serial);require(type(nonce) is str and re.fullmatch(r'[0-9a-f]{32}',nonce),'ATOMIC_TOUCH_SCOPE')
    found={}
    for stage in ('before','after'):
        result=probe.shell('run-as',DRIVER,'cat','files/atomic-'+nonce+'-'+stage+'.json',check=False)
        value={'present':False,'returncode':result.returncode}
        if result.returncode==0:value={'present':True,'marker':touch_marker(result.stdout,nonce,stage)}
        atomic_json(probe.output/('atomic-touch-'+stage+'.json'),value,exclusive=True);found[stage]=value
    return found

def preserve_driver_output(probe,output):
    out,err=output;limit=256*1024
    require(type(out) is bytes and type(err) is bytes,'ATOMIC_DRIVER_OUTPUT_TYPE')
    (probe.output/'atomic-interrupted-driver.log').write_bytes(out[:limit])
    (probe.output/'atomic-interrupted-driver.stderr').write_bytes(err[:limit])
    return len(out)>limit or len(err)>limit

def event(value,expected,nonce,pid,uid):
    fields={'ready':{'event','nonce','pid','uid','threads'},'armed':{'event','nonce','pid'},
        'entry':{'event','nonce','pid','tid','syscall','temporary','old_inode','temp_inode','device','old_bytes','temp_bytes','trace_counts'},
        'stopped':{'event','nonce','pid','original_syscall_resumed'}}
    require(expected in fields and type(value) is dict and set(value)==fields[expected]
        and value['event']==expected and value['nonce']==nonce and type(value['pid']) is int and value['pid']==pid,'ATOMIC_ORIGINAL_TRACE_EVENT')
    if expected=='ready':
        require(type(value['uid']) is int and value['uid']==uid and type(value['threads']) is int
            and 1<=value['threads']<=256,'ATOMIC_ORIGINAL_TRACE_THREADS')
    elif expected=='entry':
        trace_counts(value['trace_counts'],True)
        require(all(type(value[k]) is int and value[k]>0 for k in ('tid','syscall','old_inode','temp_inode','device','old_bytes','temp_bytes'))
            and value['tid']<2**31 and value['syscall'] in (82,264,316)
            and value['old_inode']!=value['temp_inode'] and value['old_bytes']==len(ORIGINAL)
            and value['temp_bytes']==len(UPDATED) and type(value['temporary']) is str
            and re.fullmatch(r'\.tmp[A-Za-z0-9]{5,31}',value['temporary']),'ATOMIC_EXACT_REPLACE_ENTRY')
    elif expected=='stopped':require(value['original_syscall_resumed'] is False,'ATOMIC_SYSCALL_WAS_RESUMED')
    return value

def pending(before,after):
    require(len(before['saves'])==1 and before['saves'][0]['state']=='committed'
        and len(after['saves'])==2 and after['saves'][0]==before['saves'][0],'ATOMIC_ORIGINAL_SAVE_CHAIN')
    require(len(before['files'])==len(before['drafts'])==len(before['outbox'])==1
        and re.fullmatch(UUID,before['files'][0]['id']) and before['files'][0]['path']==NOTE
        and before['files'][0]['hash']==digest(ORIGINAL) and before['files'][0]['deleted']==0,'ATOMIC_ORIGINAL_SEED_IDENTITY')
    original=after['saves'][1];require(re.fullmatch(UUID,original['operation_id']) and original['state']=='prepared'
        and original['error'] is None and original['path']==NOTE and original['content'].encode()==UPDATED
        and original['expected']==digest(ORIGINAL),'ATOMIC_PENDING_ORIGINAL_BODY')
    require(len(after['files'])==len(after['drafts'])==1 and after['files']==before['files']
        and after['outbox']==before['outbox'] and original['file_id']==after['files'][0]['id']
        and after['drafts'][0]['file_id']==original['file_id'] and after['drafts'][0]['content'].encode()==UPDATED
        and after['drafts'][0]['id']==original['draft_id']==before['drafts'][0]['id']
        and after['drafts'][0]['saved_generation']==before['drafts'][0]['saved_generation']
        and after['drafts'][0]['generation']==original['generation']>after['drafts'][0]['saved_generation'],
        'ATOMIC_PREMATURE_FILE_OR_OUTBOX')
    for name in ('operations','journal','payloads'):
        require(all(row in after[name] for row in before[name]) and len(after[name])==len(before[name])+1,'ATOMIC_PREVIOUS_NATIVE_ROWS_CHANGED')
        rows=[v for v in after[name] if v['operation_id']==original['operation_id']];require(len(rows)==1,'ATOMIC_ORIGINAL_PENDING_ROW')
        if name=='operations':require(rows[0]['state']=='pending' and rows[0]['result'] is None and re.fullmatch(r'[0-9a-f]{64}',rows[0]['fingerprint']),'ATOMIC_ORIGINAL_FINGERPRINT')
        elif name=='journal':require(rows[0]['state']=='pending' and rows[0]['file_id']==original['file_id'] and rows[0]['expected']==original['expected'],'ATOMIC_ORIGINAL_JOURNAL')
        else:require(rows[0]['hash']==digest(UPDATED) and rows[0]['size']==len(UPDATED),'ATOMIC_ORIGINAL_PAYLOAD')
    return original

def recovered(before,held,cold):
    original=pending(before,held);identity=original['operation_id']
    require(len(cold['saves'])==2 and cold['saves'][0]==held['saves'][0]
        and cold['saves'][1]==dict(original,state='committed',error=None),'ATOMIC_RECOVERY_REPLACED_INTENT')
    require(not cold['journal'] and len(cold['files'])==len(cold['drafts'])==1 and len(cold['outbox'])==2
        and cold['files'][0]['id']==original['file_id'] and cold['files'][0]['hash']==digest(UPDATED)
        and cold['files'][0]['revision']==before['files'][0]['revision']+1
        and cold['drafts'][0]['generation']==cold['drafts'][0]['saved_generation']==original['generation'],
        'ATOMIC_RECOVERY_FILE_OR_GENERATION')
    old={r['operation_id']:r for r in held['operations']};new={r['operation_id']:r for r in cold['operations']}
    require(set(old)==set(new) and all(new[k]==v for k,v in old.items() if k!=identity)
        and new[identity]['state']=='committed' and new[identity]['fingerprint']==old[identity]['fingerprint'],
        'ATOMIC_RECOVERY_ORIGINAL_RECEIPT')
    result=json.loads(new[identity]['result']);rows=[v for v in cold['outbox'] if v['operation_id']==identity]
    require(len(rows)==1 and result['file_id']==rows[0]['file_id']==original['file_id']
        and result['hash']==rows[0]['hash']==digest(UPDATED) and result['path']==rows[0]['path']==NOTE
        and rows[0]['operation']=='put' and result['revision']==rows[0]['revision']==cold['files'][0]['revision']
        and all(v in cold['outbox'] for v in before['outbox']),'ATOMIC_RECOVERY_ONE_ORIGINAL_OUTBOX')
    return {'operation_id':identity,'fingerprint':new[identity]['fingerprint'],'file_id':original['file_id']}

def snapshot(probe,label):
    value={}
    for key,table,columns in [('drafts','drafts','id,file_id,path,base_hash,generation,saved_generation,content,edit_session'),
        ('saves','save_intents','operation_id,draft_id,generation,file_id,path,expected,content,state,error')]:
        value[key]=[dict(zip(columns.split(','),row)) for row in probe.database_rows('SELECT '+columns+' FROM '+table+' WHERE path=? ORDER BY rowid',(NOTE,))]
    require(len(value['drafts'])==1 and 1<=len(value['saves'])<=2,'ATOMIC_OWN_ROW_BOUND')
    folder=Path(tempfile.mkdtemp(prefix='original-atomic-native-',dir=probe.tools));db_path=folder/'host.sqlite3'
    for suffix in ('','-wal','-shm'):
        result=probe.private(probe.root+'vault/.ainote/host.sqlite3'+suffix,check=False)
        if result.returncode==0:Path(str(db_path)+suffix).write_bytes(result.stdout)
    with closing(sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'ATOMIC_NATIVE_INTEGRITY')
        for key,columns in [('journal','operation_id,file_id,path,expected,state'),('outbox','operation_id,file_id,revision,path,hash,operation,state'),('files','id,path,hash,revision,deleted')]:
            value[key]=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM '+key+' WHERE path=? ORDER BY rowid',(NOTE,))]
        ids=[s['operation_id'] for s in value['saves']]
        for key,columns in [('operations','operation_id,fingerprint,state,result'),('payloads','operation_id,hash,size')]:
            value[key]=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM '+key+' WHERE operation_id IN ('+','.join('?' for _ in ids)+') ORDER BY rowid',ids)]
    atomic_json(probe.output/(label+'-native.json'),value,exclusive=True);return value

class OriginalTrace:
    def __init__(self,probe,pid,uid,ticks,target,nonce,binary):
        self.probe,self.pid,self.uid,self.nonce=probe,pid,uid,nonce;self.buffer=b'';self.ordinal=0;self.ended_output=None;self.ended_validation_failed=False
        remote='cache/original-atomic-'+nonce
        require(probe.shell('run-as',APP,'test','!','-e',remote,check=False).returncode==0,'ATOMIC_TRACE_TOOL_EXISTS')
        probe.device('shell','-T',shlex.join(('run-as',APP,'sh','-c','set -C; umask 077; cat > '+remote)),input=binary.read_bytes())
        probe.shell('run-as',APP,'chmod','700',remote)
        require(probe.shell('run-as',APP,'sha256sum',remote).stdout.decode().split()[0]==digest(binary.read_bytes()),'ATOMIC_TRACE_BINARY_BYTES')
        command=['run-as',APP,remote,str(pid),str(uid),str(ticks),nonce,target]
        self.process=subprocess.Popen([str(probe.adb),'-s',probe.args.serial,'shell','-T',shlex.join(command)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    def read(self,kind,timeout=30):
        until=time.monotonic()+timeout
        while b'\n' not in self.buffer and time.monotonic()<until:
            readable,_,_=select.select([self.process.stdout],[],[],max(0,until-time.monotonic()))
            require(readable,'ATOMIC_ORIGINAL_TRACE_OBSERVATION_TIMEOUT')
            block=os.read(self.process.stdout.fileno(),2048);require(block,'ATOMIC_ORIGINAL_TRACE_ENDED')
            self.buffer+=block;require(len(self.buffer)<=4096,'ATOMIC_ORIGINAL_TRACE_BOUND')
        require(b'\n' in self.buffer,'ATOMIC_ORIGINAL_TRACE_LINE');line,self.buffer=self.buffer.split(b'\n',1)
        value=decode_event(line);self.ordinal+=1
        atomic_json(self.probe.output/('atomic-trace-event-'+str(self.ordinal)+'.json'),value,exclusive=True)
        return event(value,kind,self.nonce,self.pid,self.uid)
    def send(self,value):
        require(value in (b'arm\n',b'stop\n'),'ATOMIC_TRACE_COMMAND');self.process.stdin.write(value);self.process.stdin.flush()
    def finish(self):
        if self.ended_output is not None:
            require(not self.ended_validation_failed,'ATOMIC_OBSERVER_REMAINDER_PREVIOUSLY_REFUSED')
            return self.ended_output
        require(self.process.poll() is not None,'ATOMIC_OBSERVER_STILL_LIVE')
        out=self.buffer+self.process.stdout.read(4097);err=self.process.stderr.read(4097)
        require(len(out)<=4096 and len(err)<=4096,'ATOMIC_OBSERVER_REMAINDER_BOUND')
        self.ended_output=(out,err)
        (self.probe.output/'atomic-observer-remainder.stdout').write_bytes(out)
        (self.probe.output/'atomic-observer-ended.stderr').write_bytes(err)
        # Drain each actual pipe once. communicate() closes these streams and
        # caused the former success cleanup to read an already-closed stderr.
        self.process.stdout.close();self.process.stderr.close()
        try:
            for line in out.splitlines():
                value=decode_event(line)
                require(type(value) is dict and set(value)=={'event','nonce','pid','held','raw_error_exported','trace_counts'}
                    and value['event']=='refused' and value['nonce']==self.nonce and value['pid']==self.pid
                    and type(value['held']) is bool and value['raw_error_exported'] is False,'ATOMIC_OBSERVER_REMAINDER_IDENTITY')
                trace_counts(value['trace_counts'])
        except Exception:
            self.ended_validation_failed=True;raise
        return self.ended_output

def build_observer(probe):
    root=Path(__file__).resolve().parent;source=root/'android-runtime-native/atomic_replace_observer.c'
    host=probe.tools/'atomic-host-check';binary=probe.tools/'atomic-observer'
    check=probe.command(['cc','-std=c11','-O1','-Wall','-Wextra',source,'-o',host],check=False)
    (probe.output/'atomic-host-build.stderr').write_bytes(check.stderr)
    atomic_json(probe.output/'atomic-host-build.json',{'exit_code':check.returncode,'source_sha256':digest(source.read_bytes())},exclusive=True)
    require(check.returncode==0,'ATOMIC_HOST_C_COMPILE')
    check=probe.command([host,'--self-test'],check=False)
    (probe.output/'atomic-observer-data.txt').write_bytes(check.stdout)
    require(check.returncode==0 and check.stdout==b'ORIGINAL_ATOMIC_OBSERVER_DATA_OK 15\n','ATOMIC_HOST_C_DATA_CHECK')
    compiler=probe.args.sdk/'ndk'/NDK/'toolchains/llvm/prebuilt/linux-x86_64/bin/x86_64-linux-android24-clang'
    require(compiler.is_file() and compiler.resolve().is_relative_to(probe.args.sdk.resolve()),'ATOMIC_PINNED_NDK_REQUIRED')
    result=probe.command([compiler,'-std=c11','-O1','-Wall','-Wextra','-Werror=implicit-function-declaration',source,'-o',binary],check=False)
    (probe.output/'atomic-android-build.stderr').write_bytes(result.stderr)
    atomic_json(probe.output/'atomic-android-build.json',{'exit_code':result.returncode,'NDK':NDK},exclusive=True)
    require(result.returncode==0,'ATOMIC_ANDROID_C_COMPILE')
    body=binary.read_bytes();require(0<len(body)<=2*1024**2 and body[:4]==b'\x7fELF' and body[4]==2 and body[18:20]==b'\x3e\x00','ATOMIC_OBSERVER_ELF_IDENTITY')
    atomic_json(probe.output/'atomic-observer-source.json',{'tools_commit':probe.args.tools_commit,'source_sha256':digest(source.read_bytes()),'binary_sha256':digest(body),'NDK':NDK,'original_APK_not_rebuilt':True},exclusive=True)
    return binary

def run_atomic(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='write-interruption','ATOMIC_EXPLICIT_SCOPE')
    report={'passed':False,'run_id':os.environ['GITHUB_RUN_ID'],'tools_source':probe.args.tools_commit,'compiled_source':probe.args.commit,
        'APK_sha256':probe.binding['sha256'],'physical_phone_upgrade_or_all_A2_complete':False,'no_native_code_or_callback_replacement':True}
    tracer=driver=None;driver_output=None;markers=None;nonce=None
    def intent(stage,**data):
        report['stage']=stage;atomic_json(probe.output/(stage+'-original-intent.json'),{'scope':'write-interruption','stage':stage,'path':NOTE,'automatic_retry':False,**data},exclusive=True)
    try:
        binary=build_observer(probe);probe.enable_test_ime();probe.phase('atomic-init',keep_running=True)
        intent('atomic-seed');probe.phase('atomic-seed',continue_process=True,keep_running=True)
        before=snapshot(probe,'atomic-seed');require(probe.original(NOTE)==ORIGINAL,'ATOMIC_ORIGINAL_SEED_BYTES')
        intent('atomic-edit');probe.phase('atomic-edit',continue_process=True,keep_running=True)
        home=probe.shell('run-as',APP,'pwd').stdout.decode('ascii').strip();home=probe.shell('readlink','-f',home).stdout.decode('ascii').strip()
        uid=int(probe.shell('run-as',APP,'id','-u').stdout.strip());require(10000<=uid<100000,'ATOMIC_ORIGINAL_UID')
        raw_pid=probe.shell('pidof',APP).stdout.strip();require(re.fullmatch(rb'[1-9][0-9]{0,9}',raw_pid),'ATOMIC_ORIGINAL_PID');pid=int(raw_pid)
        ticks=start_time(probe.shell('run-as',APP,'cat','/proc/'+str(pid)+'/stat').stdout)
        target=target_path(home,probe.root);nonce=uuid.uuid4().hex
        require(probe.shell('run-as',APP,'readlink','-f',target.rsplit('/',1)[0]).stdout.decode('ascii').strip()==target.rsplit('/',1)[0],
            'ATOMIC_NONCANONICAL_PARENT')
        intent('atomic-observer',original_pid=pid,original_uid=uid,start_ticks=ticks,nonce=nonce)
        tracer=OriginalTrace(probe,pid,uid,ticks,target,nonce,binary);tracer.read('ready');tracer.send(b'arm\n');tracer.read('armed')
        intent('atomic-save',original_pid=pid,nonce=nonce,requested_sha256=digest(UPDATED))
        driver=subprocess.Popen([str(probe.adb),'-s',probe.args.serial,'shell','-T',shlex.join(('am','instrument','-w','-e','phase','atomic-save','-e','atomic_nonce',nonce,DRIVER+'/.RuntimeProbe'))],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        report['original_GUI_Save_invocations']=1
        entry=tracer.read('entry');require(driver.poll() is None,'ATOMIC_ORIGINAL_DRIVER_ALREADY_ENDED')
        held=snapshot(probe,'atomic-entry');original=pending(before,held)
        require(probe.original(NOTE)==ORIGINAL,'ATOMIC_ORIGINAL_REPLACED_BEFORE_STOP')
        temp=probe.private(probe.root+'vault/atomic-area/'+entry['temporary']).stdout
        require(temp==UPDATED,'ATOMIC_ORIGINAL_TEMP_BYTES');(probe.output/'atomic-original-temp.md').write_bytes(temp)
        spool=probe.private(probe.root+'vault/.ainote/sync-spool/'+digest(UPDATED),check=False)
        # The exact frozen native spool path is admitted by the source verifier;
        # absence refuses rather than weakening the original payload requirement.
        require(spool.returncode==0 and spool.stdout==UPDATED,'ATOMIC_ORIGINAL_PAYLOAD_BYTES')
        (probe.output/'atomic-original-payload.md').write_bytes(spool.stdout)
        intent('atomic-original-stop',operation_id=original['operation_id'],original_pid=pid,nonce=nonce)
        tracer.send(b'stop\n');tracer.read('stopped');tracer.process.stdin.close();tracer.process.wait(timeout=15)
        stdout,stderr=tracer.finish()
        require(tracer.process.returncode==0 and not stdout and not stderr,'ATOMIC_ORIGINAL_TRACE_EXIT')
        until=time.monotonic()+10
        while time.monotonic()<until and probe.shell('pidof',APP,check=False).stdout.strip():time.sleep(.1)
        require(not probe.shell('pidof',APP,check=False).stdout.strip(),'ATOMIC_ORIGINAL_PROCESS_NOT_ABSENT')
        probe.shell('am','force-stop',DRIVER);driver_output=driver.communicate(timeout=15);out,err=driver_output
        require(not preserve_driver_output(probe,driver_output),'ATOMIC_DRIVER_OUTPUT_TRUNCATED')
        require(b'OPENNEXUS_RUNTIME_PHASE_OK atomic-save' not in out,'ATOMIC_INTERRUPTED_GUI_FALSLY_SUCCEEDED')
        markers=collect_touch_markers(probe,nonce);require(markers['before']['present'],'ATOMIC_ORIGINAL_TOUCH_CHECKPOINT_MISSING')
        stopped=snapshot(probe,'atomic-stopped');require(stopped==held and probe.original(NOTE)==ORIGINAL,'ATOMIC_UNACKNOWLEDGED_ORIGINAL_CHANGED')
        intent('atomic-cold',operation_id=original['operation_id'],original_pid=pid)
        probe.phase('atomic-cold',keep_running=True);cold=snapshot(probe,'atomic-cold');decision=recovered(before,held,cold)
        cold_pid=int(probe.shell('pidof',APP).stdout.strip());require(cold_pid!=pid and probe.original(NOTE)==UPDATED,'ATOMIC_COLD_EXACT_ORIGINAL')
        report.update(passed=True,original_PID=pid,cold_PID=cold_pid,original_decision=decision,actual_original_replace_syscall_entry=entry,
            interrupted_original_GUI_invocations=1,original_touch_markers=markers,no_new_Save_or_replayed_native_apply=True,old_target_sha256=digest(ORIGINAL),recovered_sha256=digest(UPDATED))
    except Exception as error:
        report['failure_class']=type(error).__name__
        if probe.root is not None:
            try:
                probe.original(NOTE);snapshot(probe,'atomic-failure-readonly')
            except Exception as observation:report['failure_readonly_observation_class']=type(observation).__name__
        raise
    finally:
        # Cleanup owns only this fresh installed app/driver/observer. A refused
        # capability or absent proof never counts as an actual midwrite pass.
        try:
            probe.force_stop()
            if driver is not None and driver.poll() is None:probe.shell('am','force-stop',DRIVER,check=False)
            if driver is not None and driver_output is None:
                try:
                    driver_output=driver.communicate(timeout=15)
                    report['driver_output_truncated']=preserve_driver_output(probe,driver_output)
                except (OSError,subprocess.TimeoutExpired):report['driver_cleanup_unconfirmed']=True;report['passed']=False
            if driver is not None and markers is None and nonce is not None:
                try:report['original_touch_markers']=collect_touch_markers(probe,nonce)
                except Exception as observation:report['touch_observation_class']=type(observation).__name__
            if tracer is not None:
                if tracer.process.poll() is None:
                    try:
                        if not tracer.process.stdin.closed:tracer.process.stdin.close()
                        tracer.process.wait(timeout=15)
                    except (OSError,subprocess.TimeoutExpired):report['observer_cleanup_unconfirmed']=True;report['passed']=False
                if tracer.process.poll() is not None:
                    try:tracer.finish()
                    except Exception as observation:report['observer_diagnostic_class']=type(observation).__name__;report['passed']=False
        finally:atomic_json(probe.output/'atomic-write-interruption-proof.json',report,exclusive=True)
