"""Exclusive hosted PostgreSQL/S3 + fixed Sync service + temporary HTTPS origin.

No developer-machine fallback, production pointer, secret CLI argument or automatic
restart. Secrets/raw provider logs stay in the private owner directory, outside
runtime artifacts. The actual Android UI run is a separate consumer.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
import uuid
from android_auth_fixture import SERVICE_SHA, atomic_json, initialize_control, owned_root, read_json, require, require_hosted

CLOUDFLARED_URL='https://github.com/cloudflare/cloudflared/releases/download/2026.10.1/cloudflared-linux-amd64'
CLOUDFLARED_SHA256='3bfc539f45d2620c71debb7a86be0efa737ed5ccbad9eb85c787116900e5c586'
CLOUDFLARED_BYTES=40227032
DOCS='https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/'


def minimal_environment(root):
    names=('PATH','LANG','LC_ALL','GITHUB_ACTIONS','RUNNER_ENVIRONMENT','RUNNER_OS','GITHUB_EVENT_NAME',
        'GITHUB_REPOSITORY','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_WORKSPACE','RUNNER_TRACKING_ID')
    result={name:os.environ[name] for name in names if name in os.environ}
    result.update(HOME=str(root/'home'),PYTHONUNBUFFERED='1',AWS_EC2_METADATA_DISABLED='true')
    return result


def process_identity(pid):
    try:
        stat=Path('/proc',str(pid),'stat').read_text()
        command=Path('/proc',str(pid),'cmdline').read_bytes()
        # Fields after the parenthesized comm start at field3. starttime=22.
        ticks=int(stat[stat.rfind(')')+2:].split()[19])
        return {'pid':pid,'start_ticks':ticks,'command_sha256':hashlib.sha256(command).hexdigest(),
            'argv':[part.decode('utf-8') for part in command.rstrip(b'\0').split(b'\0')]}
    except (OSError,ValueError,UnicodeError,IndexError):return None


def same_process(saved,current):
    return bool(current and all(saved.get(key)==current.get(key) for key in ('pid','start_ticks','command_sha256','argv')))


def download_cloudflared(root):
    require_hosted()
    destination=root/'cloudflared'
    require(not destination.exists() and not destination.is_symlink(),'OWNED_AUTH_ASSET_EXISTS')
    temporary=root/'cloudflared.download'
    digest=hashlib.sha256();size=0
    fd=os.open(temporary,os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,'O_NOFOLLOW',0),0o600)
    request=urllib.request.Request(CLOUDFLARED_URL,headers={'User-Agent':'OpenNexus-owned-Android-auth-test'})
    with os.fdopen(fd,'wb') as stream, urllib.request.urlopen(request,timeout=120) as response:
        require(response.geturl().startswith('https://'),'OWNED_AUTH_ASSET_ORIGIN_INVALID')
        while data:=response.read(1024*1024):
            size+=len(data);require(size<=CLOUDFLARED_BYTES,'OWNED_AUTH_ASSET_SIZE_INVALID')
            digest.update(data);stream.write(data)
        stream.flush();os.fsync(stream.fileno())
    require(size==CLOUDFLARED_BYTES and digest.hexdigest()==CLOUDFLARED_SHA256,'OWNED_AUTH_ASSET_HASH_INVALID')
    os.replace(temporary,destination);destination.chmod(0o700)
    return {'release':'2026.10.1','url':CLOUDFLARED_URL,'bytes':size,'sha256':digest.hexdigest(),'docs':DOCS}


class HostedOrigin:
    def __init__(self,root):
        self.root=root
        self.path=root/'owner-state.json'
        self.state=read_json(self.path) if self.path.exists() else None
        self.processes=[]

    def save(self):atomic_json(self.path,self.state)

    def command(self,argv,stage,*,env=None,cwd=None,timeout=120,capture=False):
        log=self.root/(stage+'.private.log')
        fd=os.open(log,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as output:
            result=subprocess.run([str(arg) for arg in argv],cwd=cwd,env=env or minimal_environment(self.root),
                stdout=subprocess.PIPE if capture else output,stderr=output if capture else subprocess.STDOUT,timeout=timeout)
        require(result.returncode==0,'OWNED_AUTH_STAGE_FAILED')
        require(not capture or len(result.stdout)<=1024*1024,'OWNED_AUTH_STAGE_OUTPUT_INVALID')
        return result.stdout if capture else b''

    def spawn(self,name,argv,env):
        argv=[str(arg) for arg in argv]
        self.state['process_intents'][name]={'argv':argv,'result':'prepared'};self.save()
        fd=os.open(self.root/(name+'.private.log'),os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as output:
            child=subprocess.Popen(argv,env=env,cwd=self.root,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        self.processes.append(child)
        identity=process_identity(child.pid)
        require(identity is not None and identity['argv']==argv,'OWNED_AUTH_PROCESS_START_UNCONFIRMED')
        self.state['process_intents'][name].update(result='started',identity=identity);self.save()
        return child

    def start_container(self,name,image,arguments,environment):
        intent={'name':name,'image':image,'result':'prepared'}
        self.state['containers'].append(intent);self.save()
        argv=['docker','run','--detach','--name',name,'--label','opennexus.auth.owner='+self.state['owner'],*arguments,image]
        if 'objects' in name:argv+=['server','/data']
        data=self.command(argv,'start-'+name,env=environment,capture=True)
        identity=data.decode().strip();require(re.fullmatch(r'[0-9a-f]{64}',identity),'OWNED_AUTH_CONTAINER_START_UNCONFIRMED')
        intent.update(result='started',id=identity);self.save()
        # Keep the resolved immutable image, not only the mutable registry tag.
        # Inspect this one field; Config.Env contains private provider secrets.
        resolved=self.command(['docker','inspect','--format','{{.Image}}',identity],
            'image-'+name,capture=True).decode().strip()
        require(re.fullmatch(r'sha256:[0-9a-f]{64}',resolved),'OWNED_AUTH_CONTAINER_IMAGE_UNCONFIRMED')
        intent['image_id']=resolved;self.save()

    def prepare(self):
        require_hosted();require(not self.root.exists(),'OWNED_AUTH_ALREADY_PREPARED')
        self.root.mkdir(mode=0o700);(self.root/'home').mkdir(mode=0o700);(self.root/'staging').mkdir(mode=0o700)
        source=Path(os.environ['GITHUB_WORKSPACE']).resolve()/'.build/android-auth-source'
        require(subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==SERVICE_SHA,'OWNED_AUTH_SOURCE_CHANGED')
        require(not subprocess.check_output(['git','status','--porcelain'],cwd=source),'OWNED_AUTH_SOURCE_CHANGED')
        suffix=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
        self.state={'schema':1,'source':SERVICE_SHA,'owner':suffix+'-'+secrets.token_hex(12),
            'result':'preparing','process_intents':{},'containers':[]};self.save()
        initialize_control(self.root)
        password=secrets.token_urlsafe(40);access='android-own-'+secrets.token_hex(4);secret=secrets.token_urlsafe(40)
        bucket='onx-android-auth-'+suffix
        accounts=[{'username':'android-owned-'+kind+'-'+secrets.token_hex(6),'password':secrets.token_urlsafe(32),
            'device_name':'Owned Android '+kind+' '+suffix} for kind in ('a','b')]
        environment={'SYNC_DATABASE_URL':'postgresql+psycopg://android_owned:'+password+'@127.0.0.1:15472/android_auth',
            'SYNC_S3_ENDPOINT':'http://127.0.0.1:19072','SYNC_S3_BUCKET':bucket,'SYNC_STAGING_DIR':str(self.root/'staging'),
            'SYNC_OPERATIONS_PATH':str(self.root/'operations.sqlite3'),'AWS_ACCESS_KEY_ID':access,
            'AWS_SECRET_ACCESS_KEY':secret,'AWS_DEFAULT_REGION':'us-east-1','AWS_EC2_METADATA_DISABLED':'true'}
        atomic_json(self.root/'service-private.json',{'source':SERVICE_SHA,'bucket':bucket,'accounts':accounts,'environment':environment},exclusive=True)
        docker_env=minimal_environment(self.root)
        docker_env.update(POSTGRES_USER='android_owned',POSTGRES_DB='android_auth',POSTGRES_PASSWORD=password,
            MINIO_ROOT_USER=access,MINIO_ROOT_PASSWORD=secret)
        image='opennexus-auth-objects:'+suffix
        self.command(['docker','build','--file',source/'Dockerfile.objects','--tag',image,source], 'build-objects',timeout=1200)
        self.start_container('onx-auth-pg-'+suffix,'postgres:17.6',
            ['--publish','127.0.0.1:15472:5432','--env','POSTGRES_USER','--env','POSTGRES_DB','--env','POSTGRES_PASSWORD'],docker_env)
        self.start_container('onx-auth-objects-'+suffix,image,
            ['--publish','127.0.0.1:19072:9000','--env','MINIO_ROOT_USER','--env','MINIO_ROOT_PASSWORD'],docker_env)
        runtime_env=minimal_environment(self.root);runtime_env.update(environment)
        python=source/'.venv/bin/python'
        self.command([python,'-m','sync_server','initialize','--workers','1'],'initialize-service',env=runtime_env,cwd=source,timeout=150)
        fixture=Path(__file__).with_name('android_auth_fixture.py').resolve()
        self.command([python,fixture,'seed'],'seed-owned-users',env=runtime_env,cwd=source)
        self.spawn('service',[python,fixture,'serve','--owner-run',suffix],runtime_env)
        deadline=time.monotonic()+45
        while True:
            try:
                with urllib.request.urlopen('http://127.0.0.1:19073/sync/v1/capabilities',timeout=3) as response:
                    require(response.status==200,'OWNED_AUTH_ORIGIN_NOT_READY');break
            except Exception:
                require(time.monotonic()<deadline,'OWNED_AUTH_ORIGIN_NOT_READY');time.sleep(.25)
        asset=download_cloudflared(self.root)
        argv=[self.root/'cloudflared','tunnel','--no-autoupdate','--protocol','http2','--loglevel','info',
            '--logfile',self.root/'tunnel-origin.private.log','--url','http://127.0.0.1:19073']
        tunnel=self.spawn('tunnel',argv,minimal_environment(self.root))
        endpoint=None;deadline=time.monotonic()+90
        while time.monotonic()<deadline and tunnel.poll() is None:
            log=self.root/'tunnel-origin.private.log'
            text=log.read_text('utf-8',errors='replace') if log.exists() else ''
            values=re.findall(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com',text)
            if values:endpoint=values[0];break
            time.sleep(.25)
        require(endpoint is not None,'OWNED_AUTH_TUNNEL_NOT_READY')
        # This is only origin readiness from the runner, not Android TLS proof.
        deadline=time.monotonic()+90
        while True:
            try:
                with urllib.request.urlopen(endpoint+'/sync/v1/capabilities',timeout=5) as response:
                    require(response.status==200 and response.geturl()==endpoint+'/sync/v1/capabilities','OWNED_AUTH_TUNNEL_NOT_READY')
                    response.read(65537);break
            except Exception:
                require(time.monotonic()<deadline,'OWNED_AUTH_TUNNEL_NOT_READY');time.sleep(.5)
        atomic_json(self.root/'device-private.json',{'endpoint':endpoint,'accounts':accounts},exclusive=True)
        self.state.update(result='ready',endpoint=endpoint,cloudflared=asset,bucket=bucket);self.save()
        return {'source':SERVICE_SHA,'endpoint':endpoint,'providers':'Owned PostgreSQL17.6/S3 and actual production initialize/create-user CLI',
            'images':[{'requested':item['image'],'resolved_id':item['image_id']} for item in self.state['containers']],
            'cloudflared':asset,'Android_TLS_or_auth_verified':False,'secrets_in_artifacts':False}

    def stop(self):
        require_hosted()
        if not self.path.exists():return {'stopped':[],'missing_owner_state':True}
        self.state=read_json(self.path);stopped=[]
        # Recover a process only by the exact original argv (including the
        # unique owner-run or private tunnel logfile), never by a program name.
        identities=[]
        for name,intent in reversed(list(self.state['process_intents'].items())):
            identity=intent.get('identity')
            if identity is None:
                matches=[]
                for entry in Path('/proc').iterdir():
                    if entry.name.isdigit():
                        current=process_identity(int(entry.name))
                        if current and current['argv']==intent['argv']:matches.append(current)
                require(len(matches)<=1,'OWNED_AUTH_PROCESS_OWNERSHIP_UNCERTAIN')
                identity=matches[0] if matches else None
            if identity and same_process(identity,process_identity(identity['pid'])):
                require(os.getpgid(identity['pid'])==identity['pid'],'OWNED_AUTH_PROCESS_OWNERSHIP_UNCERTAIN')
                os.killpg(identity['pid'],signal.SIGTERM);stopped.append(name)
                identities.append(identity)
        deadline=time.monotonic()+10
        while any(same_process(saved,process_identity(saved['pid'])) for saved in identities) and time.monotonic()<deadline:
            for child in self.processes:child.poll()
            time.sleep(.1)
        for saved in identities:
            if same_process(saved,process_identity(saved['pid'])):
                require(os.getpgid(saved['pid'])==saved['pid'],'OWNED_AUTH_PROCESS_OWNERSHIP_UNCERTAIN')
                os.killpg(saved['pid'],signal.SIGKILL)
        deadline=time.monotonic()+5
        while any(same_process(saved,process_identity(saved['pid'])) for saved in identities) and time.monotonic()<deadline:
            for child in self.processes:child.poll()
            time.sleep(.1)
        for child in self.processes:
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:raise RuntimeError('OWNED_AUTH_PROCESS_STOP_UNCONFIRMED')
        require(not any(same_process(saved,process_identity(saved['pid'])) for saved in identities),'OWNED_AUTH_PROCESS_STOP_UNCONFIRMED')
        for intent in reversed(self.state['containers']):
            query=subprocess.run(['docker','inspect','--format','{{json .}}',intent['name']],capture_output=True,text=True,timeout=30)
            if query.returncode:continue
            current=json.loads(query.stdout)
            require(current['Config']['Labels'].get('opennexus.auth.owner')==self.state['owner'],'OWNED_AUTH_CONTAINER_OWNERSHIP_UNCERTAIN')
            if intent.get('id'):require(current['Id']==intent['id'],'OWNED_AUTH_CONTAINER_OWNERSHIP_UNCERTAIN')
            subprocess.run(['docker','rm','--force','--volumes',current['Id']],capture_output=True,check=True,timeout=60)
            stopped.append(intent['name'])
        self.state.update(result='stopped',cleanup={'stop_requests':stopped,'original_processes_exited':True,'only_original_owned_resources':True});self.save()
        return self.state['cleanup']


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('prepare','stop'));args=parser.parse_args()
    root=owned_root();origin=HostedOrigin(root)
    if args.action=='stop':result=origin.stop()
    else:
        require(not root.exists(),'OWNED_AUTH_ALREADY_PREPARED')
        try:result=origin.prepare()
        except Exception:
            origin.stop();raise
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except Exception as error:
        from android_auth_fixture import SAFE_CODES
        code=str(error)
        # No raw DSN, provider stdout, body or unexpected exception value.
        print(code if code in SAFE_CODES else 'OWNED_AUTH_HOST_FAILED_'+type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
