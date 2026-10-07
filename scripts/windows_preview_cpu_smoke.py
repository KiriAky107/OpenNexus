"""Attribute actual native preview CPU to the owned worker and renderer threads."""
from __future__ import annotations

import json
from pathlib import Path

from windows_preview_cpu import OwnedPreviewCpu
from windows_preview_smoke import exercise as preview


def phase_cpu(samples, start, end):
    points = {point['phase']:point['counters'] for point in samples}
    before, after = points[start], points[end]
    result = {'wall_ms':(after['monotonic_seconds']-before['monotonic_seconds'])*1000}
    for role in ('worker', 'main'):
        old, new = before[role], after[role]
        if any(old[key] != new[key] for key in ('pid','tid','name','creation_100ns')):
            raise RuntimeError('CPU counters came from different threads')
        kernel = new['kernel_100ns']-old['kernel_100ns']
        user = new['user_100ns']-old['user_100ns']
        if min(kernel, user) < 0:
            raise RuntimeError('Native thread CPU counters moved backwards')
        result[role] = {'kernel_ms':kernel/10000, 'user_ms':user/10000, 'total_ms':(kernel+user)/10000}
    return result


def exercise(page, process, work: Path, vault: Path, *, line_counts=(100,1000,5000)):
    # One distinct primer initializes the real production highlighter before
    # thread selection; it cannot populate the 100/1,000/5,000 source caches.
    preview(page, process, work, vault, line_counts=(64,), verify_worker_timing=True)
    cpu = OwnedPreviewCpu(process)
    try:
        targets = page.context.new_cdp_session(page).send('Target.getTargets')['targetInfos']
        workers = [target for target in targets if target['type']=='worker']
        if len(workers) != 1 or '/previewHighlight.worker-' not in workers[0]['url']:
            raise RuntimeError('Expected only the production preview highlighter Worker target: '+json.dumps(workers))
        cpu.select()
        page.expose_function('smokePreviewThreadCpu', cpu.capture)
        result = preview(page, process, work, vault, line_counts=line_counts, wrapped_line_counts=(5000,),
            verify_worker_timing=True, verify_thread_cpu=True)
        for sample in result['samples']:
            sample['cpu_phases'] = {
                'initial_text':phase_cpu(sample['native_thread_cpu'],'before_text','after_plain'),
                'initial_color':phase_cpu(sample['native_thread_cpu'],'after_plain','after_color'),
                'initial_text_and_color':phase_cpu(sample['native_thread_cpu'],'before_text','after_color'),
                'tail_updates':phase_cpu(sample['native_thread_cpu'],'before_tail','after_tail'),
                'offscreen_browser_find':phase_cpu(sample['native_thread_cpu'],'before_browser_find','after_browser_find'),
            }
            if sample['lines']==5000 and sample['cpu_phases']['initial_text_and_color']['worker']['total_ms'] <= 0:
                raise RuntimeError('The actual large highlight had no measured Worker CPU')
        result.update(worker_target={'type':workers[0]['type'],'url':workers[0]['url']},
            host_pid=process.pid,host_creation_100ns=cpu.root_creation,owned_process_ancestry=cpu.ancestry,
            selected_threads={role:item for role,(item,_) in cpu.selected.items()},
            measurement='Windows GetThreadTimes kernel+user counters on one named DedicatedWorker and its CrRendererMain, validated by owned Host ancestry, creation time and exactly one matching production Worker target.',
            limitations=['FILETIME units are 100ns; effective accounting resolution can be coarser, especially for short samples.',
                'Worker helper/parallel GC threads, GPU, Core, Host and service CPU are excluded.',
                'Phase boundaries include profiler binding overhead; use prior uninstrumented measurements for render latency.',
                'Existing round-trip minus Worker elapsed remains aggregate message cloning and scheduling, not pure transmission time.'])
        (work/'native-preview-cpu.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n','utf-8')
        return result
    except BaseException:
        diagnosis = page.evaluate('()=>window.nativePreviewTailDiagnosis ?? null')
        (work/'native-preview-cpu-tail-diagnosis.json').write_text(json.dumps(diagnosis,indent=2)+'\n','utf-8')
        raise
    finally:
        (work/'native-preview-thread-inventory.json').write_text(json.dumps(
            {'ancestry':cpu.ancestry,'threads':cpu.inventory},ensure_ascii=False,indent=2)+'\n','utf-8')
        cpu.close()
