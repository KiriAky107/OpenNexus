"""Read actual original renderer asset hashes before arming the native pause."""
import json
from pathlib import Path

from android_auth_fixture import atomic_json
from android_desktop_handoff import require
from android_runtime_probe import APP
import android_original_outcome_process as original


def read_original_renderer(probe, context, manifest, value):
    original.scope(probe, context, manifest)
    require(set(value)=={'configuration','context'} and value['configuration']['nonce']==manifest['nonce']
            and value['configuration']['original_pid']==manifest['original_pid'], 'OUTCOME_RENDERER_ORIGINAL_REFUSED')
    atomic_json(probe.output/'original-renderer-resource-intent.json', {'context': context, 'nonce': manifest['nonce'],
        'original_pid': manifest['original_pid'], 'same_origin_static_JS_only': True,
        'application_or_native_IPC': False, 'automatic_retry': False}, exclusive=True)
    script=Path(__file__).parent/'android-runtime/original_compiled_resources.mjs'
    result=probe.command(['node','--max-old-space-size=128',script,json.dumps(value,separators=(',',':'))], timeout=25,check=False)
    require(result.returncode==0 and not result.stderr.strip() and 0<len(result.stdout)<=65536, 'OUTCOME_RENDERER_READ_UNCONFIRMED')
    proof=json.loads(result.stdout)
    require(type(proof) is dict and proof.get('nonce')==manifest['nonce'] and proof.get('original_pid')==manifest['original_pid']
            and proof.get('declared_safe_code_list') is True and proof.get('declared_presentation_case') is True
            and proof.get('application_or_native_IPC_sent') is False and proof.get('Debugger_enabled') is False
            and proof.get('raw_resource_bytes_exported') is False, 'OUTCOME_RENDERER_READ_UNCONFIRMED')
    original.same_original(probe, manifest)
    pid=probe.shell('pidof',APP,check=False)
    require(pid.returncode==0 and not pid.stderr.strip() and pid.stdout.strip()==str(manifest['original_pid']).encode('ascii'),
            'OUTCOME_RENDERER_ORIGINAL_PID_CHANGED')
    atomic_json(probe.output/'original-renderer-resource-proof.json', {'context': context, 'APK_sha256': probe.binding['sha256'],
        'original_renderer': proof, 'actual_same_original_installed_client': True,
        'code_presence_not_a_real_native_declared_code_failure': True}, exclusive=True)
    return proof
