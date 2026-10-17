"""Opt-in owned long-note measurements; no speed threshold or product mutation bypass."""
import hashlib
import json
import math
import os
from pathlib import Path
import re

from android_runtime_probe import APP, DRIVER, require, require_hosted_emulator

PATH = 'runtime-long.md'
MARKER = '\n末尾输入已确认 😀 / original end input\n'


def original_long_note():
    return ('# 长笔记测量 😀\n\n' + ''.join(f'## 段落 {i}\n'
        '保留中文和 emoji 😀。这是拥有的离线输入，记录真实耗时，不设虚构性能门槛。\n'
        '[原链接](https://example.invalid/long-note) `original Markdown`\n\n' for i in range(768))).encode('utf-8')


def parse_launch(value):
    require(type(value) is bytes and len(value) <= 16384, 'Unbounded launch observation')
    text = value.decode('utf-8','strict')
    require(re.findall(r'^Status:\s*(\S+)\s*$',text,re.M) == ['ok']
        and 'Activity not started' not in text, 'Original app launch was not confirmed')
    result = {}
    for name in ('TotalTime','WaitTime','ThisTime'):
        values = re.findall(r'^'+name+r':\s*([0-9]+)\s*$',text,re.M)
        require(len(values) in ((0,1) if name=='ThisTime' else (1,)), 'Ambiguous launch timing')
        result[name+'_ms'] = int(values[0]) if values else None
    return {**result,'scope':'Android am start -W report; not full editor readiness or frame-paint latency'}


def parse_memory(value, pid):
    require(type(pid) is int and pid>0 and type(value) is bytes and len(value)<=131072, 'Unbounded memory observation')
    text=value.decode('utf-8','strict')
    headers=re.findall(r'\*\* MEMINFO in pid ([0-9]+) \[([^\]\n]+)\] \*\*',text)
    require(headers==[(str(pid),APP)], 'Memory belongs to another process')
    result={'pid':pid,'units':'kB as reported by dumpsys meminfo','scope':'application PID only; separate WebView renderer processes not included'}
    for label,key in (('TOTAL PSS','total_pss_kB'),('TOTAL RSS','total_rss_kB'),('TOTAL SWAP PSS','total_swap_pss_kB')):
        values=re.findall(r'\b'+label+r':\s*([0-9]+)',text)
        require(len(values)==1 if label=='TOTAL PSS' else len(values)<=1, 'Missing or ambiguous memory quantity')
        result[key]=int(values[0]) if values else None
    return result


def validate_metrics(value, phase):
    require(phase in ('performance-create','performance-reopen') and type(value) is dict
        and value.get('phase')==phase and value.get('physical_or_commercial_IME') is False,
        'Wrong original long-note metrics')
    ack=value.get('original_commit_ack_ms')
    require(type(ack) is list and (2<=len(ack)<=16 if phase=='performance-create' else len(ack)==1), 'Wrong original input count')
    for number in [value.get(k) for k in ('open_or_create_to_editable_ms','after_input_to_draft_kept_label_ms','save_to_saved_label_ms')]+ack:
        require(type(number) in (int,float) and math.isfinite(number) and number>=0, 'Invalid actual timing')
    require(value.get('input_backend')=='owned real InputConnection; ordered broadcast acknowledgement'
        and value.get('timing_scope')=='UI readiness and original IME acknowledgements, not frame-paint or physical keystroke latency',
        'Timing scope changed')
    return value


def sample_memory(probe):
    require_hosted_emulator(probe.args.serial)
    probe.wait_owned_connection()
    before=probe.shell('pidof',APP).stdout.strip()
    require(re.fullmatch(rb'[1-9][0-9]{0,9}',before), 'Owned memory process is ambiguous')
    result=probe.shell('dumpsys','meminfo','-s',before.decode(),timeout=30).stdout
    require(probe.shell('pidof',APP).stdout.strip()==before, 'Memory process changed while observing')
    return parse_memory(result,int(before))


def run_performance(probe):
    require(probe.args.scope=='performance', 'Performance scope must be explicit')
    require_hosted_emulator(probe.args.serial)
    expected=original_long_note();require(65536<len(expected)<1024*1024, 'Owned long note left its bounded product limit')
    probe.enable_test_ime()
    stages=[]
    for phase in ('performance-create','performance-reopen'):
        probe.phase(phase,keep_running=True)
        if phase=='performance-reopen':expected+=MARKER.encode()
        require(probe.original(PATH)==expected, 'Original long-note bytes differ after the one Save; no input replay')
        drafts=probe.draft_rows()
        require(len(drafts)==1 and drafts[0][0]==PATH and drafts[0][1].encode()==expected and drafts[0][2]==drafts[0][3],
            'Long-note draft is not the acknowledged original generation')
        raw=probe.device('exec-out','run-as',DRIVER,'cat','files/'+phase+'-metrics.json').stdout
        require(0<len(raw)<=16384, 'Original long-note timing record missing')
        metrics=validate_metrics(json.loads(raw),phase)
        launch=json.loads((probe.output/(phase+'-launch.json')).read_text('utf-8'))
        stages.append({'phase':phase,'launch':launch,'metrics':metrics,'memory':sample_memory(probe),
            'saved_bytes':len(expected),'saved_sha256':hashlib.sha256(expected).hexdigest()})
    require(stages[0]['memory']['pid']!=stages[1]['memory']['pid'], 'Long note was not observed in a new app process')
    probe.force_stop()
    proof={'passed':True,'scope':'performance','source_commit':probe.args.commit,'runtime_tools_commit':probe.args.tools_commit,
        'APK_sha256':probe.binding['sha256'],'compiled_from':probe.args.build_origin,
        'run_id':os.environ['GITHUB_RUN_ID'],'stages':stages,'original_IME_messages_retried':False,
        'original_note_opened_after_process_stop':True,'physical_phone_or_commercial_IME_verified':False,
        'frame_paint_latency_or_entire_process_tree_memory_claimed':False,'performance_threshold_or_optimization_added':False,
        'full_Android_reliability_or_sync_acceptance_claimed':False}
    (probe.output/'long-note-performance-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n','utf-8')
