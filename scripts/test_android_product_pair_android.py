"""Synthetic original-identity admission; no local ADB/device/service is used."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from android_auth_fixture import SERVICE_SHA
from android_desktop_handoff import HandoffError
from android_product_pair_android import auth_identity,native_identity,original_pair_ui_phase,read_frozen_fixture,resume_probe


LOGIN='11111111-1111-4111-8111-111111111111'
REFRESH='22222222-2222-4222-8222-222222222222'


class AndroidOriginalResume(unittest.TestCase):
    def setUp(self):
        self.context={'run_id':1234,'tools_commit':'a'*40,'android_commit':'b'*40,
                      'desktop_commit':'931b96d6c4dab7a6c8f1d9ac4548dd0e51297c30'}
        row={'operation_id':LOGIN,'endpoint':'https://owned-example.trycloudflare.com/',
             'account':'android-owned-a-'+'c'*12,'device_name':'Owned Android original',
             'device_id':'d'*32,'kind':'login','phase':'active','generation':1,'previous_operation':None}
        self.auth={'integrity':'ok','secret_payload_selected':False,'intents':[row],
                   'selection':{k:row[k] for k in ('operation_id','endpoint','account','generation')}}
        self.auth['selection']['revision']=1
        self.native={'integrity':'ok','credentials_selected':False,
                     'binding':{'id':'33333333-3333-4333-8333-333333333333','state':'active','cursor':1,'remote_vault':'e'*32},
                     'association':{'state':'bound','remote_vault':'e'*32,'auth_operation_id':LOGIN,
                                    'operation_id':'44444444-4444-4444-8444-444444444444','request_hash':'f'*64}}

    def refreshed(self):
        value=deepcopy(self.auth);row=dict(value['intents'][0],operation_id=REFRESH,
                                         kind='refresh',generation=2,previous_operation=LOGIN)
        value['intents'].append(row)
        value['selection'].update(operation_id=REFRESH,generation=2,revision=2)
        return value

    def test_same_device_native_refresh_is_allowed_but_relogin_and_broken_ancestry_are_not(self):
        original=auth_identity(self.auth);value=self.refreshed()
        resumed=auth_identity(value,original)
        self.assertEqual(resumed['chain'],[LOGIN,REFRESH])
        for mutation in ('new-device','new-login','missing-parent','unrelated','cycle','wrong-generation','pending'):
            changed=deepcopy(value);current=changed['intents'][-1]
            if mutation=='new-device':current['device_id']='0'*32
            elif mutation=='new-login':current.update(kind='login',previous_operation=None)
            elif mutation=='missing-parent':changed['intents'].pop(0)
            elif mutation=='unrelated':changed['intents'].append(dict(changed['intents'][0],operation_id='55555555-5555-4555-8555-555555555555'))
            elif mutation=='cycle':current['previous_operation']=REFRESH
            elif mutation=='wrong-generation':current['generation']=3;changed['selection']['generation']=3
            else:current['phase']='refresh_pending'
            with self.subTest(mutation=mutation),self.assertRaises(HandoffError):auth_identity(changed,original)

    def test_original_binding_receipt_and_cursor_cannot_be_replaced_or_rewound(self):
        auth=auth_identity(self.auth);original=native_identity(self.native,auth)
        changed=deepcopy(self.native);changed['binding']['cursor']=4
        self.assertEqual(native_identity(changed,auth,original)['cursor'],4)
        for section,key,value in [('binding','cursor',0),('binding','cursor',True),
                                  ('binding','id','55555555-5555-4555-8555-555555555555'),
                                  ('association','request_hash','0'*64),('association','auth_operation_id',REFRESH)]:
            changed=deepcopy(self.native);changed[section][key]=value
            with self.subTest(key=key),self.assertRaises(HandoffError):native_identity(changed,auth,original)

    def test_local_host_is_rejected_before_original_file_probe_or_adb_access(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True),patch('android_product_pair_android.SyncProbe') as probe:
            root=Path(folder);args=SimpleNamespace(serial='emulator-5562')
            with self.assertRaises(RuntimeError):resume_probe(self.context,args,root/'unread.apk',{},root)
            probe.assert_not_called();self.assertEqual(list(root.iterdir()),[])

    def test_installed_tool_or_emulator_replacement_refuses_before_any_product_ui(self):
        for changed in ('device','packages'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);args=SimpleNamespace(serial='emulator-5562',commit='b'*40,tools_commit='a'*40)
                anchor={'schema':1,'context':self.context,'serial':args.serial,'device':{'boot_id':'original'},
                        'packages':[{'sha256':'c'*64}],'activity':'original'}
                probe=Mock()
                with patch('android_product_pair_android.hosted_scope'),patch('android_product_pair_android.read_json',return_value=anchor),\
                     patch('android_product_pair_android.SyncProbe',return_value=probe),\
                     patch('android_product_pair_android.observe_device',return_value={'boot_id':'changed' if changed=='device' else 'original'}),\
                     patch('android_product_pair_android.observe_packages',return_value=[{'sha256':('0' if changed=='packages' else 'c')*64}]):
                    with self.assertRaisesRegex(HandoffError,'INSTALLED_ORIGINAL_REPLACED'):
                        resume_probe(self.context,args,root/'original.apk',{'sha256':'c'*64},root)
                probe.boot.assert_not_called();probe.run.assert_not_called();probe.build_driver.assert_not_called()
                probe.shell.assert_not_called();probe.auth_phase.assert_not_called();probe.sync_phase.assert_not_called()

    def test_expired_independent_setup_session_does_not_trigger_a_new_setup_login(self):
        with tempfile.TemporaryDirectory() as folder,patch('android_product_pair_android.hosted_scope'):
            root=Path(folder);(root/'sync-setup').mkdir();(root/'sync-control').mkdir()
            account={'username':'android-owned-a-'+'c'*12,'password':'x'*48}
            files={'owner-state.json':{'source':SERVICE_SHA,'result':'ready','fixture':'sync','endpoint':'https://owned-example.trycloudflare.com'},
                   'device-private.json':{'endpoint':'https://owned-example.trycloudflare.com','accounts':[account,{'username':'unused'}]},
                   'sync-setup/vault-receipt.json':{'vault_id':'e'*32,'name':'Owned Android Sync '+'e'*32},
                   'sync-control/target.json':{'schema':1,'vault_id':'e'*32},
                   'sync-setup/login-response.private.json':{'received_at':0,'body':{'expires_in':900}}}
            for path,value in files.items():(root/path).write_text(json.dumps(value),encoding='utf-8')
            before={p.relative_to(root):p.read_bytes() for p in root.rglob('*.json')}
            with patch('android_sync_setup.Setup.session',side_effect=AssertionError('No setup session replay')),\
                 patch('android_sync_setup.Setup.run',side_effect=AssertionError('No setup replay')):
                payload=read_frozen_fixture(root,self.context,'emulator-5562')
            self.assertEqual(payload['vault_id'],'e'*32)
            self.assertEqual(before,{p.relative_to(root):p.read_bytes() for p in root.rglob('*.json')})
            files['sync-control/target.json']['vault_id']='0'*32
            (root/'sync-control/target.json').write_text(json.dumps(files['sync-control/target.json']),encoding='utf-8')
            with self.assertRaisesRegex(HandoffError,'ORIGINAL_FIXTURE_CHANGED'):read_frozen_fixture(root,self.context,'emulator-5562')

    def test_unknown_actual_edit_keeps_original_intent_and_restores_ime_without_replay(self):
        with tempfile.TemporaryDirectory() as folder,patch('android_product_pair_android.hosted_scope'):
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(serial='emulator-5562')
            probe.phase.side_effect=RuntimeError('Synthetic lost driver completion')
            with self.assertRaises(RuntimeError):original_pair_ui_phase(probe,self.context,'pair-note-return')
            intent=(probe.output/'pair-note-return-original-intent.json').read_bytes()
            with self.assertRaises(FileExistsError):original_pair_ui_phase(probe,self.context,'pair-note-return')
            self.assertEqual((probe.output/'pair-note-return-original-intent.json').read_bytes(),intent)
            probe.enable_test_ime.assert_called_once();probe.phase.assert_called_once_with('pair-note-return',keep_running=False)
            probe.restore_test_ime.assert_called_once()


if __name__=='__main__':unittest.main()
