"""One dropped refresh response on the original signed successor; no mutation retry."""
import json
import os
import re

from android_auth_fixture import read_json
from android_auth_runtime import AuthProbe,active_intent,expect_original_recovery,expect_requests,provider_account
from android_release_auth import ReleaseAuth,main
from android_runtime_probe import require
from release_plan import atomic_json,digest

PHASES=('login-a','refresh-unknown','current-unusable')


def validate_recovery(provider,native,cipher,control,marker):
    require(len(provider)==len(native)==len(cipher)==3,'RELEASE_AUTH_RECOVERY_PHASE_COUNT')
    operation=control['operation_id']
    require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',operation)
            and control['mode']=='drop_refresh' and control['result_revision']==control['expected_revision']+1
            and marker==dict(operation_id=operation,mode='drop_refresh',revision=control['result_revision'],action='refresh',
                             service_status=200,service_response_complete=True,raw_body_recorded=False),
            'RELEASE_AUTH_RECOVERY_ORIGINAL_FAULT')
    for value,counts in zip(provider,((1,0,0),(1,1,0),(1,1,0))):
        expect_requests(value,*counts)
        require(not provider_account(value,1)['devices'],'RELEASE_AUTH_RECOVERY_FOREIGN_DEVICE')
    first=provider_account(provider[0],0)
    require(len(first['devices'])==1 and first['session_counts']==[dict(device_id=first['devices'][0]['id'],sessions=1)],'RELEASE_AUTH_RECOVERY_SINGLE_SESSION')
    require(all(provider_account(v,0)==first for v in provider[1:]),'RELEASE_AUTH_RECOVERY_PROVIDER_IDENTITY_CHANGED')
    event=dict(action='refresh',service_status=200,delivery_status=503,fault_operation=operation)
    require(provider[1]['requests']['events'][-1]==event and provider[2]['requests']==provider[1]['requests'],
            'RELEASE_AUTH_RECOVERY_COMMITTED_AND_DROPPED_ONCE')
    login=active_intent(native[0],'login',first['devices'][0]['id'])
    require(len(native[0]['intents'])==1 and len(native[1]['intents'])==2
            and native[1]['intents'][0]==login and native[1]['selection'] is not None,'RELEASE_AUTH_RECOVERY_ORIGINAL_INTENT_COUNT')
    selected=native[1]['selection']['operation_id']
    pending=native[1]['intents'][1]
    require(pending['operation_id']==selected and pending['kind']=='refresh' and pending['phase']=='refresh_pending'
            and pending['previous_operation']==login['operation_id'] and pending['device_id']==login['device_id'],
            'RELEASE_AUTH_RECOVERY_ORIGINAL_REFRESH_PENDING')
    expect_original_recovery(native[1],native[2])
    require(bool(cipher[0]) and cipher[0]==cipher[1]==cipher[2],'RELEASE_AUTH_RECOVERY_OLD_CIPHER_CHANGED')
    return dict(original_login_id=login['operation_id'],original_refresh_id=selected,fault_control_id=operation,
                original_pending_to_unknown_only=True,no_refresh_repost_or_silent_old_credential_activation=True)


class ReleaseAuthRecovery(ReleaseAuth):
    allowed_phases=PHASES
    driver_entry='ReleaseAuthRecoveryRuntimeProbe'
    driver_namespace='RELEASE_AUTH_RECOVERY'
    control=AuthProbe.control

    def run(self):
        self.prepare()
        provider,native,cipher=[],[],[]
        for phase,label in zip(PHASES,('bootstrap-login','lost-refresh','cold-unknown')):
            if phase=='refresh-unknown':
                operation=self.control('drop_refresh')
                control=read_json(self.owner/'control-receipts'/(operation+'.json'))
            self.phase(phase,label)
            provider.append(self.observe(label));native.append(self.snapshot(label));cipher.append(self.observer.ciphertext_fingerprints())
            atomic_json(self.output/(label+'-ciphertext-fingerprints.json'),cipher[-1])
        marker=read_json(self.owner/'fault-events'/(operation+'.json'))
        atomic_json(self.output/'original-refresh-fault-marker.json',marker)
        checks=validate_recovery(provider,native,cipher,control,marker)
        require(self.phases[1]['pid']!=self.phases[2]['pid'],'RELEASE_AUTH_RECOVERY_NEW_COLD_PROCESS')
        result=dict(schema=1,passed=True,kind='android-signed-release-refresh-recovery',package=self.identities['successor']['package'],abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'],run_id=int(os.environ['GITHUB_RUN_ID']),compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs/'runtime-inputs.json'),installed_binary=self.identities['successor'],phases=self.phases,original_checks=checks,
            required_fresh_bootstrap_login=True,normal_component_not_retested=True,actual_default_trusted_HTTPS_lost_refresh_original_ID_cold_recovery=True,
            old_ciphertext_retained_but_current_shown_unusable=True,private_auth_index_or_ciphertext_or_UI_or_credentials_exported=False,
            device_kind='owned-hosted-emulator',physical_phone_verified=False,background_or_certificate_recovery_verified=False,bidirectional_sync_or_worker_verified=False,published=False)
        atomic_json(self.output/'auth-recovery-runtime-proof.json',result)
        return result


if __name__=='__main__':raise SystemExit(main(ReleaseAuthRecovery))
