"""Signed production GUI rejects a fresh SAN-valid self-signed TLS origin.

Only stopped nonsensitive auth projections and bounded cipher fingerprints
leave the private observer. No CA, product, trust-store or permission changes.
"""
from copy import deepcopy
import json
import os
import re

from android_auth_runtime import admit_owned_configuration, expect_requests, provider_account
from android_certificate_probe import ENDPOINT, UntrustedOrigin, validate_rejected_handshake
from android_release_auth import ReleaseAuth, main
from android_release_observation import APP
from android_runtime_probe import DRIVER, require, require_hosted_emulator
from release_plan import atomic_json, digest

PHASES = ('invalid-certificate', 'certificate-cold-unusable')


def validate_certificate(provider, native, cipher, origin, tls):
    require(len(provider) == 3 and len(native) == len(cipher) == len(tls) == 2,
            'RELEASE_CERTIFICATE_OBSERVATION_COUNT')
    require(re.fullmatch(r'[0-9a-f]{64}', origin['certificate_sha256'])
            and origin['SAN_IPs'] == ['10.0.2.2', '127.0.0.1']
            and origin['fresh_self_signed'] and origin['runner_default_trust_rejected']
            and origin['trust_store_modified'] is False, 'RELEASE_CERTIFICATE_ORIGIN_SCOPE')
    validate_rejected_handshake(origin['baseline'], tls[0])
    require(tls[1] == tls[0], 'RELEASE_CERTIFICATE_COLD_RETRIED_TLS')
    for value in provider:
        expect_requests(value, 0, 0, 0)
        require(all(not v['devices'] and not v['session_counts'] for v in value['users']),
                'RELEASE_CERTIFICATE_PROVIDER_IDENTITY_CREATED')
    require(provider[0] == provider[1] == provider[2] and cipher == [{}, {}],
            'RELEASE_CERTIFICATE_PROVIDER_OR_CIPHER_CHANGED')
    first = native[0]
    require(first['integrity'] == 'ok' and first['secret_payload_selected'] is False
            and len(first['intents']) == 1 and first['selection'] is not None,
            'RELEASE_CERTIFICATE_ORIGINAL_INTENT_MISSING')
    intent = first['intents'][0]; selected = first['selection']; operation = intent['operation_id']
    require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', operation)
            and intent['endpoint'] == ENDPOINT + '/' and intent['account'] == provider_account(provider[0], 0)['username']
            and intent['kind'] == 'login' and intent['phase'] == 'login_pending' and intent['device_id'] is None
            and intent['generation'] == 1 and intent['previous_operation'] is None
            and intent['server_logout_confirmed'] == 0,
            'RELEASE_CERTIFICATE_ACTIVATED_OR_REPLACED_ORIGINAL')
    require(selected == dict(revision=1, endpoint=intent['endpoint'], account=intent['account'], operation_id=operation, generation=1),
            'RELEASE_CERTIFICATE_SELECTION_CHANGED')
    expected = deepcopy(first); expected['intents'][0]['phase'] = 'unknown'
    require(native[1] == expected, 'RELEASE_CERTIFICATE_COLD_ORIGINAL_CHANGED')
    return dict(original_login_id=operation, original_pending_to_unknown_only=True,
                one_Android_certificate_alert_without_HTTP_bytes=True,
                zero_provider_requests_or_devices_or_sessions=True, no_ciphertext_created_or_activated=True)


class ReleaseAuthCertificate(ReleaseAuth):
    allowed_phases = PHASES
    driver_entry = 'ReleaseAuthCertificateRuntimeProbe'
    driver_namespace = 'RELEASE_AUTH_CERTIFICATE'

    def stage_owned_input(self, configuration):
        require_hosted_emulator(self.args.serial)
        # Admit the original owned provider identities before substituting the
        # one closed certificate endpoint. The normal input gate stays strict.
        admit_owned_configuration(configuration)
        require(re.fullmatch(r'[0-9a-f]{64}', self.certificate['certificate_sha256'])
                and self.certificate['SAN_IPs'] == ['10.0.2.2', '127.0.0.1']
                and self.certificate['fresh_self_signed'] is True
                and self.certificate['runner_default_trust_rejected'] is True
                and self.certificate['trust_store_modified'] is False, 'RELEASE_CERTIFICATE_INPUT_ORIGIN')
        value = dict(configuration, endpoint=ENDPOINT, scope='owned-untrusted-certificate',
                     certificate_sha256=self.certificate['certificate_sha256'])
        self.shell('run-as', DRIVER, 'mkdir', '-p', 'no_backup')
        self.device('shell', '-T', 'run-as '+DRIVER+' sh -c '+
                    "'umask 077; test ! -e no_backup/owned-auth.json && cat > no_backup/owned-auth.json'",
                    input=json.dumps(value).encode())

    def run(self):
        server = UntrustedOrigin(self.owner)
        try:
            self.certificate = server.prepare()
            atomic_json(self.output / 'certificate-origin.json', self.certificate)
            self.prepare()
            provider = [self.observe('certificate-before')]; native = []; cipher = []; tls = []
            for phase, label in zip(PHASES, ('certificate-rejected', 'certificate-cold')):
                # Retain the original TLS outcome even if the readonly GUI tail
                # fails. Never interpret a missing GUI acknowledgement as retry.
                try:
                    self.phase(phase, label)
                finally:
                    observed = server.snapshot()
                    atomic_json(self.output / (label + '-TLS.json'), observed)
                tls.append(observed)
                provider.append(self.observe(label)); native.append(self.snapshot(label))
                cipher.append(self.observer.ciphertext_fingerprints())
                atomic_json(self.output / (label + '-ciphertext-fingerprints.json'), cipher[-1])
            checks = validate_certificate(provider, native, cipher, self.certificate, tls)
            require(self.phases[0]['pid'] != self.phases[1]['pid'], 'RELEASE_CERTIFICATE_NEW_COLD_PROCESS')
            result = dict(schema=1, passed=True, kind='android-signed-release-certificate-rejection', package=APP, abi='x86_64',
                tools_commit=os.environ['GITHUB_SHA'], run_id=int(os.environ['GITHUB_RUN_ID']),
                compiled_source=self.manifest['successor']['source_commit'], manifest_sha256=digest(self.inputs / 'runtime-inputs.json'),
                installed_binary=self.identities['successor'], phases=self.phases, original_checks=checks,
                actual_SAN_valid_self_signed_TLS_rejected_without_HTTP_bytes=True,
                private_auth_index_or_ciphertext_or_UI_or_credentials_exported=False,
                platform_trust_or_NSC_or_permissions_changed=False, only_successor_installed=True,
                device_kind='owned-hosted-emulator', physical_phone_verified=False,
                bidirectional_sync_or_worker_verified=False, published=False)
            atomic_json(self.output / 'auth-certificate-runtime-proof.json', result)
            return result
        finally:
            server.close()
            atomic_json(self.output / 'certificate-server-cleanup.json', dict(stopped=True, final=server.snapshot(), owned_listener_only=True))


if __name__ == '__main__': raise SystemExit(main(ReleaseAuthCertificate))
