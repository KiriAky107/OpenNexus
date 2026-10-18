from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import json,subprocess,tempfile,unittest
import android_external_routing as subject
from android_external_contract import HOST,DRIVER


def output(enabled=False):
    return (DRIVER+':\n  ID: owned\n  Signatures: [own]\n  Domain verification state:\n'
        '  User 0:\n    Verification link handling allowed: true\n    Selection state:\n'
        +('      Enabled:\n        '+HOST+'\n' if enabled else '')
        +'      Disabled:\n        '+HOST+'\n')


class OwnedRouting(unittest.TestCase):
    def test_only_exact_reserved_host_and_user_selection_is_admitted(self):
        self.assertFalse(subject.selected(output()));self.assertTrue(subject.selected(output(True)))
        for text in (output(True).replace(DRIVER,'other.application'),output(True).replace(HOST,'other.invalid',1),
            output(True).replace('allowed: true','allowed: false'),output(True)+'  User 10:\n',
            output(True).replace('User 0:','User 1:'),'x'*32769,output(True).replace('        '+HOST+'\n','        '+HOST+'\n        extra.invalid\n',1)):
            with self.subTest(text=text[:100]),self.assertRaises(RuntimeError):subject.selected(text)

    def test_unsupported_command_or_scope_never_changes_host_association(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'):
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(serial='emulator-5562',scope='other')
            with self.assertRaisesRegex(RuntimeError,'ROUTING_SCOPE'):subject.setup(probe)
            probe.shell.assert_not_called();probe.args.scope='external-links'
            probe.shell.return_value=subprocess.CompletedProcess([],0,b'no supported interface',b'')
            with self.assertRaisesRegex(RuntimeError,'SUPPORTED_ROUTING_COMMANDS_REQUIRED'):subject.setup(probe)
            self.assertEqual(probe.shell.call_count,1);self.assertFalse((probe.output/'external-routing-intent.json').exists())

    def test_original_intent_is_durable_before_one_package_host_selection(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'):
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(serial='emulator-5562',scope='external-links')
            calls=[]
            def shell(*args,**kwargs):
                calls.append(args)
                if args==('cmd','package','help'):return subprocess.CompletedProcess([],0,b'get-app-links set-app-links-user-selection',b'')
                if 'set-app-links-user-selection' in args:
                    intent=json.loads((probe.output/'external-routing-intent.json').read_bytes())
                    self.assertEqual(intent['command'],list(args));self.assertFalse(intent['global_default_browser_change'])
                    return subprocess.CompletedProcess([],0,b'',b'')
                if 'get-app-links' in args:return subprocess.CompletedProcess([],0,output(len(calls)>3).encode(),b'')
                return subprocess.CompletedProcess([],0,(DRIVER+'/.ExternalIntentReceiver\n').encode(),b'')
            probe.shell.side_effect=shell;subject.setup(probe)
            setters=[c for c in calls if 'set-app-links-user-selection' in c];self.assertEqual(len(setters),1)
            self.assertEqual(setters[0],('cmd','package','set-app-links-user-selection','--user','0','--package',DRIVER,'true',HOST))

    def test_unknown_setter_cleanup_reads_before_clearing_only_its_own_host(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject,'require_hosted_emulator'):
            probe=Mock();probe.output=Path(folder);probe.args=SimpleNamespace(serial='emulator-5562',scope='external-links')
            subject.cleanup(probe);probe.shell.assert_not_called()
            (probe.output/'external-routing-intent.json').write_text('{}')
            probe.shell.side_effect=[subprocess.CompletedProcess([],0,output(True).encode(),b''),subprocess.CompletedProcess([],0,b'',b''),subprocess.CompletedProcess([],0,output().encode(),b'')]
            result=subject.cleanup(probe);self.assertTrue(result['own_selection_cleared'])
            self.assertEqual(probe.shell.call_args_list[0].args,('cmd','package','get-app-links','--user','0',DRIVER))
            self.assertEqual(probe.shell.call_args_list[1].args,('cmd','package','set-app-links-user-selection','--user','0','--package',DRIVER,'false',HOST))

if __name__=='__main__':unittest.main()
