"""Read actual original renderer asset hashes before arming the native pause."""
import json
from pathlib import Path

from android_auth_fixture import atomic_json
from android_desktop_handoff import require
from android_runtime_probe import APP
import android_original_outcome_process as original


def safe_tree_summary(value):
    flags = ('frame_present', 'frame_id_valid', 'frame_origin_owned', 'resources_array')
    counts = ('resource_count_bounded', 'owned_script_candidates', 'owned_other_js_candidates')
    types = ('Script', 'Other', 'Image', 'Stylesheet', 'Fetch', 'XHR', 'Unknown')
    require(type(value) is dict and set(value) == set(flags + counts + ('types',)), 'OUTCOME_RESOURCE_SUMMARY_REFUSED')
    require(all(type(value[k]) is bool for k in flags)
            and all(type(value[k]) is int and 0 <= value[k] <= 513 for k in counts)
            and type(value['types']) is dict and set(value['types']) == set(types)
            and all(type(v) is int and 0 <= v <= 512 for v in value['types'].values())
            and sum(value['types'].values()) <= min(value['resource_count_bounded'], 512)
            and value['owned_script_candidates'] <= value['types']['Script']
            and value['owned_other_js_candidates'] <= value['types']['Other'], 'OUTCOME_RESOURCE_SUMMARY_REFUSED')
    return value


def safe_script_summary(value):
    counts = ('parsed_notifications', 'owned_candidates')
    flags = ('protocol_admitted', 'Debugger_enable_reply_seen', 'unexpected_pause_observed')
    require(type(value) is dict and set(value) == set(counts + flags)
            and all(type(value[k]) is bool for k in flags)
            and type(value['parsed_notifications']) is int and 0 <= value['parsed_notifications'] <= 513
            and type(value['owned_candidates']) is int and 0 <= value['owned_candidates'] <= min(value['parsed_notifications'], 25)
            and (not value['Debugger_enable_reply_seen'] or value['protocol_admitted']), 'OUTCOME_SCRIPT_SUMMARY_REFUSED')
    return value


def read_original_renderer(probe, context, manifest, value):
    original.scope(probe, context, manifest)
    require(set(value)=={'configuration','context'} and value['configuration']['nonce']==manifest['nonce']
            and value['configuration']['original_pid']==manifest['original_pid'], 'OUTCOME_RENDERER_ORIGINAL_REFUSED')
    atomic_json(probe.output/'original-renderer-resource-intent.json', {'context': context, 'nonce': manifest['nonce'],
        'original_pid': manifest['original_pid'], 'same_origin_static_JS_only': True,
        'resource_read_transport': 'Debugger.getScriptSource', 'Debugger_domain_enable_only': True,
        'pause_breakpoint_or_evaluation': False,
        'application_or_native_IPC': False, 'automatic_retry': False}, exclusive=True)
    script=Path(__file__).parent/'android-runtime/original_compiled_scripts.mjs'
    result=probe.command(['node','--max-old-space-size=128',script,json.dumps(value,separators=(',',':'))], timeout=30,check=False)
    if result.returncode!=0 or result.stderr.strip() or not 0<len(result.stdout)<=65536:
        diagnostic={'stage':'admission','reason':'unconfirmed'}
        if 0<len(result.stdout)<=65536:
            try:
                child=json.loads(result.stdout)
                if (type(child) is dict and set(child) in ({'error','stage','reason'}, {'error','stage','reason','tree_summary'}, {'error','stage','reason','script_summary'})
                    and child['error']=='OWNED_COMPILED_RESOURCE_UNCONFIRMED'
                    and child['stage'] in ('admission','page-list','protocol','socket-open','script-list','script-content','evaluation-send','evaluation-reply','evaluation-exception','resource-summary','resource-tree','resource-content')
                    and child['reason'] in ('unconfirmed','timeout','send-unconfirmed','connection-unconfirmed','reply-refused','summary-refused',
                        'origin','bound','resource-unavailable','crypto-unavailable','page-exception','no-owned-script','script-ambiguous','unexpected-pause')):
                    diagnostic={'stage':child['stage'],'reason':child['reason']}
                    if 'tree_summary' in child:
                        diagnostic['tree_summary']=safe_tree_summary(child['tree_summary'])
                    if 'script_summary' in child:
                        diagnostic['script_summary']=safe_script_summary(child['script_summary'])
            except (ValueError,TypeError,RuntimeError):pass
        atomic_json(probe.output/'original-renderer-resource-diagnostic.json',
            {'nonce':manifest['nonce'],'original_pid':manifest['original_pid'],**diagnostic,
             'child_returncode':result.returncode,'stderr_bytes_discarded':len(result.stderr),
             'raw_error_resource_or_credentials_exported':False,'automatic_retry':False},exclusive=True)
    require(result.returncode==0 and not result.stderr.strip() and 0<len(result.stdout)<=65536, 'OUTCOME_RENDERER_READ_UNCONFIRMED')
    proof=json.loads(result.stdout)
    require(type(proof) is dict and proof.get('nonce')==manifest['nonce'] and proof.get('original_pid')==manifest['original_pid']
            and proof.get('declared_safe_code_list') is True and proof.get('declared_presentation_case') is True
            and proof.get('application_or_native_IPC_sent') is False and proof.get('Debugger_enabled') is True
            and proof.get('Debugger_pause_breakpoint_or_evaluation_sent') is False
            and proof.get('resource_read_transport')=='Debugger.getScriptSource'
            and proof.get('digest_scope')=='UTF-8 bytes of decoded V8 script source, not HTTP wire encoding'
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
