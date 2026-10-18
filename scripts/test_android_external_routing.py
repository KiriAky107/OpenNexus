from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import json,subprocess,tempfile,unittest
import android_external_routing as subject
from android_external_contract import HOST,DRIVER


def state(stage='before',selected=False):
    return dict(schema=1,scope='external-links',stage=stage,package=DRIVER,user_id=0,
        link_handling_allowed=True,host_states={HOST:1 if selected else 0},internet_verification_claimed=False)


class OwnedRouting(unittest.TestCase):
    def test_only_exact_own_unverified_domain_state_is_admitted(self):
        self.assertFalse(subject.selected(state(),'before'));self.assertTrue(subject.selected(state(selected=True),'before'))
        for key,value in [('schema',True),('scope','other'),('stage','other'),('package','other.app'),('user_id',True),('user_id',10),
            ('link_handling_allowed',False),('host_states',{}),('host_states',{HOST:True}),('host_states',{HOST:2}),
            ('host_states',{HOST:1,'other.invalid':0}),('internet_verification_claimed',True)]:
            bad=dict(state(),**{key:value})
            with self.subTest(key=key,value=value),self.assertRaises(RuntimeError):subject.selected(bad,'before')
        with self.assertRaises(RuntimeError):subject.selected(dict(state(),extra=True),'before')
        with self.assertRaises(RuntimeError):subject.selected(state(),'foreign')

    def test_unsupported_command_or_scope_never_changes_host_association(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'):
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(serial='emulator-5562',scope='other')
            with self.assertRaisesRegex(RuntimeError,'ROUTING_SCOPE'):subject.setup(probe)
            probe.shell.assert_not_called();probe.args.scope='external-links'
            probe.shell.return_value=subprocess.CompletedProcess([],0,b'no supported interface',b'')
            with self.assertRaisesRegex(RuntimeError,'SUPPORTED_ROUTING_COMMANDS_REQUIRED'):subject.setup(probe)
            self.assertEqual(probe.shell.call_count,1);self.assertFalse((probe.output/'external-routing-intent.json').exists())

    def test_one_package_host_selection_follows_a_durable_typed_original(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'),patch.object(subject,'observe',side_effect=[state(),state('after',True)]):
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(serial='emulator-5562',scope='external-links')
            calls=[]
            def shell(*args,**kwargs):
                calls.append(args)
                if args==('cmd','package','help'):raw=b'get-app-links set-app-links-user-selection'
                elif 'get-app-links' in args:raw=b'Owned raw display without a User 0 line; not used for admission'
                elif 'set-app-links-user-selection' in args:
                    intent=json.loads((probe.output/'external-routing-intent.json').read_bytes());self.assertEqual(intent['command'],list(args))
                    self.assertEqual(intent['before'],state());raw=b''
                else:raw=(DRIVER+'/.ExternalIntentReceiver\n').encode()
                return subprocess.CompletedProcess([],0,raw,b'')
            probe.shell.side_effect=shell;subject.setup(probe)
            setters=[c for c in calls if 'set-app-links-user-selection' in c];self.assertEqual(len(setters),1)
            self.assertEqual(setters[0],('cmd','package','set-app-links-user-selection','--user','0','--package',DRIVER,'true',HOST))
            self.assertIn(b'not used for admission',(probe.output/'external-domain-before-display.txt').read_bytes())
            resolver=next(c for c in calls if 'resolve-activity' in c)
            self.assertEqual(resolver[resolver.index('--query-flags')+1],'65536')
            self.assertEqual(resolver[resolver.index('--user')+1],'0')
            self.assertEqual((probe.output/'external-resolver-original.txt').read_bytes(),(DRIVER+'/.ExternalIntentReceiver\n').encode())

    def test_unknown_setter_cleanup_reads_before_clearing_only_its_own_host(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'),patch.object(subject,'observe',side_effect=[state('cleanup-before',True),state('cleanup-after')]) as read:
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(serial='emulator-5562',scope='external-links')
            subject.cleanup(probe);probe.shell.assert_not_called();read.assert_not_called()
            (probe.output/'external-routing-intent.json').write_text('{}')
            def shell(*args):
                self.assertEqual(read.call_count,1);return subprocess.CompletedProcess([],0,b'',b'')
            probe.shell.side_effect=shell;result=subject.cleanup(probe);self.assertTrue(result['own_selection_cleared'])
            probe.shell.assert_called_once_with('cmd','package','set-app-links-user-selection','--user','0','--package',DRIVER,'false',HOST)
            self.assertEqual([c.args[1] for c in read.call_args_list],['cleanup-before','cleanup-after'])

    def test_native_structured_read_is_closed_and_persists_original_output(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'):
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(serial='emulator-5562',scope='external-links')
            with self.assertRaises(RuntimeError):subject.observe(probe,'arbitrary')
            probe.shell.assert_not_called()
            probe.shell.side_effect=[subprocess.CompletedProcess([],0,b'OPENNEXUS_RUNTIME_PHASE_OK external-domain-before\n',b''),
                subprocess.CompletedProcess([],0,json.dumps(state()).encode(),b'')]
            self.assertEqual(subject.observe(probe,'before'),state())
            self.assertEqual(json.loads((probe.output/'external-domain-before.json').read_bytes()),state())
            probe.phase.assert_not_called();probe.device.assert_not_called()
            probe.shell.assert_any_call('am','instrument','-w','-e','phase','external-domain-before',DRIVER+'/.RuntimeProbe',timeout=60)

    def test_rejected_native_state_raw_bytes_are_retained_before_admission(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'):
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(serial='emulator-5562',scope='external-links')
            value=state('after',True);value['host_states'][HOST]=subject.DOMAIN_VERIFIED;raw=json.dumps(value).encode()
            probe.shell.side_effect=[subprocess.CompletedProcess([],0,b'OPENNEXUS_RUNTIME_PHASE_OK external-domain-after\n',b''),subprocess.CompletedProcess([],0,raw,b'')]
            with self.assertRaisesRegex(RuntimeError,'SINGLE_UNVERIFIED_HOST'):subject.observe(probe,'after')
            self.assertEqual((probe.output/'external-domain-after-raw.json').read_bytes(),raw)
            self.assertFalse((probe.output/'external-domain-after.json').exists());probe.device.assert_not_called();probe.phase.assert_not_called()

    def test_unexpected_resolver_is_retained_and_refused_without_launch_or_product_action(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'),patch.object(subject,'observe',side_effect=[state(),state('after',True)]):
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(serial='emulator-5562',scope='external-links')
            raw=b'priority=0 preferredOrder=0 match=0x208000\nandroid/com.android.internal.app.ResolverActivity\n'
            probe.shell.side_effect=[subprocess.CompletedProcess([],0,b'get-app-links set-app-links-user-selection',b''),
                subprocess.CompletedProcess([],0,b'User 0 only\n',b''),subprocess.CompletedProcess([],0,b'',b''),subprocess.CompletedProcess([],0,raw,b'')]
            with self.assertRaisesRegex(RuntimeError,'RESOLVER_NOT_OWN_RECEIVER'):subject.setup(probe)
            self.assertEqual((probe.output/'external-resolver-original.txt').read_bytes(),raw)
            probe.phase.assert_not_called();probe.device.assert_not_called()

if __name__=='__main__':unittest.main()
