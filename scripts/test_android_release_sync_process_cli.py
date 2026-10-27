"""Real dedicated CLI, auth constructor and host; controlled device and binding."""
from contextlib import chdir,redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import android_release_auth as auth
import android_release_sync_process as runtime
import android_release_sync_process_lifecycle as cli
from android_release_upgrade import ReleaseUpgrade
from test_android_sync_process_host import Harness,host


class ProcessCLI(unittest.TestCase):
    def exercise(self,root,phase):
        h=Harness(root,phase);original=h.subject
        (h.owner/'owner-state.json').write_text(json.dumps(dict(result='ready')),'utf-8')
        inputs=root/'inputs';inputs.mkdir();(inputs/'runtime-inputs.json').write_bytes(b'{}')
        calls=[];local_guards=[]
        def factory(*args):
            subject=runtime.ReleaseSyncProcess(*args)
            calls.append(dict(output=subject.output,tools=subject.tools))
            # Keep the real constructor's path contract and the actual host's
            # command recorder, while device/GUI observations use the fixture.
            for name,value in vars(original).items():
                if name not in ('output','tools','original_command'):setattr(subject,name,value)
            subject.original_command=ReleaseUpgrade.original_command.__get__(subject)
            h.subject=subject
            def run():
                try:h.execute()
                except RuntimeError as error:
                    self.assertEqual(str(error),'SYNC_PROCESS_PRIVATE_OUTPUT_ALIAS')
                    local_guards.append('SYNC_PROCESS_PRIVATE_OUTPUT_ALIAS')
                    raise
                return dict(kind='controlled-CLI-constructor-host',passed=True,abi='x86_64',physical_phone_verified=False,published=False)
            subject.run=run
            return subject
        args=['--inputs',str(inputs),'--sdk',str(root/'sdk'),'--java',str(root/'java'),
              '--output','.build/android-release-runtime/evidence']
        env=dict(GITHUB_ACTIONS='true',RUNNER_ENVIRONMENT='github-hosted',GITHUB_RUN_ID='17',GITHUB_RUN_ATTEMPT='1')
        with chdir(root),patch.dict(os.environ,env),patch('android_runtime_probe.sys.platform','linux'), \
             patch.object(auth,'require_hosted'),patch.object(auth,'owned_root',return_value=h.owner), \
             patch.object(auth,'bind_pair',return_value={}),redirect_stdout(io.StringIO()) as stream:
            result=cli.main(factory,args)
        self.assertEqual(result,0,stream.getvalue()+repr(local_guards))
        self.assertEqual(len(calls),1);self.assertTrue(all(v.is_absolute() and v.resolve()==v for v in calls[0].values()))
        self.assertEqual(h.subject.stop.call_count,1)
        self.assertEqual(h.events,['launch','tap','held','input-finish','expect-loss','force-stop','release-CAS','released-signal']
            if phase==host.LOSS else ['launch','tap','input-finish'])
        return calls

    def test_workflow_relative_output_real_CLI_constructor_and_original_loss_host(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder),host.LOSS)

    def test_workflow_relative_output_real_CLI_constructor_and_explicit_read_host(self):
        with tempfile.TemporaryDirectory() as folder:self.exercise(Path(folder),host.RESUME)

    def test_parent_alias_is_rejected_before_constructor_without_resolving_it_into_an_allowed_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'other').mkdir();factory=Mock()
            args=['--inputs',str(root/'inputs'),'--sdk',str(root/'sdk'),'--java',str(root/'java'),
                  '--output',str(root/'other'/'..'/'evidence')]
            with redirect_stdout(io.StringIO()):result=cli.main(factory,args)
            self.assertEqual(result,1);factory.assert_not_called();self.assertFalse((root/'evidence').exists())


if __name__=='__main__':unittest.main()
