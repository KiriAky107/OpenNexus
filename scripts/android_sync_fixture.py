"""Bounded delivery controls for a NEW owned Sync run, not product evidence.

Compose the fixed real service with this proxy only in an exclusive hosted owner.
It forwards each original request once and changes only response delivery after
the service completes. It never manufactures a revision, receipt or client ACK.
Actual PG/S3 provisioning, UI phases and provider observation are separate steps.
"""
from pathlib import Path
import argparse
import json
import os
import re
import sys
import android_auth_fixture as original
from android_auth_fixture import (
    DeliveryProxy as OriginalDeliveryProxy,
    arm_control as original_arm_control,
    initialize_control as original_initialize_control,
    require,
)

MODES=frozenset(('normal','drop_revision','hold_revision','hold_changes','release'))


def routes(remote_vault):
    require(isinstance(remote_vault,str) and re.fullmatch(r'[A-Za-z0-9-]{1,80}',remote_vault),
        'OWNED_SYNC_TARGET_INVALID')
    prefix='/sync/v1/vaults/'+remote_vault
    return {('POST',prefix+'/revisions'):'revision',('GET',prefix+'/changes'):'changes'}


def initialize_control(owner):
    # A new exclusive namespace; never reuse or alter passed auth controls.
    root=owner/'sync-control'
    require(not root.exists() and not root.is_symlink(),'OWNED_SYNC_ALREADY_PREPARED')
    root.mkdir(mode=0o700)
    original_initialize_control(root)
    return root


def control_root(owner):
    root=owner/'sync-control'
    require(root.is_dir() and not root.is_symlink(),'OWNED_SYNC_NOT_PREPARED')
    return root


def arm_control(owner, operation, mode, expected_revision, *, retry=False):
    return original_arm_control(control_root(owner),operation,mode,expected_revision,
        retry=retry,allowed_modes=MODES)


class DeliveryProxy(OriginalDeliveryProxy):
    def __init__(self, app, owner: Path, remote_vault):
        super().__init__(app,control_root(owner),routes=routes(remote_vault),response_limit=2*1024*1024)


def selected_target(owner):
    path=control_root(owner)/'target.json'
    if not path.exists():return None
    value=original.read_json(path)
    require(set(value)=={'schema','vault_id'} and value['schema']==1
        and isinstance(value['vault_id'],str) and re.fullmatch(r'[0-9a-f]{32}',value['vault_id']),
        'OWNED_SYNC_TARGET_INVALID')
    return value['vault_id']


def freeze_target(owner, vault_id):
    require(isinstance(vault_id,str) and re.fullmatch(r'[0-9a-f]{32}',vault_id),'OWNED_SYNC_TARGET_INVALID')
    with original.control_lock(control_root(owner)):
        previous=selected_target(owner)
        if previous is not None:
            require(previous==vault_id,'OWNED_SYNC_TARGET_CHANGED')
            return {'schema':1,'vault_id':previous}
        current=original.read_json(control_root(owner)/'control.json')
        require(current=={'revision':0,'operation_id':None,'mode':'normal'},'OWNED_SYNC_CONTROL_ALREADY_ARMED')
        value={'schema':1,'vault_id':vault_id}
        original.atomic_json(control_root(owner)/'target.json',value,exclusive=True)
        return value


class TargetedDeliveryProxy:
    """Pass through until real API setup selects one immutable owned vault.

    Selection is a private CLI action, never a public fault-control HTTP route.
    Even a subsequently replaced target file cannot redirect existing controls.
    """
    def __init__(self, app, owner):
        self.app,self.owner=app,owner
        self.target=None
        self.proxy=None

    async def __call__(self, scope, receive, send):
        target=selected_target(self.owner)
        if self.target is not None:
            require(target==self.target,'OWNED_SYNC_TARGET_CHANGED')
        elif target is not None:
            self.target=target
            self.proxy=DeliveryProxy(self.app,self.owner,target)
        if self.proxy is None:await self.app(scope,receive,send)
        else:await self.proxy(scope,receive,send)


def observe_provider(owner, vault_id=None):
    """Actual fixed PG snapshot, no session hashes, passwords or object IO.

    The narrow owned fixture is capped instead of silently truncating proof.
    Object rows are metadata only; payload bytes require separate actual GETs.
    """
    config=original.fixed_service(owner)
    vault_id=selected_target(owner) if vault_id is None else vault_id
    require(isinstance(vault_id,str) and re.fullmatch(r'[0-9a-f]{32}',vault_id),'OWNED_SYNC_TARGET_INVALID')
    from sync_server.database import Database
    from sqlalchemy import text
    db=Database(config['environment']['SYNC_DATABASE_URL'])
    try:
        require(not db.sqlite,'OWNED_SYNC_POSTGRES_REQUIRED')
        with db.read_only() as connection:
            version=connection.execute(text('SELECT version()')).scalar_one()
            user=connection.execute(text('SELECT id FROM users WHERE username=:name'),{'name':config['accounts'][0]['username']}).scalar_one()
            vault=connection.execute(text('SELECT id,name,sequence,quota,used FROM vaults WHERE id=:vault AND user_id=:user'),{'vault':vault_id,'user':user}).mappings().one()
            parameters={'vault':vault_id}
            rows={}
            projections={
                'revisions':'sequence,file_id,base_revision,path,operation,hash,size,device_id,operation_id,fingerprint',
                'files':'file_id,sequence,path_key,deleted',
                'objects':'hash,size',
                'uploads':'id,device_id,hash,size,offset_bytes',
                'device_progress':'device_id,committed_sequence,committed_at,offered_sequence,offered_at',
            }
            orders={'revisions':'sequence','files':'file_id','objects':'hash','uploads':'id','device_progress':'device_id'}
            for table,columns in projections.items():
                count=connection.execute(text('SELECT COUNT(*) FROM '+table+' WHERE vault_id=:vault'),parameters).scalar_one()
                require(0<=count<=512,'OWNED_SYNC_OBSERVATION_LIMIT')
                values=connection.execute(text('SELECT '+columns+' FROM '+table+' WHERE vault_id=:vault ORDER BY '+orders[table]+' LIMIT 512'),parameters).mappings().all()
                require(len(values)==count,'OWNED_SYNC_OBSERVATION_CHANGED')
                rows[table]=[dict(value) for value in values]
        return {'source':original.SERVICE_SHA,'provider':'actual PostgreSQL; repeatable-read/read-only',
            'version':version,'vault':dict(vault),'rows':rows,'secrets_selected':False,'object_IO':False,
            'client_received_or_applied_acknowledgement':False,
            'delivery':original.read_json(control_root(owner)/'request-counts.json')
                if (control_root(owner)/'request-counts.json').exists() else {'counts':{},'events':[]}}
    finally:db.engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('serve','seed','observe','select-target','control'))
    parser.add_argument('--owner-run');parser.add_argument('--vault-id')
    parser.add_argument('--operation');parser.add_argument('--mode',choices=sorted(MODES))
    parser.add_argument('--expected-revision',type=int);parser.add_argument('--retry-original',action='store_true')
    args=parser.parse_args();owner=original.owned_root()
    require(owner.is_dir(),'OWNED_SYNC_NOT_PREPARED')
    if args.action=='seed':
        # The production create-user CLI stays unchanged, including stdin-only
        # password provisioning. No fake user, revision or vault SQL is inserted.
        original.main()
    elif args.action=='serve':
        require(args.owner_run==os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'],'OWNED_AUTH_RUN_INVALID')
        original.fixed_service(owner);control_root(owner)
        from sync_server.__main__ import application
        import uvicorn
        app=OriginalDeliveryProxy(TargetedDeliveryProxy(application(),owner),owner)
        uvicorn.run(app,host='127.0.0.1',port=19073,workers=1,access_log=False,log_level='warning')
    elif args.action=='observe':print(json.dumps(observe_provider(owner)))
    elif args.action=='select-target':
        require(args.vault_id is not None,'OWNED_SYNC_TARGET_INVALID')
        observation=observe_provider(owner,args.vault_id)
        value=freeze_target(owner,observation['vault']['id'])
        print(json.dumps(value))
    else:
        require(args.operation is not None and args.mode is not None and args.expected_revision is not None,'OWNED_AUTH_CONTROL_INVALID')
        require(selected_target(owner) is not None,'OWNED_SYNC_TARGET_INVALID')
        print(json.dumps(arm_control(owner,args.operation,args.mode,args.expected_revision,retry=args.retry_original)))


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Never print an exception that can contain a provider DSN or SQL data.
        print('OWNED_SYNC_FAILED_'+type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
