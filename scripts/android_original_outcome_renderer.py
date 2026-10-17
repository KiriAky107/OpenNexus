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
    if result.returncode!=0 or result.stderr.strip() or not 0<len(result.stdout)<=65536:
        diagnostic={'stage':'admission','reason':'unconfirmed'}
        if 0<len(result.stdout)<=65536:
            try:
                child=json.loads(result.stdout)
                if (type(child) is dict and set(child)=={'error','stage','reason'}
                    and child['error']=='OWNED_COMPILED_RESOURCE_UNCONFIRMED'
                    and child['stage'] in ('admission','page-list','socket-open','evaluation-send','evaluation-reply','evaluation-exception','resource-summary','resource-tree','resource-content')
                    and child['reason'] in ('unconfirmed','timeout','send-unconfirmed','connection-unconfirmed','reply-refused','summary-refused',
                        'origin','bound','resource-unavailable','crypto-unavailable','page-exception','no-owned-script')):
                    diagnostic={'stage':child['stage'],'reason':child['reason']}
            except (ValueError,TypeError):pass
        atomic_json(probe.output/'original-renderer-resource-diagnostic.json',
            {'nonce':manifest['nonce'],'original_pid':manifest['original_pid'],**diagnostic,
             'child_returncode':result.returncode,'stderr_bytes_discarded':len(result.stderr),
             'raw_error_resource_or_credentials_exported':False,'automatic_retry':False},exclusive=True)
    require(result.returncode==0 and not result.stderr.strip() and 0<len(result.stdout)<=65536, 'OUTCOME_RENDERER_READ_UNCONFIRMED')
    proof=json.loads(result.stdout)
    require(type(proof) is dict and proof.get('nonce')==manifest['nonce'] and proof.get('original_pid')==manifest['original_pid']
            and proof.get('declared_safe_code_list') is True and proof.get('declared_presentation_case') is True
            and proof.get('application_or_native_IPC_sent') is False and proof.get('Debugger_enabled') is False
            and proof.get('resource_read_transport')=='Page.getResourceContent'
            and proof.get('page_fetch_or_CSP_change_sent') is False
            and proof.get('resource_bytes_transferred_to_owned_reader') is True
            and proof.get('resource_bytes_written_to_artifacts') is False, 'OUTCOME_RENDERER_READ_UNCONFIRMED')
    original.same_original(probe, manifest)
    pid=probe.shell('pidof',APP,check=False)
    require(pid.returncode==0 and not pid.stderr.strip() and pid.stdout.strip()==str(manifest['original_pid']).encode('ascii'),
            'OUTCOME_RENDERER_ORIGINAL_PID_CHANGED')
    atomic_json(probe.output/'original-renderer-resource-proof.json', {'context': context, 'APK_sha256': probe.binding['sha256'],
        'original_renderer': proof, 'actual_same_original_installed_client': True,
        'code_presence_not_a_real_native_declared_code_failure': True}, exclusive=True)
    return proof
