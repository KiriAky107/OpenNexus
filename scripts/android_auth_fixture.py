"""Owned hosted Sync0.8 authentication fault proxy; never a production entry.

Only synthetic auth response delivery changes. The fixed service commits once;
there is no request replay, token/credential export or HTTP fault-control route.
"""
from contextlib import contextmanager
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

SERVICE_SHA = '29e2e6d373ea752352981bdad05d34a7a0d9c3d9'
MODES = {'normal', 'drop_login', 'drop_refresh', 'drop_logout', 'hold_login', 'release'}
SAFE_CODES = {'OWNED_AUTH_HOST_REQUIRED','OWNED_AUTH_RUN_INVALID','OWNED_AUTH_ROOT_INVALID','OWNED_AUTH_FILE_INVALID',
    'OWNED_AUTH_CONTROL_INVALID','OWNED_AUTH_CONTROL_MISMATCH','OWNED_AUTH_CONTROL_CHANGED','OWNED_AUTH_CONTROL_UNCERTAIN',
    'OWNED_AUTH_CONTROL_RETRY_REQUIRES_EXPLICIT_INTENT','OWNED_AUTH_RESPONSE_TOO_LARGE','OWNED_AUTH_HOLD_NOT_RELEASED',
    'OWNED_AUTH_SOURCE_MISSING','OWNED_AUTH_SOURCE_CHANGED','OWNED_AUTH_PROVIDER_POINTER_INVALID','OWNED_AUTH_ENV_INVALID',
    'OWNED_AUTH_NOT_PREPARED','OWNED_AUTH_PROXY_INVALID'}
ROUTES = {('POST', '/sync/v1/auth/sessions'): 'login', ('POST', '/sync/v1/auth/refresh'): 'refresh',
          ('DELETE', '/sync/v1/auth/sessions'): 'logout'}


def require(value, code):
    if not value:
        raise RuntimeError(code)


def require_hosted():
    require(sys.platform == 'linux' and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
            and os.environ.get('RUNNER_OS') == 'Linux'
            and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and os.environ.get('GITHUB_REPOSITORY') == 'KiriAky107/OpenNexus', 'OWNED_AUTH_HOST_REQUIRED')


def owned_root():
    require_hosted()
    run, attempt = os.environ.get('GITHUB_RUN_ID', ''), os.environ.get('GITHUB_RUN_ATTEMPT', '')
    require(re.fullmatch(r'[1-9][0-9]{0,17}', run) and re.fullmatch(r'[1-9][0-9]{0,5}', attempt), 'OWNED_AUTH_RUN_INVALID')
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    root = workspace / '.build/android-runtime' / ('owned-auth-' + run + '-' + attempt)
    require(root.resolve().is_relative_to(workspace), 'OWNED_AUTH_ROOT_INVALID')
    for path in (workspace / '.build', workspace / '.build/android-runtime', root):
        require(not path.is_symlink(), 'OWNED_AUTH_ROOT_INVALID')
    return root


def read_json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024 * 1024, 'OWNED_AUTH_FILE_INVALID')
    return json.loads(path.read_text('utf-8'))


def atomic_json(path, value, *, exclusive=False):
    require(not path.is_symlink(), 'OWNED_AUTH_FILE_INVALID')
    data=(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n').encode('utf-8')
    target=path if exclusive else path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd=os.open(target, flags, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    if not exclusive:os.replace(target, path)


@contextmanager
def control_lock(root):
    path=root/'control.lock';require(not path.is_symlink(), 'OWNED_AUTH_FILE_INVALID')
    fd=os.open(path, os.O_RDWR | os.O_CREAT | getattr(os,'O_NOFOLLOW',0), 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:stream.write(b'0');stream.flush()
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            try:yield
            finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:yield
            finally:fcntl.flock(stream,fcntl.LOCK_UN)


def initialize_control(root):
    (root/'control-intents').mkdir(mode=0o700)
    (root/'control-receipts').mkdir(mode=0o700)
    (root/'fault-events').mkdir(mode=0o700)
    atomic_json(root/'control.json', {'revision':0,'operation_id':None,'mode':'normal'}, exclusive=True)


def arm_control(root, operation, mode, expected_revision, *, retry=False, allowed_modes=None):
    allowed=MODES if allowed_modes is None else allowed_modes
    require(str(uuid.UUID(operation)) == operation and mode in allowed, 'OWNED_AUTH_CONTROL_INVALID')
    require(type(expected_revision) is int and 0 <= expected_revision < 10000, 'OWNED_AUTH_CONTROL_INVALID')
    body={'operation_id':operation,'mode':mode,'expected_revision':expected_revision,'result_revision':expected_revision+1}
    intent=root/'control-intents'/ (operation+'.json');receipt=root/'control-receipts'/ (operation+'.json')
    with control_lock(root):
        if receipt.exists():
            previous=read_json(receipt);require(previous == body,'OWNED_AUTH_CONTROL_MISMATCH');return previous
        exists=intent.exists()
        if exists:require(read_json(intent) == body,'OWNED_AUTH_CONTROL_MISMATCH')
        else:atomic_json(intent,body,exclusive=True)
        current=read_json(root/'control.json')
        if current['operation_id'] == operation:
            require(current == {'revision':expected_revision+1,'operation_id':operation,'mode':mode}, 'OWNED_AUTH_CONTROL_MISMATCH')
            atomic_json(receipt,body,exclusive=True);return body
        require(current['revision'] == expected_revision, 'OWNED_AUTH_CONTROL_CHANGED')
        prior=current['operation_id']
        require(prior is None or (root/'control-receipts'/(prior+'.json')).is_file(), 'OWNED_AUTH_CONTROL_UNCERTAIN')
        require(not exists or retry, 'OWNED_AUTH_CONTROL_RETRY_REQUIRES_EXPLICIT_INTENT')
        atomic_json(root/'control.json', {'revision':expected_revision+1,'operation_id':operation,'mode':mode})
        atomic_json(receipt,body,exclusive=True)
        return body


class DeliveryProxy:
    def __init__(self, app, root, *, routes=None, response_limit=64*1024):
        selected=ROUTES if routes is None else routes
        require(isinstance(selected,dict) and 1<=len(selected)<=8
            and all(isinstance(route,tuple) and len(route)==2 and route[0] in ('GET','POST','PUT','DELETE')
                and isinstance(route[1],str) and route[1].startswith('/sync/v1/') and len(route[1])<=256
                and isinstance(action,str) and re.fullmatch(r'[a-z_]{1,24}',action) for route,action in selected.items())
            and type(response_limit) is int and 64*1024<=response_limit<=2*1024*1024,'OWNED_AUTH_PROXY_INVALID')
        self.app,self.root=app,root
        self.routes=dict(selected)
        self.response_limit=response_limit
        self.lock=asyncio.Lock()

    async def record(self, action, status, delivered_status, fault=None):
        async with self.lock:
            path=self.root/'request-counts.json'
            data=read_json(path) if path.exists() else {'counts':{},'events':[]}
            key=action+':'+str(status)
            data['counts'][key]=data['counts'].get(key,0)+1
            data['events']=(data['events']+[{'action':action,'service_status':status,'delivery_status':delivered_status,
                'fault_operation':fault}])[-64:]
            atomic_json(path,data)

    async def __call__(self, scope, receive, send):
        action=self.routes.get((scope.get('method'),scope.get('path')))
        if scope.get('type') != 'http' or action is None:
            await self.app(scope,receive,send);return
        control=read_json(self.root/'control.json')
        fault=control['mode'] in ('drop_'+action,'hold_'+action)
        marker=self.root/'fault-events'/(str(control['operation_id'])+'.json')
        if marker.exists():fault=False
        status=0;messages=[];size=0
        async def forward(message):
            nonlocal status
            if message['type']=='http.response.start':status=message['status']
            await send(message)
        async def capture(message):
            nonlocal status,size
            if message['type']=='http.response.start':status=message['status']
            size+=len(message.get('body',b''))
            require(size <= self.response_limit,'OWNED_AUTH_RESPONSE_TOO_LARGE')
            messages.append(message)
        await self.app(scope,receive,capture if fault else forward)
        if not fault:
            await self.record(action,status,status);return
        claimed=False
        if 200 <= status < 300:
            try:
                atomic_json(marker,{'operation_id':control['operation_id'],'mode':control['mode'],'revision':control['revision'],
                    'action':action,'service_status':status,'service_response_complete':True,'raw_body_recorded':False},exclusive=True)
                claimed=True
            except FileExistsError:pass
        if not claimed:
            for message in messages:await send(message)
            await self.record(action,status,status);return
        await self.record(action,status,503 if control['mode'].startswith('drop_') else None,control['operation_id'])
        if control['mode'].startswith('drop_'):
            messages.clear()
            data=b'{"error":{"code":"OWNED_AUTH_RESPONSE_DROPPED"}}'
            await send({'type':'http.response.start','status':503,'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(data)).encode())]})
            await send({'type':'http.response.body','body':data});return
        deadline=time.monotonic()+45
        while time.monotonic() < deadline:
            changed=read_json(self.root/'control.json')
            if changed['operation_id'] != control['operation_id'] and changed['mode'] in ('normal','release'):break
            await asyncio.sleep(.1)
        else:
            messages.clear();raise RuntimeError('OWNED_AUTH_HOLD_NOT_RELEASED')
        # Only the original already-executed response is released. This is not
        # another service request and never claims the Android client applied it.
        completed=False
        try:
            for message in messages:await send(message)
            completed=True
        finally:
            messages.clear()
            atomic_json(self.root/'fault-events'/(control['operation_id']+'.released.json'),
                {'operation_id':control['operation_id'],'original_response_send_attempted':True,
                 'ASGI_send_returned':completed,'client_receipt_known':False},exclusive=True)


def fixed_service(root):
    require_hosted()
    source=Path(os.environ['GITHUB_WORKSPACE']).resolve()/'.build/android-auth-source'
    require(not source.is_symlink() and source.is_dir(), 'OWNED_AUTH_SOURCE_MISSING')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
    require(head==SERVICE_SHA and not subprocess.check_output(['git','status','--porcelain'],cwd=source), 'OWNED_AUTH_SOURCE_CHANGED')
    sys.path.insert(0,str(source))
    config=read_json(root/'service-private.json')
    environment=config['environment']
    allowed={'SYNC_DATABASE_URL','SYNC_S3_ENDPOINT','SYNC_S3_BUCKET','SYNC_STAGING_DIR','SYNC_OPERATIONS_PATH',
        'AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_DEFAULT_REGION','AWS_EC2_METADATA_DISABLED'}
    require(set(environment)==allowed and all(type(value) is str and len(value)<=2048 for value in environment.values()), 'OWNED_AUTH_ENV_INVALID')
    from urllib.parse import urlsplit
    database=urlsplit(environment['SYNC_DATABASE_URL'])
    require(database.scheme=='postgresql+psycopg' and database.hostname=='127.0.0.1' and database.port==15472
        and database.path=='/android_auth' and database.username=='android_owned' and database.password
        and len(database.password)>=40,'OWNED_AUTH_PROVIDER_POINTER_INVALID')
    require(environment['SYNC_S3_ENDPOINT']=='http://127.0.0.1:19072'
        and environment['SYNC_STAGING_DIR']==str(root/'staging') and environment['SYNC_OPERATIONS_PATH']==str(root/'operations.sqlite3'),
        'OWNED_AUTH_PROVIDER_POINTER_INVALID')
    require(environment['SYNC_S3_BUCKET']==config['bucket'] and re.fullmatch(r'onx-android-auth-[0-9]+-[0-9]+',config['bucket']),
        'OWNED_AUTH_PROVIDER_POINTER_INVALID')
    require(environment['AWS_EC2_METADATA_DISABLED']=='true' and environment['AWS_DEFAULT_REGION']=='us-east-1'
        and len(environment['AWS_ACCESS_KEY_ID'])>=12 and len(environment['AWS_SECRET_ACCESS_KEY'])>=40,'OWNED_AUTH_ENV_INVALID')
    for name in ('AWS_SESSION_TOKEN','AWS_PROFILE','AWS_WEB_IDENTITY_TOKEN_FILE','AWS_ROLE_ARN',
        'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI','AWS_CONTAINER_CREDENTIALS_FULL_URI'):
        os.environ.pop(name,None)
    for name,value in environment.items():
        os.environ[name]=value
    return config


def observe_provider(root):
    config=fixed_service(root)
    from sync_server.database import Database
    from sqlalchemy import text
    db=Database(config['environment']['SYNC_DATABASE_URL'])
    try:
        with db.read_only() as connection:
            version=connection.execute(text('SELECT version()')).scalar_one()
            users=[]
            for account in config['accounts']:
                user=connection.execute(text('SELECT id,username FROM users WHERE username=:name'),{'name':account['username']}).mappings().one()
                devices=connection.execute(text('SELECT id,name,revoked FROM devices WHERE user_id=:id ORDER BY id'),{'id':user['id']}).mappings().all()
                counts=[]
                for device in devices:
                    count=connection.execute(text('SELECT COUNT(*) FROM sessions WHERE device_id=:id'),{'id':device['id']}).scalar_one()
                    counts.append({'device_id':device['id'],'sessions':count})
                users.append({'user_id':user['id'],'username':user['username'],'devices':[dict(d) for d in devices],'session_counts':counts})
        return {'source':SERVICE_SHA,'provider':'actual PostgreSQL; read-only repeatable-read observation','version':version,
            'users':users,'raw_credentials_or_session_hashes_selected':False,
            'requests':read_json(root/'request-counts.json') if (root/'request-counts.json').exists() else {'counts':{},'events':[]}}
    finally:db.engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('serve','observe','control','seed'))
    parser.add_argument('--operation');parser.add_argument('--mode',choices=sorted(MODES));parser.add_argument('--expected-revision',type=int)
    parser.add_argument('--retry-original',action='store_true');parser.add_argument('--owner-run');args=parser.parse_args()
    root=owned_root();require(root.is_dir(),'OWNED_AUTH_NOT_PREPARED')
    if args.action=='control':
        require(args.operation is not None and args.mode is not None and args.expected_revision is not None,'OWNED_AUTH_CONTROL_INVALID')
        print(json.dumps(arm_control(root,args.operation,args.mode,args.expected_revision,retry=args.retry_original)))
    elif args.action=='observe':print(json.dumps(observe_provider(root)))
    elif args.action=='seed':
        config=fixed_service(root)
        require(len(config['accounts'])==2,'OWNED_AUTH_ENV_INVALID')
        atomic_json(root/'seed-intent.json',{'source':SERVICE_SHA,'accounts':[a['username'] for a in config['accounts']]},exclusive=True)
        # Exercise the fixed production create-user CLI while keeping its
        # synthetic password in a protected file, never a process argument.
        import getpass,runpy
        original_argv=sys.argv;original_getpass=getpass.getpass
        try:
            for account in config['accounts']:
                require(re.fullmatch(r'android-owned-[ab]-[0-9a-f]{12}',account['username'])
                    and 40<=len(account['password'])<=100,'OWNED_AUTH_ENV_INVALID')
                getpass.getpass=lambda prompt='',value=account['password']:value
                sys.argv=['sync_server','create-user','--username',account['username']]
                runpy.run_module('sync_server',run_name='__main__')
        finally:sys.argv=original_argv;getpass.getpass=original_getpass
        atomic_json(root/'seed-receipt.json',observe_provider(root),exclusive=True)
    else:
        require(args.owner_run==os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'],'OWNED_AUTH_RUN_INVALID')
        fixed_service(root)
        from sync_server.__main__ import application
        import uvicorn
        uvicorn.run(DeliveryProxy(application(),root),host='127.0.0.1',port=19073,workers=1,access_log=False,log_level='warning')


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Provider exceptions can contain DSNs or query parameters. Only an
        # allowlisted local code/class may reach the hosted job's public log.
        code=str(error)
        print(code if code in SAFE_CODES else 'OWNED_AUTH_FAILED_'+type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
