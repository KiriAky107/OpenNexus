"""Observe one original native Canvas gesture; never create or retry its input.

The driver pauses before and after its own original pointer stream. Only bounded
geometry is read from that same app process. An acknowledgement goes to the
separate driver, never to the product or its model/native command bridge.
"""
import hashlib,http.client,json,os,queue,re,shlex,subprocess,threading,time,uuid
from pathlib import Path
from urllib.parse import urlsplit
from android_auth_fixture import atomic_json
from android_runtime_probe import APP,DRIVER,require,require_hosted_emulator
from android_canvas_view_geometry import PHASES,geometry,calibrate,frame_check


def original_identity(probe):
    raw=probe.shell('pidof',APP).stdout.strip()
    require(re.fullmatch(rb'[1-9][0-9]{0,9}',raw),'VIEW_ORIGINAL_PID_UNAVAILABLE')
    return probe.native_process_identity(int(raw))


def read_original_geometry(probe,nonce,phase,stage,identity):
    require(phase in PHASES and stage in ('before','after') and str(uuid.UUID(nonce))==nonce,'VIEW_READ_SCOPE')
    require(original_identity(probe)==identity,'VIEW_ORIGINAL_PROCESS_CHANGED')
    native_name=phase+'-'+stage+'-native-view.json'
    raw=probe.device('exec-out','run-as',DRIVER,'cat','files/'+native_name).stdout
    require(0<len(raw)<=2048,'VIEW_ORIGINAL_NATIVE_METADATA_BOUND')
    native=json.loads(raw);port=None
    require(native.get('schema')==1 and native.get('scope')=='canvas-view' and native.get('owner')==APP
        and native.get('dispatch_id')==nonce and native.get('phase')==phase and native.get('stage')==stage,'VIEW_NATIVE_ORIGIN_REFUSED')
    record=dict(dispatch_id=nonce,phase=phase,stage=stage,original_process=identity,
        original_native_sha256=hashlib.sha256(raw).hexdigest(),read_only=True,
        product_command_or_input_dispatched=False,automatic_retry=False)
    target=probe.output/(phase+'-'+stage+'-read.json')
    try:
        port=probe.device('forward','tcp:0','localabstract:webview_devtools_remote_'+str(identity['pid'])).stdout.decode().strip()
        require(re.fullmatch(r'[0-9]{4,5}',port) and 1024<=int(port)<=65535,'VIEW_FORWARD_PORT_REFUSED')
        connection=http.client.HTTPConnection('127.0.0.1',int(port),timeout=8)
        try:
            connection.request('GET','/json/list');response=connection.getresponse();body=response.read(1024**2+1)
            require(response.status==200 and len(body)<=1024**2,'VIEW_METADATA_UNAVAILABLE')
            pages=[x for x in json.loads(body) if x.get('type')=='page' and x.get('title')=='OpenNexus']
        finally:connection.close()
        require(len(pages)==1,'VIEW_ORIGINAL_PAGE_AMBIGUOUS')
        socket=urlsplit(pages[0]['webSocketDebuggerUrl'])
        require(socket.scheme=='ws' and socket.hostname in ('127.0.0.1','localhost') and socket.port==int(port)
            and re.fullmatch(r'/devtools/page/[A-Za-z0-9_-]+',socket.path) and not socket.query and not socket.fragment
            and not socket.username and not socket.password,'VIEW_FOREIGN_PAGE_REFUSED')
        result=probe.command(['node','--max-old-space-size=128',Path(__file__).resolve().parent/'android-runtime/read_canvas_view.mjs',
            'ws://127.0.0.1:'+port+socket.path,nonce,stage],timeout=18,check=False)
        require(result.returncode==0 and 0<len(result.stdout)<=16384,'VIEW_ORIGINAL_RENDERER_METADATA_UNAVAILABLE')
        value=geometry(json.loads(result.stdout),nonce,stage);calibrate(value,native,phase)
        require(original_identity(probe)==identity,'VIEW_ORIGINAL_PROCESS_CHANGED_AFTER_READ')
        for name,original in ((native_name,raw),(phase+'-'+stage+'-renderer-view.json',result.stdout)):
            with (probe.output/name).open('xb') as stream:stream.write(original);stream.flush();os.fsync(stream.fileno())
        ack=dict(passed=True,dispatch_id=nonce,phase=phase,stage=stage,
            native_sha256=record['original_native_sha256'],renderer_sha256=hashlib.sha256(result.stdout).hexdigest())
        owned_port=port;port=None
        record['own_forward_removed']=probe.device('forward','--remove','tcp:'+owned_port,check=False).returncode==0
        require(record['own_forward_removed'],'VIEW_OWN_FORWARD_CLEANUP_UNCONFIRMED')
        # The exclusive intent precedes one atomic publication to driver files.
        # It is an observation acknowledgement, not a product operation ID.
        atomic_json(probe.output/(phase+'-'+stage+'-ack-intent.json'),dict(ack,automatic_retry=False,
            target_driver=DRIVER,native_operation_id=False),exclusive=True)
        filename='files/'+phase+'-'+stage+'-view-ack.json';temporary=filename+'.partial'
        script='umask 077; test ! -e '+shlex.quote(filename)+' && test ! -e '+shlex.quote(temporary)+ \
            ' && cat > '+shlex.quote(temporary)+' && mv '+shlex.quote(temporary)+' '+shlex.quote(filename)
        probe.device('shell','-T',shlex.join(('run-as',DRIVER,'sh','-c',script)),
            input=(json.dumps(ack,separators=(',',':'))+'\n').encode())
        record.update(passed=True,acknowledgement=ack)
        return value,native
    except Exception as error:
        record.update(passed=False,error_class=type(error).__name__,raw_error_returned=False)
        raise RuntimeError('VIEW_ORIGINAL_READ_UNCONFIRMED') from None
    finally:
        if port and re.fullmatch(r'[0-9]{4,5}',port):
            try:record['own_forward_removed']=probe.device('forward','--remove','tcp:'+port,check=False).returncode==0
            except Exception:record['own_forward_removed']=False
        atomic_json(target,record,exclusive=True)


def instrument_original_view(probe,phase):
    require_hosted_emulator(probe.args.serial)
    require(probe.args.scope in ('canvas-view','canvas-view-remaining') and phase in
        (('view-background',) if probe.args.scope=='canvas-view-remaining' else PHASES),'VIEW_INSTRUMENT_SCOPE')
    require(re.fullmatch(re.escape(APP)+r'/[A-Za-z0-9_.]+',probe.activity),'VIEW_RESUME_ACTIVITY_SCOPE')
    nonce=str(uuid.uuid4());identity=original_identity(probe)
    intent=dict(dispatch_id=nonce,phase=phase,scope=probe.args.scope,original_process=identity,
        compiled_source=probe.args.commit,tools_source=probe.args.tools_commit,
        real_native_pointer_stream=True,CDP_geometry_read_only=True,automatic_retry=False)
    atomic_json(probe.output/(phase+'-observation-intent.json'),intent,exclusive=True)
    command=[str(probe.adb),'-s',probe.args.serial,'shell','am','instrument','-w','-e','phase',phase,
        '-e','view_dispatch_id',nonce,'-e','motion_activity',probe.activity,DRIVER+'/.RuntimeProbe']
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    signals=queue.Queue(maxsize=2);done=threading.Event();state={'overflow':False,'signals':0};chunks=[]
    marker=b'INSTRUMENTATION_STATUS: owned_canvas_view_ready='
    def read_original():
        total=0
        try:
            for line in iter(process.stdout.readline,b''):
                total+=len(line)
                if total>1024**2:state['overflow']=True;break
                chunks.append(line)
                if line.startswith(marker):
                    state['signals']+=1
                    try:signals.put_nowait(line[len(marker):].strip())
                    except queue.Full:state['overflow']=True;break
        finally:done.set()
    reader=threading.Thread(target=read_original,daemon=True);reader.start()
    observed=[];failure=None;stages=[]
    try:
        deadline=time.monotonic()+100
        for stage in ('before','after'):
            expected=(nonce+':'+phase+':'+stage).encode()
            while True:
                require(not state['overflow'] and time.monotonic()<deadline,'VIEW_ORIGINAL_STREAM_TIMEOUT')
                try:signal=signals.get(timeout=.05);break
                except queue.Empty:require(not done.is_set() or process.poll() is None,'VIEW_ORIGINAL_DRIVER_ENDED')
            require(signal==expected and process.poll() is None,'VIEW_ORIGINAL_SIGNAL_ORDER_OR_PROCESS')
            observed.append(read_original_geometry(probe,nonce,phase,stage,identity));stages.append(stage)
        process.wait(timeout=max(.1,deadline-time.monotonic()));reader.join(timeout=3)
        require(done.is_set() and not state['overflow'] and state['signals']==2 and signals.empty()
            and original_identity(probe)==identity,'VIEW_ORIGINAL_STREAM_OR_IDENTITY_CHANGED')
        text=b''.join(chunks)
        require(b'OPENNEXUS_RUNTIME_PHASE_OK '+phase.encode() in text and b'OPENNEXUS_RUNTIME_FAILED' not in text,
            'VIEW_ORIGINAL_NATIVE_PHASE_FAILED')
    except Exception as error:failure=type(error).__name__
    finally:
        if process.poll() is None:
            try:process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=4)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=4)
        reader.join(timeout=3)
        frame_observations=[]
        for stage in ('before','after'):
            try:
                frame_observations.append(read_original_frame_check(probe,nonce,phase,stage))
            except Exception as error:
                frame_observations.append(dict(stage=stage,available=False,error_class=type(error).__name__))
                if failure is None:failure='OriginalFrameCheckUnavailable'
        atomic_json(probe.output/(phase+'-observation-stream.json'),dict(intent,stages=stages,
            readiness_signals=state['signals'],bounded_stream_complete=done.is_set() and not state['overflow'],
            original_process_returncode=process.returncode,passed=failure is None,error_class=failure,
            raw_error_returned=False,driver_acknowledgement_is_not_a_product_mutation=True,frame_checks=frame_observations),exclusive=True)
    return subprocess.CompletedProcess(command,process.returncode if failure is None else 1,b''.join(chunks),b'')


def read_original_frame_check(probe,nonce,phase,stage):
    require(phase in PHASES and stage in ('before','after'),'VIEW_FRAME_READ_SCOPE')
    name=phase+'-'+stage+'-native-frame-check.json'
    raw=probe.device('exec-out','run-as',DRIVER,'cat','files/'+name).stdout
    require(0<len(raw)<=8192,'VIEW_FRAME_READ_BOUND')
    native_raw=(probe.output/(phase+'-'+stage+'-native-view.json')).read_bytes();native=json.loads(native_raw)
    value=json.loads(raw);require(native['dispatch_id']==nonce and value['native_sha256']==hashlib.sha256(native_raw).hexdigest(),'VIEW_FRAME_NATIVE_HASH_MISMATCH')
    summary=frame_check(value,native)
    target=probe.output/name
    with target.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    return summary
