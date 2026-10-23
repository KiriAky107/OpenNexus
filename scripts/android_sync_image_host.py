"""One root compositor helper, with the same closed production GUI held alive.

No screenshot, product run-as, arbitrary crop/path input, permission grant or
capture fallback. Failed or unknown original commands are recorded, never retried.
"""
import base64
import json
import re
import shlex
import subprocess
import time

from android_release_auth_background import ReleaseAuthBackground
from android_release_observation import APP, exclusive
from android_release_sync_snapshot import ASSET
from android_runtime_probe import DRIVER, require, require_hosted_emulator
from android_sync_image_pixels import validate_packet
from android_sync_image_pixels import digest as bytes_digest
from release_plan import atomic_json, digest

PHASE = 'release-sync-image'
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
READY = 'no_backup/owned-sync-image-ready.json'
HELPER = DRIVER+'.OwnedImageCrop'


def installed_driver_path(raw):
    """API36 getNextCodePath: two canonical 16-byte URL-safe Base64 tokens.

    AOSP frameworks/base99b01a65 services/core/.../pm/PackageManagerServiceUtils
    prefixes the first directory with ~~ and the second with the exact package.
    This is not a generic /data/app or arbitrary APK/path admission.
    """
    require(type(raw) is bytes and 0<len(raw)<=4096,'SYNC_IMAGE_INSTALLED_DRIVER_OUTPUT_BOUND')
    lines=raw.decode('ascii').splitlines()
    pattern=r'package:(/data/app/~~([A-Za-z0-9_-]{22}==)/'+re.escape(DRIVER)+r'-([A-Za-z0-9_-]{22}==)/base\.apk)'
    match=re.fullmatch(pattern,lines[0]) if len(lines)==1 else None
    require(match is not None,'SYNC_IMAGE_INSTALLED_DRIVER_PATH')
    require(raw in (lines[0].encode(),lines[0].encode()+b'\n',lines[0].encode()+b'\r\n'),'SYNC_IMAGE_INSTALLED_DRIVER_LINE_END')
    for token in match.group(2,3):
        decoded=base64.b64decode(token,altchars=b'-_',validate=True)
        require(len(decoded)==16 and base64.urlsafe_b64encode(decoded).decode('ascii')==token,'SYNC_IMAGE_INSTALLED_DRIVER_TOKEN')
    return match[1]


def bind_installed_helper(subject,label):
    exclusive(subject.output/(label+'-helper-binding-intent.json'),json.dumps(dict(package=DRIVER,
        readonly=True,original_driver_APK_not_uploaded=True,automatic_retry=False)).encode())
    stage='pm-path'
    def read(name,*args):
        result=subject.shell(*args,check=False)
        require(len(result.stdout)<=4096 and len(result.stderr)<=4096,'SYNC_IMAGE_HELPER_METADATA_BOUND')
        exclusive(subject.output/(label+'-'+name+'.stdout'),result.stdout)
        exclusive(subject.output/(label+'-'+name+'.stderr'),result.stderr)
        atomic_json(subject.output/(label+'-'+name+'-result.json'),dict(returncode=result.returncode,readonly=True))
        require(result.returncode==0,'SYNC_IMAGE_HELPER_METADATA_READ_FAILED');return result.stdout
    try:
        apk=installed_driver_path(read('driver-pm-path','pm','path',DRIVER))
        stage='canonical-path'
        parts=apk.split('/');parents=['/'.join(parts[:n]) for n in (3,4,5,6)]
        script=' && '.join('test ! -L '+shlex.quote(path) for path in parents)
        script+=' && test -f '+shlex.quote(apk)+' && readlink -f '+shlex.quote(apk)
        canonical=read('driver-canonical-path','sh','-c',script)
        require(canonical==apk.encode()+b'\n','SYNC_IMAGE_INSTALLED_DRIVER_ALIAS')
        stage='original-byte-hash';built=subject.tools/'driver/driver.apk'
        require(built.is_file() and not built.is_symlink(),'SYNC_IMAGE_ORIGINAL_DRIVER_APK')
        expected=digest(built);observed=read('driver-sha256','sha256sum',apk).decode('ascii').strip().split()
        require(observed==[expected,apk],'SYNC_IMAGE_INSTALLED_DRIVER_BYTES')
        atomic_json(subject.output/(label+'-helper-binding.json'),dict(driver_APK_sha256=expected,installed_path=apk,exact_class=HELPER,
            canonical_path_and_no_symlinks_verified=True,original_driver_APK_not_uploaded=True,no_helper_rebuild=True))
        return apk
    except Exception as error:
        atomic_json(subject.output/(label+'-helper-binding-failure.json'),dict(stage=stage,error_type=type(error).__name__,
            image_instrumentation_or_capture_dispatched=False,automatic_retry=False))
        raise


def closed_json(raw):
    def pairs(items):
        value = dict(items); require(len(items) == len(value), 'SYNC_IMAGE_DUPLICATE_JSON'); return value
    require(type(raw) is bytes and 0 < len(raw) <= 4096, 'SYNC_IMAGE_READY_BOUND')
    value = json.loads(raw,object_pairs_hook=pairs)
    require(json.dumps(value,separators=(',',':'),ensure_ascii=False).encode() == raw, 'SYNC_IMAGE_COMPACT_JSON')
    return value


def validate_ready(value, elapsed_ms):
    require(type(value) is dict and set(value) == {'nonce','phase','package','uid','window','display','screen','canvas','image',
        'elapsed_ms','stable_ms','title_verified','exact_image_alt','visible_unobscured','no_keyboard'}, 'SYNC_IMAGE_READY_KEYS')
    require(value['phase'] == PHASE and value['package'] == APP and value['exact_image_alt'] == ASSET
            and type(value['nonce']) is str and re.fullmatch(UUID,value['nonce'])
            and all(value[k] is True for k in ('title_verified','visible_unobscured','no_keyboard')), 'SYNC_IMAGE_READY_SCOPE')
    require(all(type(value[k]) is int for k in ('uid','window','display','elapsed_ms','stable_ms')) and type(elapsed_ms) is int
            and 10000 <= value['uid'] <= 19999 and 0 <= value['window'] <= 65535 and value['display'] == 0
            and 0 <= value['elapsed_ms'] <= elapsed_ms <= value['elapsed_ms']+2000
            and 600 <= value['stable_ms'] <= 10000, 'SYNC_IMAGE_READY_IDENTITY_OR_TIME')
    for key in ('screen','canvas','image'):
        r=value[key]
        require(type(r) is list and len(r) == 4 and all(type(n) is int and 0 <= n <= 16384 for n in r)
                and r[0] < r[2] and r[1] < r[3], 'SYNC_IMAGE_READY_RECTANGLE')
    screen,canvas,image = (value[k] for k in ('screen','canvas','image'))
    require(screen[:2] == [0,0] and all(a[0] <= b[0] < b[2] <= a[2] and a[1] <= b[1] < b[3] <= a[3]
        for a,b in ((screen,canvas),(canvas,image))), 'SYNC_IMAGE_READY_CONTAINMENT')
    side=min(image[2]-image[0],image[3]-image[1]); require(64 <= side <= 2048,'SYNC_IMAGE_READY_SAMPLING_SIZE')
    left=image[0]+((image[2]-image[0])-side)//2; top=image[1]+((image[3]-image[1])-side)//2
    return [[left+(side//4 if i%2 == 0 else side*3//4)-4,top+(side//4 if i<2 else side*3//4)-4,
             left+(side//4 if i%2 == 0 else side*3//4)+4,top+(side//4 if i<2 else side*3//4)+4] for i in range(4)]


def elapsed(subject):
    clock=subject.shell('cat','/proc/uptime').stdout.decode('ascii').split()[0]
    require(re.fullmatch(r'[0-9]+\.[0-9]{1,3}',clock),'SYNC_IMAGE_UPTIME_SHAPE')
    whole,fraction=clock.split('.'); return int(whole)*1000+int(fraction.ljust(3,'0'))


def original_stdin(subject, label, command, raw, *, timeout, binary=False):
    # Exclusive intent is the once-only gate, including unknown writes/captures.
    exclusive(subject.output/(label+'-intent.json'),json.dumps(dict(arguments=list(command),stdin_bytes=len(raw),
        stdin_sha256=bytes_digest(raw),automatic_retry=False),indent=2).encode())
    try: result=subject.device(*command,input=raw,timeout=timeout,check=False)
    except subprocess.TimeoutExpired as error:
        atomic_json(subject.output/(label+'-result.json'),dict(returncode=None,unknown_result=True,automatic_retry=False,
            stdout_bytes=len(error.stdout or b''),stderr_bytes=len(error.stderr or b'')))
        raise
    atomic_json(subject.output/(label+'-result.json'),dict(returncode=result.returncode,unknown_result=False,automatic_retry=False,
        stdout_bytes=len(result.stdout),stdout_sha256=bytes_digest(result.stdout),stderr_bytes=len(result.stderr),stderr_sha256=bytes_digest(result.stderr)))
    # Unknown binary output is not an approved ROI. Only the separately decoded
    # four bounded PNGs may be retained. Error text is reduced to static tokens.
    if binary:
        marker=re.fullmatch(rb'OWNED_IMAGE_CROP_FAILED stage=([a-z-]+) no_fallback=1\r?\n',result.stderr)
        atomic_json(subject.output/(label+'-static-error.json'),dict(helper_stage=marker[1].decode() if marker else None,
            raw_stderr_omitted=True,unvalidated_binary_not_saved=True))
    else:
        require(len(result.stdout)<=4096 and len(result.stderr)<=4096,'SYNC_IMAGE_STATIC_SIGNAL_BOUND')
        exclusive(subject.output/(label+'.stdout'),result.stdout);exclusive(subject.output/(label+'.stderr'),result.stderr)
    require(result.returncode == 0,'SYNC_IMAGE_ORIGINAL_COMMAND_FAILED'); return result


def run_image(subject, label):
    require(re.fullmatch(r'[a-z][a-z0-9-]{0,47}',label),'SYNC_IMAGE_PHASE_LABEL');require_hosted_emulator(subject.args.serial)
    exclusive(subject.output/(label+'-phase-intent.json'),json.dumps(dict(phase=PHASE,capture_helper=HELPER,
        four_8x8_source_crops_only=True,automatic_retry=False)).encode())
    subject.stop()
    subject.original_command(label+'-launch',('shell','-T',shlex.join(('am','start','-W','-n',subject.activity))))
    observed_pid=subject.shell('pidof',APP,check=False)
    require(len(observed_pid.stdout)<=4096 and len(observed_pid.stderr)<=4096,'SYNC_IMAGE_PID_OUTPUT_BOUND')
    exclusive(subject.output/(label+'-image-process.stdout'),observed_pid.stdout)
    exclusive(subject.output/(label+'-image-process.stderr'),observed_pid.stderr)
    atomic_json(subject.output/(label+'-image-process-result.json'),dict(returncode=observed_pid.returncode,readonly=True))
    pid=observed_pid.stdout.strip()
    require(observed_pid.returncode==0 and re.fullmatch(rb'[1-9][0-9]{0,6}',pid),'SYNC_IMAGE_ORIGINAL_PID')
    # Observe the original read-only path/hash even if admission fails, before
    # any instrumentation or compositor action. No product run-as is involved.
    apk=bind_installed_helper(subject,label)
    args=('shell','-T',shlex.join(('am','instrument','-w','-e','phase',PHASE,DRIVER+'/.ReleaseSyncImageRuntimeProbe')))
    exclusive(subject.output/(label+'-instrument-intent.json'),json.dumps(dict(arguments=list(args),automatic_retry=False)).encode())
    process=subprocess.Popen([str(subject.adb),'-s',subject.args.serial,*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    complete=False;stdout=stderr=b''
    try:
        deadline=time.monotonic()+45
        while True:
            require(process.poll() is None,'SYNC_IMAGE_DRIVER_ENDED_BEFORE_READY')
            ready=subject.shell('run-as',DRIVER,'sh','-c','test -f '+READY+' && test ! -L '+READY+' && cat '+READY,check=False)
            require(len(ready.stdout)<=4096,'SYNC_IMAGE_READY_BOUND');value=None
            if ready.returncode == 0:
                try: value=closed_json(ready.stdout)
                except (ValueError,UnicodeDecodeError): pass  # Original exclusive write can be incomplete.
            if value is not None: break
            require(time.monotonic()<deadline,'SYNC_IMAGE_READY_TIMEOUT');time.sleep(.05)
        patches=validate_ready(value,elapsed(subject))
        exclusive(subject.output/(label+'-ready.json'),ready.stdout)
        def owner():
            require(process.poll() is None and subject.shell('pidof',APP).stdout.strip()==pid
                and ReleaseAuthBackground.app_is_resumed(subject) and subject.observer.observe_identity()==subject.identity,
                'SYNC_IMAGE_FOREGROUND_OWNER_CHANGED')
            validate_ready(value,elapsed(subject))
        owner()
        request=dict(nonce=value['nonce'],boot_id=subject.identity['boot_id'],ready_sha256=bytes_digest(ready.stdout),pid=int(pid))
        require(re.fullmatch(UUID,request['boot_id']),'SYNC_IMAGE_BOOT_ID')
        atomic_json(subject.output/(label+'-capture-binding.json'),dict(**request,source_crops=patches,
            same_APP_PID_foreground=True,same_instrumentation_alive=True,original_ready_age_bound_ms=2000))
        command=('shell','-T',shlex.join(('env','CLASSPATH='+apk,'app_process','/system/bin',HELPER)))
        result=original_stdin(subject,label+'-compositor',command,json.dumps(request,separators=(',',':')).encode(),timeout=20,binary=True)
        owner()  # No early driver exit/force-stop can make the captured result pass.
        frames,pixels=validate_packet(result.stdout,value['image'])
        require([v['source_crop'] for v in pixels['observations']]==patches,'SYNC_IMAGE_SOURCE_CROP_MODEL_CHANGED')
        for i,frame in enumerate(frames):exclusive(subject.output/(label+'-ROI-'+str(i)+'.png'),frame)
        atomic_json(subject.output/(label+'-pixel-observation.json'),pixels)
        finish='no_backup/owned-sync-image-'+value['nonce']+'.finish'
        command=('shell','-T','run-as '+DRIVER+' sh -c '+shlex.quote('umask 077; test ! -e '+finish+' && test ! -L '+finish+' && cat > '+finish))
        original_stdin(subject,label+'-finish',command,b'finish\n',timeout=10)
        readback=subject.shell('run-as',DRIVER,'cat',finish,check=False)
        require(readback.returncode == 0 and readback.stdout == b'finish\n','SYNC_IMAGE_FINISH_READBACK')
        atomic_json(subject.output/(label+'-finish-readback.json'),dict(nonce=value['nonce'],exact_static_bytes=True,bytes=7))
        stdout,stderr=process.communicate(timeout=20);complete=True
        require(process.returncode==0 and b'OPENNEXUS_RELEASE_SYNC_IMAGE_PHASE_OK release-sync-image gui_guard_only=1 decoded_image_verified=0' in stdout
            and b'_FAILED' not in stdout,'SYNC_IMAGE_HELD_GUI_GUARD_FAILED')
        proof=dict(phase=PHASE,label=label,pid=int(pid),passed=True,nonce=value['nonce'],actual_four_source_ROI_pixels_verified=True,
            GUI_guard_ack_alone_is_not_pixel_evidence=True,whole_display_capture=False,source_crops=patches,pixels=pixels)
        subject.phases.append(proof);return proof
    finally:
        if not complete:
            if process.poll() is None:process.terminate()
            try:stdout,stderr=process.communicate(timeout=10)
            except subprocess.TimeoutExpired:process.kill();stdout,stderr=process.communicate(timeout=10)
        # The closed GUI entry emits only static stages/status, never AX/UI/errors.
        if len(stdout)<=1024**2 and len(stderr)<=1024**2:
            exclusive(subject.output/(label+'-instrument.stdout'),stdout);exclusive(subject.output/(label+'-instrument.stderr'),stderr)
        atomic_json(subject.output/(label+'-instrument-result.json'),dict(returncode=process.returncode,unknown_result=not complete,automatic_retry=False))
        subject.stop()
