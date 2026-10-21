"""Synthetic metadata and failed-tail boundaries; no local network or device."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import android_release_auth_certificate as runtime

OPERATION = '11111111-2222-4333-8444-555555555555'


class ReleaseCertificate(unittest.TestCase):
    def sequence(self):
        provider = dict(users=[dict(username='owned-a', devices=[], session_counts=[]),
                               dict(username='owned-b', devices=[], session_counts=[])],
                        requests=dict(counts={}, events=[]))
        intent = dict(operation_id=OPERATION, endpoint=runtime.ENDPOINT+'/', account='owned-a', device_name='owned',
                      kind='login', phase='login_pending', device_id=None, generation=1,
                      server_logout_confirmed=0, previous_operation=None)
        native = dict(intents=[intent], selection=dict(revision=1, endpoint=runtime.ENDPOINT+'/', account='owned-a',
                      operation_id=OPERATION, generation=1), integrity='ok', secret_payload_selected=False)
        cold = deepcopy(native); cold['intents'][0]['phase'] = 'unknown'
        baseline = dict(counts=dict(connections=1, verified_handshakes=0, rejected_handshakes=1, http_bytes_observed=0),
                        tls_reasons=['TLSV1_ALERT_UNKNOWN_CA'], raw_request_recorded=False)
        rejected = deepcopy(baseline); rejected['counts']['connections'] += 1
        rejected['counts']['rejected_handshakes'] += 1; rejected['tls_reasons'].append('TLSV1_ALERT_UNKNOWN_CA')
        origin = dict(certificate_sha256='a'*64, SAN_IPs=['10.0.2.2', '127.0.0.1'], fresh_self_signed=True,
                      runner_default_trust_rejected=True, trust_store_modified=False, baseline=baseline)
        return [deepcopy(provider) for _ in range(3)], [native, cold], [{}, {}], origin, [rejected, deepcopy(rejected)]

    def test_original_TLS_alert_zero_HTTP_and_same_ID_cold_unknown(self):
        result = runtime.validate_certificate(*self.sequence())
        self.assertEqual(result['original_login_id'], OPERATION)
        self.assertTrue(result['no_ciphertext_created_or_activated'])

    def test_wrong_certificate_boundary_or_successful_HTTP_is_rejected(self):
        for mode in ('trust-change', 'SAN', 'extra-TLS', 'successful-handshake', 'HTTP-byte', 'other-alert', 'raw-request', 'cold-retry'):
            with self.subTest(mode=mode):
                provider, native, cipher, origin, tls = self.sequence()
                if mode == 'trust-change': origin['trust_store_modified'] = True
                if mode == 'SAN': origin['SAN_IPs'] = ['127.0.0.1']
                if mode == 'extra-TLS': tls[0]['counts']['connections'] += 1
                if mode == 'successful-handshake': tls[0]['counts']['verified_handshakes'] += 1
                if mode == 'HTTP-byte': tls[0]['counts']['http_bytes_observed'] += 1
                if mode == 'other-alert': tls[0]['tls_reasons'][-1] = 'PEER_IO_FAILURE'
                if mode == 'raw-request': tls[0]['raw_request_recorded'] = True
                if mode == 'cold-retry': tls[1]['counts']['connections'] += 1
                with self.assertRaises(RuntimeError): runtime.validate_certificate(provider, native, cipher, origin, tls)

    def test_identity_session_or_cipher_activation_and_new_ID_are_rejected(self):
        for mode in ('request', 'device', 'session', 'cipher', 'account', 'endpoint', 'active', 'new-cold-ID', 'selection', 'generation'):
            with self.subTest(mode=mode):
                provider, native, cipher, origin, tls = self.sequence()
                if mode == 'request': provider[1]['requests']['counts']['login200'] = 1
                if mode == 'device': provider[1]['users'][0]['devices'] = [dict(id='unexpected')]
                if mode == 'session': provider[1]['users'][0]['session_counts'] = [dict(device_id='unexpected', sessions=1)]
                if mode == 'cipher': cipher[0]['unexpected.bin'] = dict(bytes=256, sha256='b'*64)
                if mode == 'account': native[0]['intents'][0]['account'] = 'foreign'
                if mode == 'endpoint': native[0]['intents'][0]['endpoint'] = 'https://foreign.invalid'
                if mode == 'active': native[1]['intents'][0]['phase'] = 'active'
                if mode == 'new-cold-ID': native[1]['intents'][0]['operation_id'] = 'other'
                if mode == 'selection': native[1]['selection']['revision'] += 1
                if mode == 'generation': native[0]['intents'][0]['generation'] += 1
                with self.assertRaises(RuntimeError): runtime.validate_certificate(provider, native, cipher, origin, tls)

    def owned_configuration(self):
        return dict(endpoint='https://owned.trycloudflare.com',accounts=[
            dict(username='android-owned-'+label+'-'+'a'*12,password='B'*48,device_name='Owned Android '+label)
            for label in ('a','b')])

    def input_subject(self):
        from types import SimpleNamespace
        subject=object.__new__(runtime.ReleaseAuthCertificate);subject.certificate=self.sequence()[3]
        subject.args=SimpleNamespace(serial='emulator-5562');subject.shell=Mock();subject.device=Mock()
        return subject

    def test_actual_certificate_stdin_path_preserves_owned_identity_gate(self):
        # Mock only the device boundary, not the inherited admission method.
        # This catches a certificate config accidentally fed to the normal
        # cloudflared-only gate before any actual hosted GUI can run.
        subject=self.input_subject();original=self.owned_configuration()
        with patch('android_auth_runtime.require_hosted_emulator'),patch.object(runtime,'require_hosted_emulator',create=True):
            subject.stage_owned_input(original)
        subject.device.assert_called_once();args=subject.device.call_args
        actual=json.loads(args.kwargs['input'])
        self.assertEqual(actual,dict(original,endpoint=runtime.ENDPOINT,scope='owned-untrusted-certificate',certificate_sha256='a'*64))
        self.assertEqual(args.args[:2],('shell','-T'));self.assertNotIn('B'*48,str(args.args))
        self.assertIn('test ! -e no_backup/owned-auth.json',args.args[2])
        self.assertEqual(original['endpoint'],'https://owned.trycloudflare.com')

    def test_certificate_stdin_rejects_unowned_accounts_origin_or_fingerprint_before_device(self):
        for mode in ('foreign-origin','extra-input','foreign-user','extra-account-field','short-password','bad-device-name','fingerprint','not-self-signed','SAN','trust-change'):
            with self.subTest(mode=mode):
                subject=self.input_subject();original=self.owned_configuration()
                if mode=='foreign-origin':original['endpoint']='https://foreign.invalid'
                if mode=='extra-input':original['arbitrary']='forbidden'
                if mode=='foreign-user':original['accounts'][0]['username']='user-account'
                if mode=='extra-account-field':original['accounts'][0]['token']='forbidden'
                if mode=='short-password':original['accounts'][0]['password']='short'
                if mode=='bad-device-name':original['accounts'][0]['device_name']='user phone'
                if mode=='fingerprint':subject.certificate['certificate_sha256']='not-a-fingerprint'
                if mode=='not-self-signed':subject.certificate['fresh_self_signed']=False
                if mode=='SAN':subject.certificate['SAN_IPs']=['foreign.invalid']
                if mode=='trust-change':subject.certificate['trust_store_modified']=True
                with patch('android_auth_runtime.require_hosted_emulator'),patch.object(runtime,'require_hosted_emulator',create=True),self.assertRaises(RuntimeError):
                    subject.stage_owned_input(original)
                subject.shell.assert_not_called();subject.device.assert_not_called()

    def test_normal_stdin_still_refuses_certificate_scope_and_local_or_foreign_endpoint(self):
        subject=self.input_subject()
        for mode in ('certificate','local','foreign','extra'):
            original=self.owned_configuration()
            if mode=='certificate':original.update(endpoint=runtime.ENDPOINT,scope='owned-untrusted-certificate',certificate_sha256='a'*64)
            if mode=='local':original['endpoint']=runtime.ENDPOINT
            if mode=='foreign':original['endpoint']='https://foreign.invalid'
            if mode=='extra':original['scope']='forbidden'
            with self.subTest(mode=mode),patch('android_auth_runtime.require_hosted_emulator'),self.assertRaises(RuntimeError):
                runtime.ReleaseAuth.stage_owned_input(subject,original)
            subject.shell.assert_not_called();subject.device.assert_not_called()
        original=self.owned_configuration()
        with patch('android_auth_runtime.require_hosted_emulator'):
            runtime.ReleaseAuth.stage_owned_input(subject,original)
        subject.device.assert_called_once()
        self.assertEqual(json.loads(subject.device.call_args.kwargs['input']),original)

    def test_unknown_original_GUI_retains_TLS_outcome_and_closes_without_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            subject = object.__new__(runtime.ReleaseAuthCertificate); subject.output = Path(folder); subject.owner = Path(folder)
            subject.prepare = Mock(); subject.observe = Mock(return_value=self.sequence()[0][0])
            subject.phase = Mock(side_effect=TimeoutError('private GUI result unknown'))
            server = Mock(); server.prepare.return_value = self.sequence()[3]; server.snapshot.return_value = self.sequence()[4][0]
            with patch.object(runtime, 'UntrustedOrigin', return_value=server), self.assertRaises(TimeoutError): subject.run()
            subject.phase.assert_called_once_with('invalid-certificate', 'certificate-rejected')
            server.close.assert_called_once()
            self.assertEqual(json.loads((subject.output/'certificate-rejected-TLS.json').read_bytes()), server.snapshot.return_value)
            self.assertTrue(json.loads((subject.output/'certificate-server-cleanup.json').read_bytes())['stopped'])
            self.assertFalse((subject.output/'auth-certificate-runtime-proof.json').exists())

    def test_certificate_entry_refuses_all_other_mutations_before_stop_or_input(self):
        subject = object.__new__(runtime.ReleaseAuthCertificate); subject.stop = Mock()
        self.assertEqual(subject.allowed_phases, runtime.PHASES)
        for phase in ('login-a', 'current', 'refresh', 'logout', 'login-b-held', 'current-unusable', '../invalid-certificate'):
            with self.subTest(phase=phase), self.assertRaises(RuntimeError): subject.phase(phase, 'forbidden')
        subject.stop.assert_not_called()


if __name__ == '__main__': unittest.main()
