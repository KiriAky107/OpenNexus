"""Actual packaged Desktop UI actions for the owned two-product pair.

Source preparation until wired to the hosted paired jobs. Call only inside
start_product: its failure boundary redacts Playwright arguments and preserves
the original owned process/profile. No backend write or HTTP peer substitutes
for these UI actions. The marker precedes any input; unknown input is not replayed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from android_desktop_handoff import canonical, require, validate_payload
from android_desktop_product import directory_identity, original_marker, owned_root
from android_desktop_recipient import write_new


def names(english, chinese):
    return re.compile('^(?:' + re.escape(english) + '|' + re.escape(chinese) + ')$')


def canonical_uuid(value):
    try:return type(value) is str and str(uuid.UUID(value)) == value
    except (ValueError,TypeError,AttributeError):return False


def bound_identity(value, payload, previous=None):
    """Public original identity only; never returns a token or auth payload."""
    binding = value.get('binding')
    require(type(binding) is dict and binding.get('endpoint') == payload['endpoint']
            and binding.get('account') == payload['username']
            and binding.get('remote_vault') == payload['vault_id']
            and value.get('credential_state') == 'ready', 'DESKTOP_PAIR_BOUND_SCOPE_INVALID')
    require(canonical_uuid(binding.get('id')) and canonical_uuid(value.get('vault_id'))
            and type(binding.get('cursor')) is int and 0 <= binding['cursor'] <= 9007199254740991,
            'DESKTOP_PAIR_BOUND_SCOPE_INVALID')
    identity = {'local_vault_id':value['vault_id'],'binding_id':binding['id'],'remote_vault_id':binding['remote_vault']}
    if previous is not None:
        require(identity == previous, 'DESKTOP_PAIR_COLD_IDENTITY_CHANGED')
    return identity


class OwnedDesktopUI:
    def __init__(self, context, page, vault, attempt, stage):
        root = owned_root(os.environ, context)  # Refuse local/user profiles first.
        original_marker(root, context)
        require(type(stage) is int and stage in (1,2)
                and Path(vault) == root/'vault' and Path(attempt) == root/('stage-'+str(stage)),
                'DESKTOP_PAIR_UI_OWNER_INVALID')
        directory_identity(vault); directory_identity(attempt)
        self.context,self.page,self.vault,self.attempt,self.stage = context,page,Path(vault),Path(attempt),stage

    def intent(self, name, public):
        require(name in ('sign-in','bind-confirm','source-edit','canvas-edit','sync-now-initial','sync-now-outbound','sync-now-cold','pause-cycle','resume-cycle'),
                'DESKTOP_PAIR_UI_INTENT_INVALID')
        value={'context':self.context,'stage':self.stage,'driver_intent_id':str(uuid.uuid4()),
               'native_operation_id':False,'action':name,'original':public,'automatic_retry':False}
        write_new(self.attempt/(name+'-original-intent.json'),canonical(value))
        return value

    def status(self):
        return self.page.evaluate("()=>pairInvoke('sync_status')")

    def panel(self):
        self.page.evaluate("()=>pairRouter.push('/settings')")
        self.page.locator('.settings-nav').get_by_role('button',name='Sync',exact=True).click()
        panel=self.page.locator('.sync-settings')
        panel.get_by_role('heading',name='OpenNexus Sync',exact=True).wait_for()
        return panel

    def login_and_merge(self, payload, device_name, own_unlock_phrase):
        require(self.stage == 1, 'DESKTOP_PAIR_COLD_LOGIN_REFUSED')
        validate_payload(payload)
        require(device_name == 'Owned Desktop '+str(self.context['run_id'])
                and re.fullmatch(r'[A-Za-z0-9_-]{40,100}',own_unlock_phrase), 'DESKTOP_PAIR_LOGIN_INPUT_INVALID')
        self.intent('sign-in',{'remote_vault_id':payload['vault_id'],'device_name':device_name,
                    'account_scope_sha256':hashlib.sha256(canonical([payload['endpoint'],payload['username']])).hexdigest()})
        self.page.evaluate("()=>pairRouter.push('/settings')")
        self.page.locator('.settings-nav').get_by_role('button',name=names('Data and credentials','\u6570\u636e\u4e0e\u51ed\u636e')).click()
        self.page.get_by_text(names('Device credential vault','\u8bbe\u5907\u51ed\u636e\u4fdd\u9669\u5e93')).first.click()
        credentials=self.page.evaluate("()=>pairInvoke('credentials_status')")
        require(type(credentials.get('locked')) is bool, 'DESKTOP_PAIR_CREDENTIAL_STATUS_INVALID')
        if credentials['locked']:
            section=self.page.locator('.credential-vault')
            section.get_by_label(names('Vault password','\u89e3\u9501\u53e3\u4ee4')).fill(own_unlock_phrase)
            section.get_by_role('button',name=names('Unlock','\u89e3\u9501')).click()
            self.page.wait_for_function("async()=>!(await pairInvoke('credentials_status')).locked")
        panel=self.panel();before=self.status()
        require(before.get('binding') is None, 'DESKTOP_PAIR_ALREADY_BOUND')
        for label,key in [('Server URL','endpoint'),('Account','username'),('Password','password')]:
            chinese={'Server URL':'\u670d\u52a1\u5668\u5730\u5740','Account':'\u8d26\u6237','Password':'\u5bc6\u7801'}[label]
            panel.get_by_label(names(label,chinese)).fill(payload[key])
        panel.get_by_label(names('Device name','\u8bbe\u5907\u540d\u79f0')).fill(device_name)
        require(not panel.locator('.test-http input').is_checked(), 'DESKTOP_PAIR_PLAINTEXT_REFUSED')
        panel.get_by_role('button',name=names('Sign in','\u767b\u5f55')).click()
        panel.get_by_role('button',name=names('Refresh vaults','\u5237\u65b0\u8fdc\u7aef\u5e93')).wait_for()
        require(panel.get_by_label(names('Password','\u5bc6\u7801')).input_value() == '', 'DESKTOP_PAIR_PASSWORD_NOT_CLEARED')
        # In the published UI this wrapping label also includes every option's
        # text. Scope the original combobox to its authenticated connection panel.
        panel.locator('.sync-connect').get_by_role('combobox').select_option(payload['vault_id'])
        panel.get_by_role('button',name=names('Preview merge','\u9884\u89c8\u5408\u5e76')).click()
        preview=panel.locator('.sync-preview')
        preview.get_by_role('button',name=names('Confirm merge and bind','\u786e\u8ba4\u5408\u5e76\u5e76\u7ed1\u5b9a')).wait_for()
        original=preview.inner_text()
        require(0 < len(original) <= 65536 and 'runtime-note.md' in original
                and self.status().get('binding') is None, 'DESKTOP_PAIR_PREVIEW_INVALID')
        self.intent('bind-confirm',{'local_vault_id':before['vault_id'],'remote_vault_id':payload['vault_id'],
                    'preview_DOM_sha256':hashlib.sha256(original.encode()).hexdigest(),
                    'native_preview_fingerprint_exposed_by_UI':False,'mode':'merge',
                    'normal_preference_seed_may_create_real_jobs':True})
        preview.get_by_role('button',name=names('Confirm merge and bind','\u786e\u8ba4\u5408\u5e76\u5e76\u7ed1\u5b9a')).click()
        dialog=self.page.locator('.action-dialog')
        require(dialog.count()==1, 'DESKTOP_PAIR_CONFIRMATION_AMBIGUOUS')
        dialog.get_by_role('button',name=names('Confirm','\u786e\u5b9a')).click()
        value=self.wait_status(lambda s:s.get('binding') is not None)
        return bound_identity(value,payload)

    def wait_status(self, condition, timeout=120):
        require(type(timeout) is int and 1 <= timeout <= 180, 'DESKTOP_PAIR_WAIT_INVALID')
        deadline=time.monotonic()+timeout
        while True:
            value=self.status()
            if condition(value):return value
            require(time.monotonic()<deadline, 'DESKTOP_PAIR_STATUS_TIMEOUT')
            time.sleep(.25)

    def account_identity(self, payload, previous=None):
        require(self.stage == 1 or previous is not None, 'DESKTOP_PAIR_COLD_ORIGINAL_REQUIRED')
        scope_keys=('local_vault_id','binding_id','remote_vault_id')
        expected=None if previous is None else {k:previous.get(k) for k in scope_keys}
        value=self.status();identity=bound_identity(value,payload,expected)
        details=self.page.evaluate("args=>pairInvoke('sync_account_status',{request:args})",
                   {'vault_id':identity['local_vault_id'],'binding_id':identity['binding_id']})
        current=details.get('current_device_id')
        require(details.get('vault_id')==identity['local_vault_id']
                and details.get('binding_id')==identity['binding_id']
                and details.get('vault',{}).get('id')==identity['remote_vault_id']
                and type(current) is str and re.fullmatch(r'[0-9a-f]{32}',current)
                and any(d.get('id')==current and d.get('revoked') is False for d in details.get('devices',[])),
                'DESKTOP_PAIR_CURRENT_DEVICE_INVALID')
        require(previous is None or current == previous.get('current_device_id'), 'DESKTOP_PAIR_COLD_DEVICE_CHANGED')
        return {**identity,'current_device_id':current,'credential_state_ready':True}

    def edit_original_note(self, expected_before, after):
        require(type(expected_before) is bytes and type(after) is bytes and 0<len(after)<=1024**2,
                'DESKTOP_PAIR_EDIT_INPUT_INVALID')
        note=self.vault/'runtime-note.md'
        require(not note.is_symlink() and note.is_file() and note.read_bytes()==expected_before,
                'DESKTOP_PAIR_ORIGINAL_NOTE_CHANGED')
        self.intent('source-edit',{'path':'runtime-note.md','before_sha256':hashlib.sha256(expected_before).hexdigest(),
                    'after_sha256':hashlib.sha256(after).hexdigest(),'bytes':len(after),'filesystem_write_by_driver':False})
        self.page.evaluate("async()=>{await pairRouter.push('/workspace');await pairPinia._s.get('workspace').refreshFileTree()}")
        self.page.locator('.tree-node[aria-label="runtime-note.md"]').click()
        self.page.locator('.mode-switch').get_by_role('button',name=names('Source','\u6e90\u7801')).click()
        editor=self.page.get_by_label(names('Markdown source editor','Markdown \u6e90\u7801\u7f16\u8f91\u5668'))
        editor.fill(after.decode('utf-8'))
        self.page.locator('.editor-header .save-button').click()
        self.wait_original_note_save(note,after)
        return {'path':'runtime-note.md','sha256':hashlib.sha256(after).hexdigest(),'bytes':len(after),
                'actual_source_editor_and_Save':True,'filesystem_write_by_driver':False}

    def wait_original_note_save(self, note, after):
        require(note==self.vault/'runtime-note.md','DESKTOP_PAIR_ORIGINAL_NOTE_CHANGED')
        observed={'read_attempts':0,'transient_read_failures':{},'matched_requested_bytes':False,
                  'last_read_bytes':None,'last_read_sha256':None,'Save_or_edit_replayed':False}
        deadline=time.monotonic()+60
        try:
            while True:
                require(not note.is_symlink(),'DESKTOP_PAIR_ORIGINAL_NOTE_CHANGED')
                observed['read_attempts']+=1
                try:
                    body=note.read_bytes()
                except (FileNotFoundError,PermissionError) as error:
                    # A file can be briefly unavailable during native atomic
                    # replacement. Reconcile only this original read; Save is
                    # already dispatched and must never be sent again here.
                    name=type(error).__name__
                    observed['transient_read_failures'][name]=observed['transient_read_failures'].get(name,0)+1
                else:
                    observed.update(last_read_bytes=len(body),last_read_sha256=hashlib.sha256(body).hexdigest())
                    if body==after:
                        observed['matched_requested_bytes']=True
                        return
                require(time.monotonic()<deadline and observed['read_attempts']<=320,'DESKTOP_PAIR_ORIGINAL_SAVE_UNKNOWN')
                time.sleep(.2)
        finally:
            write_new(self.attempt/'source-save-observation.json',canonical(observed))

    def set_paused(self, paused):
        require(self.stage==1 and type(paused) is bool, 'DESKTOP_PAIR_PAUSE_SCOPE_INVALID')
        panel=self.panel();current=self.status()
        require(current.get('binding') is not None and type(current.get('paused')) is bool,
                'DESKTOP_PAIR_PAUSE_SCOPE_INVALID')
        action='pause-cycle' if paused else 'resume-cycle'
        if current.get('paused') is paused:
            return {'paused':paused,'new_UI_input':False,
                    'original_intent_readonly_reconciled':(self.attempt/(action+'-original-intent.json')).exists()}
        self.intent(action,{'binding_id':current['binding']['id'],'paused':paused})
        control=names('Pause sync','\u6682\u505c\u540c\u6b65') if paused else names('Resume sync','\u7ee7\u7eed\u540c\u6b65')
        panel.get_by_role('button',name=control).click()
        self.wait_status(lambda s:s.get('paused') is paused and (not paused or not s.get('running')))
        return {'paused':paused,'new_UI_input':True,'automatic_retry':False}

    def edit_original_canvas(self, expected_before, node_id, text, x, y):
        require(self.stage==1 and type(expected_before) is bytes and 0<len(expected_before)<=4*1024**2
                and re.fullmatch(r'[a-z][a-z0-9-]{0,63}',node_id) and type(text) is str and 0<len(text)<=2048
                and type(x) is int and type(y) is int, 'DESKTOP_PAIR_CANVAS_INPUT_INVALID')
        require(self.status().get('paused') is True, 'DESKTOP_PAIR_CANVAS_BATCH_NOT_PAUSED')
        path=self.vault/'peer.canvas'
        require(not path.is_symlink() and path.is_file() and path.read_bytes()==expected_before,
                'DESKTOP_PAIR_ORIGINAL_CANVAS_CHANGED')
        expected=json.loads(expected_before)
        selected=[n for n in expected.get('nodes',[]) if n.get('id')==node_id]
        require(len(selected)==1 and selected[0].get('type')=='text', 'DESKTOP_PAIR_CANVAS_NODE_INVALID')
        selected[0].update(text=text,x=x,y=y)
        self.intent('canvas-edit',{'path':'peer.canvas','node_id':node_id,'x':x,'y':y,
                    'before_sha256':hashlib.sha256(expected_before).hexdigest(),
                    'new_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'filesystem_write_by_driver':False})
        self.page.evaluate("async()=>{await pairRouter.push('/workspace');await pairPinia._s.get('workspace').refreshFileTree()}")
        self.page.locator('.tree-node[aria-label="peer.canvas"]').click()
        self.page.locator('.canvas-node[data-node-id="'+node_id+'"]').click()
        if self.page.locator('.canvas-inspector').count()==0:
            self.page.get_by_role('button',name=names('Show right sidebar','\u663e\u793a\u53f3\u4fa7\u680f')).click()
        properties=self.page.locator('.canvas-inspector .node-properties')
        properties.get_by_label(names('Node content','\u8282\u70b9\u5185\u5bb9')).fill(text)
        properties.get_by_label('x',exact=True).fill(str(x));properties.get_by_label('y',exact=True).fill(str(y))
        properties.get_by_role('button',name=names('Apply property changes','\u5e94\u7528\u5c5e\u6027\u4fee\u6539')).click()
        self.page.locator('.editor-header .save-button').click()
        deadline=time.monotonic()+60
        while True:
            raw=path.read_bytes()
            if json.loads(raw)==expected:break
            require(time.monotonic()<deadline, 'DESKTOP_PAIR_CANVAS_SAVE_UNKNOWN')
            time.sleep(.2)
        return {'path':'peer.canvas','sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                'actual_UI_text_and_coordinates_and_Save':True,'unknown_fields_and_other_nodes_preserved':True,
                'filesystem_write_by_driver':False,'original_seed_SHA256':hashlib.sha256(expected_before).hexdigest()}

    def finish_actual_cycle(self, *, cycle, force=False):
        require(type(force) is bool and (self.stage==1 and cycle in ('initial','outbound')
                or self.stage==2 and cycle=='cold'), 'DESKTOP_PAIR_CYCLE_INPUT_INVALID')
        panel=self.panel();current=self.wait_status(lambda s:not s.get('running'))
        require(current.get('binding') is not None and current.get('credential_state')=='ready'
                and not current.get('paused') and not current.get('conflicts'), 'DESKTOP_PAIR_CYCLE_STATE_INVALID')
        manual=force or current.get('pending',0)>0
        if manual:
            self.intent('sync-now-'+cycle,{'pending_before':current['pending'],'manual_click_count':1,'explicit_cycle':cycle})
            panel.get_by_role('button',name=names('Sync now','\u7acb\u5373\u540c\u6b65')).click()
        final=self.wait_status(lambda s:not s.get('running') and s.get('pending')==0
                   and not s.get('error') and not s.get('conflicts') and s.get('last_cycle_success_at') is not None)
        return {'manual_UI_trigger':manual,'automatic_client_may_have_preceded_manual_control':True,
                'pending':final['pending'],'cursor':final['binding']['cursor'],
                'last_cycle_success':final['last_cycle_success_at'],'client_apply_ACK_API_claimed':False}

    def read_cold_originals(self, note, canvas_text, image_path):
        """Actual rendered content after the original client's cold incoming cycle."""
        require(self.stage==2 and type(note) is bytes and 0<len(note)<=1024**2
                and type(canvas_text) is str and 0<len(canvas_text)<=2048
                and re.fullmatch(r'attachments/[0-9a-f]{2}/[0-9a-f]{64}\.png',image_path),
                'DESKTOP_PAIR_COLD_RENDER_INPUT_INVALID')
        self.page.evaluate("async()=>{await pairRouter.push('/workspace');await pairPinia._s.get('workspace').refreshFileTree()}")
        self.page.locator('.tree-node[aria-label="runtime-note.md"]').click()
        self.page.locator('.mode-switch').get_by_role('button',name=names('Source','\u6e90\u7801')).click()
        editor=self.page.get_by_label(names('Markdown source editor','Markdown \u6e90\u7801\u7f16\u8f91\u5668'))
        deadline=time.monotonic()+60
        while True:
            actual=editor.evaluate("e=>Array.from(e.querySelectorAll(':scope>.cm-line'),line=>line.textContent).join('\\n')")
            if actual==note.decode('utf-8'):break
            require(time.monotonic()<deadline, 'DESKTOP_PAIR_COLD_RENDERED_SOURCE_MISMATCH')
            time.sleep(.2)
        self.page.locator('.tree-node[aria-label="peer.canvas"]').click()
        text=self.page.locator('.canvas-node[data-node-id="owned-pair-text"] .canvas-node-content')
        image=self.page.locator('.canvas-node[data-node-id="owned-pair-image"] img')
        deadline=time.monotonic()+60
        while True:
            rendered=image.evaluate("img=>({complete:img.complete,width:img.naturalWidth,height:img.naturalHeight,alt:img.alt,visible:img.getBoundingClientRect().width>0&&img.getBoundingClientRect().height>0})")
            if text.text_content()==canvas_text and rendered=={'complete':True,'width':2,'height':2,'alt':image_path,'visible':True}:break
            require(time.monotonic()<deadline, 'DESKTOP_PAIR_COLD_CANVAS_OR_IMAGE_NOT_RENDERED')
            time.sleep(.2)
        return {'actual_cold_source_rendered_sha256':hashlib.sha256(actual.encode()).hexdigest(),
                'actual_cold_Canvas_text_sha256':hashlib.sha256(canvas_text.encode()).hexdigest(),
                'actual_2x2_image_decoded':True,'UI_input':'original file selection and Source view only',
                'edit_Save_login_or_binding_sent':False}
