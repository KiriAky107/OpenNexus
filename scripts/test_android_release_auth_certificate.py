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

    def test_only_closed_endpoint_is_staged_over_existing_private_stdin_route(self):
        subject = object.__new__(runtime.ReleaseAuthCertificate)
        subject.certificate = self.sequence()[3]
        original = dict(endpoint='https://owned.trycloudflare.com', accounts=[dict(password='never-log-this')])
        with patch.object(runtime.ReleaseAuth, 'stage_owned_input') as stage:
            subject.stage_owned_input(original)
            actual = stage.call_args.args[0]
            self.assertEqual(actual['endpoint'], runtime.ENDPOINT)
            self.assertEqual(actual['scope'], 'owned-untrusted-certificate')
            self.assertEqual(actual['certificate_sha256'], 'a'*64)
            self.assertEqual(actual['accounts'], original['accounts'])
        self.assertEqual(original['endpoint'], 'https://owned.trycloudflare.com')
        subject.certificate['certificate_sha256'] = 'not-a-fingerprint'
        with patch.object(runtime.ReleaseAuth, 'stage_owned_input') as stage, self.assertRaises(RuntimeError):
            subject.stage_owned_input(original)
        stage.assert_not_called()

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
