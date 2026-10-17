"""Pure orchestration boundaries; no Docker, emulator, tunnel or service starts."""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
import android_auth_hosted as module


class HostedOriginBoundaries(unittest.TestCase):
    def test_fixture_selection_is_only_the_two_trusted_scripts_and_defaults_to_original_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.assertEqual(module.HostedOrigin(root).fixture,'auth')
            self.assertEqual(module.HostedOrigin(root,fixture='sync').fixture,'sync')
            for kind in ('auth','sync'):
                selected=module.HostedOrigin(root,fixture=kind).fixture_script()
                self.assertEqual(selected,Path(module.__file__).with_name('android_'+kind+'_fixture.py').resolve())
            for value in ('../outside','https://production.example','android_sync_fixture.py',''):
                with patch.object(module,'read_json') as read,patch.object(module.subprocess,'Popen') as start:
                    with self.assertRaisesRegex(RuntimeError,'FIXTURE_INVALID'):module.HostedOrigin(root,fixture=value)
                    read.assert_not_called();start.assert_not_called()

    def test_sync_prepare_repeat_cannot_stop_or_replace_an_existing_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            origin=Mock()
            with patch.object(module,'owned_root',return_value=Path(directory)),patch.object(module,'HostedOrigin',return_value=origin) as factory,patch.object(module.sys,'argv',['owned-test','prepare','--fixture','sync']):
                with self.assertRaisesRegex(RuntimeError,'ALREADY_PREPARED'):module.main()
                factory.assert_called_once_with(Path(directory),fixture='sync')
                origin.prepare.assert_not_called();origin.stop.assert_not_called()

    def test_only_static_owned_failure_codes_can_reach_the_job_log(self):
        self.assertEqual(module.public_failure(RuntimeError('OWNED_AUTH_ORIGIN_NOT_READY')),'OWNED_AUTH_ORIGIN_NOT_READY')
        for message in ('postgresql+psycopg://private:password@production/database','private-token','OWNED_AUTH_STAGE_FAILED private-password'):
            self.assertEqual(module.public_failure(RuntimeError(message)),'OWNED_AUTH_HOST_FAILED_RuntimeError')
    def test_readiness_calls_the_real_v1_handshake_and_retains_only_the_protocol_contract(self):
        contract={'protocol':1,'chunk_size':1048576,'max_object_size':104857600,'encryption':'transport-only'}
        endpoint='https://owned-unit-only.trycloudflare.com'
        response=io.BytesIO(__import__('json').dumps({**contract,'unrequested':'not retained'}).encode())
        response.status=200;response.geturl=lambda:endpoint+'/sync/v1/handshake?protocol=1'
        opener=Mock();opener.open.return_value=response
        with patch.object(module,'require_hosted'),patch.object(module.urllib.request,'build_opener',return_value=opener):
            self.assertEqual(module.origin_handshake(endpoint),contract)
        self.assertEqual(opener.open.call_args.args,(endpoint+'/sync/v1/handshake?protocol=1',))

    def test_readiness_refuses_html_oversized_wrong_protocol_or_redirected_content(self):
        endpoint='http://127.0.0.1:19073';url=endpoint+'/sync/v1/handshake?protocol=1'
        candidates=[(b'<html>not the service</html>',url),(b'x'*65537,url),(b'{"protocol":2}',url),
            (b'{}','https://another.example/redirected')]
        for body,final_url in candidates:
            response=io.BytesIO(body);response.status=200;response.geturl=lambda value=final_url:value
            opener=Mock();opener.open.return_value=response
            with patch.object(module,'require_hosted'),patch.object(module.urllib.request,'build_opener',return_value=opener):
                with self.assertRaises((RuntimeError,ValueError)):module.origin_handshake(endpoint)

    def test_readiness_refuses_a_developer_host_or_unowned_endpoint_before_network(self):
        with patch.object(module,'require_hosted',side_effect=RuntimeError('owned host required')),patch.object(module.urllib.request,'build_opener') as network:
            with self.assertRaises(RuntimeError):module.origin_handshake('http://127.0.0.1:19073')
            network.assert_not_called()
        with patch.object(module,'require_hosted'),patch.object(module.urllib.request,'build_opener') as network:
            with self.assertRaises(RuntimeError):module.origin_handshake('https://production.example')
            network.assert_not_called()

    def test_child_environment_has_no_ambient_cloud_or_repository_credentials(self):
        source={'PATH':'owned-path','GITHUB_TOKEN':'private-repository-token','ACTIONS_RUNTIME_TOKEN':'private-action-token',
            'AWS_SESSION_TOKEN':'private-cloud-token','SYNC_DATABASE_URL':'private-production-pointer','HOME':'user-home',
            'RUNNER_TRACKING_ID':'owned-runner-marker','GITHUB_EVENT_NAME':'workflow_dispatch'}
        with patch.dict(os.environ,source,clear=True):
            value=module.minimal_environment(Path('/owned'))
            self.assertEqual(value['HOME'],str(Path('/owned')/'home'))
            self.assertEqual(value['RUNNER_TRACKING_ID'],'owned-runner-marker')
            self.assertEqual(value['AWS_EC2_METADATA_DISABLED'],'true')
            for name in ('GITHUB_TOKEN','ACTIONS_RUNTIME_TOKEN','AWS_SESSION_TOKEN','SYNC_DATABASE_URL'):
                self.assertNotIn(name,value)
            self.assertEqual(dict(os.environ),source)

    def test_process_identity_never_matches_pid_reuse_or_a_different_original_command(self):
        saved={'pid':123,'start_ticks':456,'command_sha256':'a'*64,'argv':['/owned/cloudflared','--logfile','/owned/private.log']}
        self.assertTrue(module.same_process(saved,dict(saved)))
        self.assertFalse(module.same_process(saved,None))
        for key,value in [('pid',124),('start_ticks',457),('command_sha256','b'*64),('argv',['/other/cloudflared'])]:
            self.assertFalse(module.same_process(saved,{**saved,key:value}))

    def test_asset_download_refuses_a_developer_host_before_network_or_file_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(module,'require_hosted',side_effect=RuntimeError('OWNED_AUTH_HOST_REQUIRED')),patch.object(module.urllib.request,'urlopen') as network:
                with self.assertRaisesRegex(RuntimeError,'HOST_REQUIRED'):module.download_cloudflared(root)
                network.assert_not_called();self.assertEqual(list(root.iterdir()),[])

    def response(self,body):
        result=io.BytesIO(body);result.geturl=lambda:'https://release-assets.githubusercontent.com/owned-fixed-test'
        return result

    def test_only_exact_fixed_asset_bytes_become_executable_and_an_existing_asset_is_never_replaced(self):
        data=b'unit-only-official-tool-bytes';digest=hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(module,'require_hosted'),patch.object(module,'CLOUDFLARED_BYTES',len(data)),patch.object(module,'CLOUDFLARED_SHA256',digest),patch.object(module.urllib.request,'urlopen',return_value=self.response(data)) as network:
                proof=module.download_cloudflared(root)
                self.assertEqual(proof['sha256'],digest);self.assertEqual((root/'cloudflared').read_bytes(),data)
                with self.assertRaisesRegex(RuntimeError,'ASSET_EXISTS'):module.download_cloudflared(root)
                self.assertEqual(network.call_count,1)

    def test_wrong_digest_is_preserved_as_unexecuted_input_with_no_final_binary(self):
        data=b'unit-only-changed-tool'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(module,'require_hosted'),patch.object(module,'CLOUDFLARED_BYTES',len(data)),patch.object(module,'CLOUDFLARED_SHA256','0'*64),patch.object(module.urllib.request,'urlopen',return_value=self.response(data)):
                with self.assertRaisesRegex(RuntimeError,'HASH_INVALID'):module.download_cloudflared(root)
            self.assertFalse((root/'cloudflared').exists())
            self.assertEqual((root/'cloudflared.download').read_bytes(),data)

    def test_docker_run_captures_only_its_id_without_mixing_pull_logs_or_secret_env_into_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);origin=module.HostedOrigin(root)
            result=subprocess.CompletedProcess([],0,b'owned-container-id\n',None)
            environment={'POSTGRES_PASSWORD':'unit-only-private-password'}
            with patch.object(module.subprocess,'run',return_value=result) as process:
                output=origin.command(['docker','run','--detach','--env','POSTGRES_PASSWORD','postgres:17.6'],'unit-docker-run',env=environment,capture=True)
            self.assertEqual(output,b'owned-container-id\n')
            self.assertNotIn('unit-only-private-password',str(process.call_args.args[0]))
            self.assertEqual(process.call_args.kwargs['env'],environment)
            self.assertEqual(process.call_args.kwargs['stdout'],subprocess.PIPE)
            self.assertNotEqual(process.call_args.kwargs['stderr'],subprocess.STDOUT)

    def test_accidental_prepare_repeat_cannot_stop_or_replace_an_existing_owned_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);origin=Mock()
            with patch.object(module,'owned_root',return_value=root),patch.object(module,'HostedOrigin',return_value=origin),patch.object(module.sys,'argv',['owned-test','prepare']):
                with self.assertRaisesRegex(RuntimeError,'ALREADY_PREPARED'):module.main()
                origin.prepare.assert_not_called();origin.stop.assert_not_called()

    def test_container_image_observation_selects_only_the_resolved_id_and_never_secret_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            origin=module.HostedOrigin(Path(directory));origin.state={'owner':'unit-owner','containers':[]}
            container='a'*64;image='sha256:'+'b'*64
            with patch.object(origin,'save'),patch.object(origin,'command',side_effect=[container.encode()+b'\n',image.encode()+b'\n']) as command:
                origin.start_container('owned-pg','postgres:17.6',[],{'POSTGRES_PASSWORD':'unit-only-private-password'})
            self.assertEqual(origin.state['containers'][0]['image_id'],image)
            self.assertEqual(command.call_args_list[1].args[0],['docker','inspect','--format','{{.Image}}',container])
            self.assertNotIn('unit-only-private-password',str(origin.state))


if __name__=='__main__':unittest.main()
