"""Actual original packaged Desktop stages for the same-run Android pair.

No local Windows profile may be used. The CLI is incomplete until the Linux
counterpart and original workflow producers are wired; source checks are not a
product-pair result. Private fixture payloads never enter artifacts or errors.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import urllib.request
from urllib.parse import urlsplit

from android_desktop_exchange import exchange_bytes
from android_desktop_exchange_api import OriginalRunAPI,wait_original_exchange,failure_label
from android_desktop_handoff import canonical,context_bytes,require
from android_desktop_pair_metadata import owned_native_metadata
from android_desktop_pair_ui import OwnedDesktopUI
from android_desktop_product import DESKTOP_SOURCE,INSTALLER_SHA256,digest,extract_installer,original_marker,owned_root,start_product
from android_desktop_recipient import prepare_receiving_key,read_receiving_key,receive_original_fixture,write_new
from android_product_pair_data import ANDROID_NOTE,ANDROID_CANVAS_TEXT,ANDROID_POSITION,CANVAS_SEED,DESKTOP_NOTE,DESKTOP_CANVAS_TEXT,DESKTOP_POSITION,DRAFT,IMAGE_BYTES,IMAGE_PATH,TEXT_NODE
from android_product_pair_evidence import desktop_cold_files,desktop_outbound_files,sha,validate_canvas_edit
from android_product_pair_stages import ACTIONS,PATHS,validate_final,validate_stage
from android_product_pair_clients import fixed_android_client


INSTALLER_URL='https://github.com/KiriAky107/OpenNexus/releases/download/v0.6.0-beta6/OpenNexus_0.6.0-beta6_x64-setup.exe'


def original_context(env):
    raw=env.get('GITHUB_RUN_ID','')
    require(re.fullmatch(r'[1-9][0-9]{0,15}',raw) is not None, 'PAIR_DESKTOP_ANDROID_CONTEXT_INVALID')
    client=fixed_android_client(env.get('ANDROID_COMPILED_SOURCE'),env.get('ANDROID_BUILD_RUN_ID'))
    context={'run_id':int(raw),'tools_commit':env.get('GITHUB_SHA',''),
             'android_commit':client.source,'desktop_commit':DESKTOP_SOURCE}
    context_bytes(context)
    owned_root(env,context)  # Before downloads, file reads or process/profile access.
    return context


def private_root(context):
    root=owned_root(os.environ,context);original_marker(root,context);return root


def read_bodies(vault):
    bodies={}
    for name in PATHS:
        path=vault/name
        require(path.resolve().is_relative_to(vault.resolve()) and not path.is_symlink(), 'PAIR_DESKTOP_FILE_SCOPE_INVALID')
        for parent in path.parents:
            require(not parent.is_symlink() and not getattr(parent.stat(),'st_file_attributes',0)&0x400,
                    'PAIR_DESKTOP_FILE_SCOPE_INVALID')
            if parent==vault:break
        info=path.stat(follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and 0<info.st_size<=4*1024**2
                and not getattr(info,'st_file_attributes',0)&0x400, 'PAIR_DESKTOP_FILE_SCOPE_INVALID')
        bodies[name]=path.read_bytes()
    return bodies


def prepare(context):
    owned_root(os.environ,context)  # Reject an unowned host before delegated key IO.
    prepare_receiving_key(context);root=private_root(context)
    installer=root/'original-installer.exe';partial=root/'original-installer.exe.partial'
    write_new(root/'installer-intent.json',canonical({'source':DESKTOP_SOURCE,'SHA256':INSTALLER_SHA256,
              'bytes':105741178,'automatic_retry':False}))
    with urllib.request.urlopen(INSTALLER_URL,timeout=90) as source:
        target=urlsplit(source.geturl())
        require(target.scheme=='https' and target.hostname is not None and target.hostname.endswith('.githubusercontent.com'),
                'PAIR_DESKTOP_INSTALLER_ORIGIN_INVALID')
        with partial.open('xb') as output:
            size=0
            while block:=source.read(1024**2):
                size+=len(block);require(size<=105741178,'PAIR_DESKTOP_INSTALLER_SIZE_INVALID');output.write(block)
            output.flush();os.fsync(output.fileno())
    require(size==105741178 and digest(partial)==INSTALLER_SHA256,'PAIR_DESKTOP_INSTALLER_HASH_INVALID')
    partial.rename(installer);extract_installer(context,installer)


def publish(context,stage,body):
    root=private_root(context);validate_stage(context,stage,body)
    output=root/stage;output.mkdir(exist_ok=False)
    write_new(output/'exchange.json',exchange_bytes(context,stage,body))


def stage_body(context,stage,identity,files,metadata,cleanup):
    require(cleanup.get('original_process_exited') is True and cleanup.get('profile_preserved') is True
            and cleanup.get('only_original_owned_process') is True, 'PAIR_DESKTOP_OWNED_CLEANUP_UNKNOWN')
    return {'passed':True,'client_source':context['desktop_commit'],'client_artifact_sha256':INSTALLER_SHA256,
            'native_metadata_sha256':sha(canonical(metadata)),
            'identity':{k:identity[k] for k in ('local_vault_id','binding_id','remote_vault_id','current_device_id')},
            'files':files,'actions':ACTIONS[stage],'actual_client_closed':True,'owned_state_preserved':True}


def first(context):
    root=private_root(context);api=OriginalRunAPI(context,os.environ)
    payload,provenance=receive_original_fixture(context,api,root/'sealed-cache')
    write_new(root/'sealed-origin.json',canonical(provenance))
    # A synthetic password for this new credential vault, unrelated to user
    # passwords or QA/release signing. Never log it or export it to a receipt.
    phrase=hashlib.sha256(read_receiving_key(context)+b'owned credential vault only').hexdigest()
    with start_product(context,1) as (page,vault,attempt):
        ui=OwnedDesktopUI(context,page,vault,attempt,1)
        ui.login_and_merge(payload,'Owned Desktop '+str(context['run_id']),phrase)
        ui.finish_actual_cycle(cycle='initial',force=True)
        identity=ui.account_identity(payload)
        original_note=vault/'runtime-note.md'
        require(original_note.is_file() and not original_note.is_symlink()
                and original_note.read_bytes()==DRAFT.encode(), 'PAIR_DESKTOP_FIRST_ANDROID_ORIGINAL_MISSING')
        before=owned_native_metadata(context,identity['binding_id'])
        write_new(root/'first-native-before.json',canonical(before))
        ui.set_paused(True)
        # Explicit owned filesystem setup is not described as GUI import.
        # Actual subsequent Markdown and Canvas edits both use product UI.
        write_new(attempt/'media-input-intent.json',canonical({'Canvas_seed_sha256':sha(CANVAS_SEED),
                  'PNG_sha256':sha(IMAGE_BYTES),'filesystem_setup_not_GUI_import':True}))
        image=vault/IMAGE_PATH
        require(not image.exists() and not (vault/'peer.canvas').exists(), 'PAIR_DESKTOP_MEDIA_ALREADY_EXISTS')
        image.parent.mkdir(parents=True,exist_ok=False)
        write_new(image,IMAGE_BYTES);write_new(vault/'peer.canvas',CANVAS_SEED)
        note_action=ui.edit_original_note(DRAFT.encode(),DESKTOP_NOTE)
        canvas_action=ui.edit_original_canvas(CANVAS_SEED,TEXT_NODE,DESKTOP_CANVAS_TEXT,*DESKTOP_POSITION)
        bodies=read_bodies(vault)
        require(bodies['runtime-note.md']==DESKTOP_NOTE and bodies[IMAGE_PATH]==IMAGE_BYTES, 'PAIR_DESKTOP_OUTBOUND_BYTES_CHANGED')
        canvas=validate_canvas_edit(CANVAS_SEED,bodies['peer.canvas'],DESKTOP_CANVAS_TEXT,DESKTOP_POSITION)
        ui.set_paused(False);cycle=ui.finish_actual_cycle(cycle='outbound',force=True)
        require(ui.account_identity(payload)==identity, 'PAIR_DESKTOP_OUTBOUND_IDENTITY_CHANGED')
        native=owned_native_metadata(context,identity['binding_id'])
        files=desktop_outbound_files(native,identity,bodies)
        # The edited Markdown must retain the file downloaded from Android.
        original=[h for h in before['heads'] if h['path']=='runtime-note.md']
        edited=next(f for f in files if f['path']=='runtime-note.md')
        require(len(original)==1 and edited['file_id']==original[0]['file_id']
                and edited['base_revision']==original[0]['revision'], 'PAIR_DESKTOP_REPLACED_ANDROID_FILE')
        require(read_bodies(vault)==bodies,'PAIR_DESKTOP_POST_CYCLE_BYTES_CHANGED')
        write_new(root/'first-native.json',canonical(native));write_new(root/'first-identity.json',canonical(identity))
        write_new(attempt/'actual-edit-actions.json',canonical({'note':note_action,'Canvas':canvas_action,'preservation':canvas,'cycle':cycle}))
        write_new(root/'first-Canvas-original.bin',bodies['peer.canvas'])
    cleanup=json.loads((root/'stage-1/cleanup.json').read_bytes())
    publish(context,'desktop-first',stage_body(context,'desktop-first',identity,files,native,cleanup))


def final(context):
    root=private_root(context);api=OriginalRunAPI(context,os.environ)
    payload,_=receive_original_fixture(context,api,root/'sealed-cache')
    android,provenance=wait_original_exchange(context,'android-return',api,root/'android-return-cache')
    first=json.loads((root/'desktop-first/exchange.json').read_bytes())['body']
    previous=json.loads((root/'first-native.json').read_bytes());identity=json.loads((root/'first-identity.json').read_bytes())
    with start_product(context,2) as (page,vault,attempt):
        ui=OwnedDesktopUI(context,page,vault,attempt,2)
        require(ui.account_identity(payload,identity)==identity,'PAIR_DESKTOP_COLD_IDENTITY_CHANGED')
        before=owned_native_metadata(context,identity['binding_id'])
        write_new(attempt/'before-cold-native.json',canonical(before))
        cycle=ui.finish_actual_cycle(cycle='cold',force=True,min_cursor=max(row['sequence'] for row in android['files']))
        require(ui.account_identity(payload,identity)==identity,'PAIR_DESKTOP_COLD_IDENTITY_CHANGED')
        bodies=read_bodies(vault)
        write_new(attempt/'after-cold-files.json',canonical({path:{'bytes':len(body),'sha256':sha(body)} for path,body in bodies.items()}))
        write_new(attempt/'after-cold-native.json',canonical(owned_native_metadata(context,identity['binding_id'])))
        require(bodies['runtime-note.md']==ANDROID_NOTE and bodies[IMAGE_PATH]==IMAGE_BYTES,'PAIR_DESKTOP_COLD_BYTES_CHANGED')
        preservation=validate_canvas_edit((root/'first-Canvas-original.bin').read_bytes(),bodies['peer.canvas'],ANDROID_CANVAS_TEXT,ANDROID_POSITION)
        rendered=ui.read_cold_originals(ANDROID_NOTE,ANDROID_CANVAS_TEXT,IMAGE_PATH)
        native=owned_native_metadata(context,identity['binding_id'])
        files=desktop_cold_files(native,identity,bodies,previous,android['files'])
        write_new(attempt/'actual-cold-read-actions.json',canonical({'rendered':rendered,'cycle':cycle,'preservation':preservation}))
        write_new(root/'final-native.json',canonical(native));write_new(root/'android-return-origin.json',canonical(provenance))
    cleanup=json.loads((root/'stage-2/cleanup.json').read_bytes())
    body=stage_body(context,'desktop-final',identity,files,native,cleanup)
    validate_final(context,first,android,body);publish(context,'desktop-final',body)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=('prepare','first','final'))
    args=parser.parse_args();context=original_context(os.environ)
    {'prepare':prepare,'first':first,'final':final}[args.stage](context)
    print('OWNED_DESKTOP_PAIR_STAGE_'+args.stage.upper()+'_FINISHED')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('OWNED_DESKTOP_PAIR_STAGE_FAILED_'+failure_label(error))
        raise SystemExit(1)
