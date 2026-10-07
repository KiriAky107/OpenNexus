"""Check bounded log rendering against a real, explicitly approved native run.

Only the verifier-owned vault and Host are used. Clipboard writes are captured
inside this test WebView so the user's system clipboard is preserved.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from windows_experiment_smoke import click_confirmation


def exercise(page, process, work: Path, vault: Path):
    if vault.resolve() != work.resolve() / 'vault' or process.poll() is not None:
        raise RuntimeError('Log checks require a live verifier-owned native payload')
    folder = vault / 'experiments' / 'native-logs'
    folder.mkdir(parents=True)
    stdout_block = ('<img src=x onerror=alert(1)> stdout 中文😀 #%\r\n' * 10).encode('utf-8')
    stderr_block = ('<script>not executable</script> stderr 中文😀 #%\r\n' * 10).encode('utf-8')
    repeats = 192
    source = '\r\n'.join([
        'import sys, time', f'out = {stdout_block!r}', f'err = {stderr_block!r}',
        f'for _ in range({repeats}):', '    sys.stdout.buffer.write(out)',
        '    sys.stdout.buffer.flush()', '    sys.stderr.buffer.write(err)',
        '    sys.stderr.buffer.flush()', '    time.sleep(0.04)', '',
    ]).encode('utf-8')
    entry = 'experiments/native-logs/持续日志 #%.py'
    (vault / entry).write_bytes(source)
    source_hash = hashlib.sha256(source).hexdigest()
    page.wait_for_function("async path=>(await smokeInvoke('workspace_tree')).some(file=>file.path===path)", arg=entry)
    page.evaluate("async path=>{await smokeRouter.push('/workspace');await smokePinia._s.get('workspace').refreshFileTree();await smokePinia._s.get('editor').loadFile('/'+path);smokePinia._s.get('workspace').openFile('/'+path)}", entry)
    page.get_by_role('button', name='实验', exact=True).click()
    panel = page.locator('#experiment-panel')
    page.wait_for_function("()=>document.querySelector('#experiment-panel .panel-scroll')?.getAttribute('aria-busy')==='false'")
    panel.locator('.run-form select').select_option(entry)
    panel.get_by_text('输入文件与资源限制', exact=True).click()
    panel.get_by_label('日志 KiB', exact=True).fill('128')
    panel.get_by_label('墙钟秒数', exact=True).fill('30')
    panel.locator('[data-action=prepare-run]').click()
    panel.locator('[data-action=confirm-run]').wait_for()
    # Install observations before native approval, not after rendering finished.
    page.evaluate(r'''()=>{
        const samples=[], tasks=[], frames=[];
        let alive=true, before=performance.now(), previous=new Map(), ends=new Map(), mutations=0;
        const observer=new PerformanceObserver(list=>tasks.push(...list.getEntries().map(task=>task.duration)));
        observer.observe({type:'longtask',buffered:false});
        const changed=new MutationObserver(records=>{mutations+=records.length});
        changed.observe(document.querySelector('#experiment-panel'),{childList:true,subtree:true,characterData:true});
        const sample=now=>{
            frames.push(now-before);before=now;
            const logs=Array.from(document.querySelectorAll('.experiment-log')).map(log=>{
                const nodes=Array.from(log.querySelectorAll('[data-log-chunk]'));
                const old=previous.get(log.dataset.stream) ?? new Map();
                const retained=nodes.filter(node=>old.has(node.dataset.logChunk));
                if(retained.some(node=>old.get(node.dataset.logChunk)!==node))throw Error('Unchanged native log chunk was replaced');
                const preview=log.querySelector('.log-preview');
                const advanced=ends.has(log.dataset.stream) && ends.get(log.dataset.stream)!==Number(preview.dataset.end);
                ends.set(log.dataset.stream,Number(preview.dataset.end));
                previous.set(log.dataset.stream,new Map(nodes.map(node=>[node.dataset.logChunk,node])));
                if(nodes.length>13 || preview.textContent.length>24577)throw Error('Native log DOM exceeded its window bound');
                if(log.querySelector('img,script'))throw Error('Native log text became executable markup');
                return {stream:log.dataset.stream,chunks:nodes.length,characters:preview.textContent.length,
                    start:Number(preview.dataset.start),end:Number(preview.dataset.end),advanced,reused:retained.length};
            });
            if(logs.length)samples.push(logs);
            if(alive)requestAnimationFrame(sample);
        };requestAnimationFrame(sample);
        window.nativeLogMeasurement={stop:()=>{alive=false;observer.disconnect();changed.disconnect();return {
            samples,max_long_task_ms:Math.max(0,...tasks),max_frame_gap_ms:Math.max(0,...frames),dom_mutations:mutations}}};
    }''')
    approved = click_confirmation(page, process, '[data-action=confirm-run]', '确认运行 / Confirm run', source_hash)
    page.wait_for_function("()=>Number(document.querySelector('.experiment-log[data-stream=stdout] .log-preview')?.dataset.end)>24576")
    growing = panel.locator('.experiment-log[data-stream=stdout]')
    growing.locator('.log-preview').click()
    frozen_text, frozen_heading = growing.locator('.log-preview').text_content(), growing.locator('h4').text_content()
    page.wait_for_function("heading=>document.querySelector('.experiment-log[data-stream=stdout] h4')?.textContent!==heading", arg=frozen_heading)
    if growing.locator('.log-preview').text_content() != frozen_text:
        raise RuntimeError('Actual native output displaced a paused reader')
    growing.locator('[data-log-action=follow]').click()
    page.wait_for_function("()=>['已完成','失败','已超限停止'].includes(document.querySelector('.run-detail h3')?.textContent)")
    if panel.locator('.run-detail > .section-heading > h3').inner_text() != '已完成':
        raise RuntimeError('Approved log fixture did not complete: ' + panel.locator('.run-detail').inner_text())
    measured = page.evaluate('()=>nativeLogMeasurement.stop()')
    run = page.evaluate("async()=>{const vault_id=smokePinia._s.get('workspace').vaultId;const history=await smokeInvoke('experiment_request',{request:{vault_id,action:{kind:'history',limit:10,cursor:null}}});return smokeInvoke('experiment_request',{request:{vault_id,action:{kind:'record',operation_id:history.items[0].operation_id}}})}")
    if run['summary']['request']['entry']['hash'] != source_hash or run['result']['exit_code'] != 0:
        raise RuntimeError('Log record differs from the approved run')
    (work / 'native-experiment-log-observed.json').write_text(json.dumps({'run': run, 'measurement': measured}, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    # CPython getpath warns if its executable exists but cannot be realpath'ed
    # under the container ACL. Preserve this exact, fixture-owned diagnostic;
    # arbitrary additional stderr still fails byte-for-byte verification.
    runtime_path = (Path(process.args[0]).resolve().parent / 'runtimes/python-3.13.16-windows-x64/python.exe')
    startup_warning = ('Failed to find real location of ' + '\\\\?\\' + str(runtime_path) + '\n').encode('utf-8')
    checks = {}
    # Each stream gets half of the selected total retention limit.
    for name, original in [('stdout', stdout_block * repeats), ('stderr', stderr_block * repeats)]:
        stream = run['result']['logs'][name]
        startup_bytes = 0
        if name == 'stderr' and stream['bytes_seen'] == len(original) + len(startup_warning):
            original = startup_warning + original
            startup_bytes = len(startup_warning)
        expected = original[:stream['retained_bytes']].decode('utf-8', errors='replace')
        if stream['text'] != expected or stream['bytes_seen'] != len(original) or stream['retained_bytes'] != 64 * 1024 or not stream['truncated'] or not stream['complete']:
            raise RuntimeError('Stored native log bytes or truncation evidence differs: ' + name)
        log = panel.locator(f'.experiment-log[data-stream="{name}"]')
        log.locator('[data-log-notice=retention]').wait_for()
        if log.locator('img,script').count():
            raise RuntimeError('Malicious log markup was rendered')
        log.locator('[data-log-action=first]').click()
        parts = []
        for _ in range(32):
            parts.append(log.locator('.log-preview').text_content())
            if log.locator('[data-log-action=next]').is_disabled():
                break
            log.locator('[data-log-action=next]').click()
        else:
            raise RuntimeError('Native log pagination did not terminate')
        if ''.join(parts) != expected:
            raise RuntimeError('Native log paging changed or omitted retained text')
        page.evaluate('''()=>{
            window.nativeLogClipboardDescriptor=Object.getOwnPropertyDescriptor(navigator,'clipboard');
            Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.nativeLogCopied=text}}});
        }''')
        try:
            log.locator('[data-log-action=copy]').click()
            page.wait_for_function('()=>typeof window.nativeLogCopied==="string"')
            copied = page.evaluate('()=>window.nativeLogCopied')
        finally:
            page.evaluate('''()=>{const descriptor=window.nativeLogClipboardDescriptor;
                if(descriptor)Object.defineProperty(navigator,'clipboard',descriptor);else delete navigator.clipboard;
                delete window.nativeLogCopied;delete window.nativeLogClipboardDescriptor;}''')
        if copied != expected:
            raise RuntimeError('Copy did not include all available retained text')
        log.locator('[data-log-action=follow]').click()
        final = log.locator('.log-preview').text_content()
        if not expected.endswith(final):
            raise RuntimeError('Native log follow did not show the retained end')
        checks[name] = {'bytes_seen': len(original), 'retained_bytes': stream['retained_bytes'],
            'retained_text_sha256': hashlib.sha256(expected.encode('utf-8')).hexdigest(),
            'pages': len(parts), 'copy_retained_text_equal': True, 'storage_truncated': True,
            'invalid_utf8': stream['invalid_utf8'], 'runtime_startup_diagnostic_bytes': startup_bytes}
    if not any(item['advanced'] and item['reused'] and item['start'] > 0 for sample in measured['samples'] for item in sample):
        raise RuntimeError('Native growing log did not demonstrate reused bounded chunk nodes')
    page.screenshot(path=str(work / 'native-experiment-logs.png'))
    result = {'passed': True, 'operation_id': run['summary']['request']['operation_id'],
        'native_approval': approved, 'streams': checks, 'measurement': measured, 'paused_reader_preserved': True,
        'scope': 'Actual packaged native experiment and production log UI; copied decoded retained prefix, not discarded bytes; system clipboard unchanged'}
    (work / 'native-experiment-log-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return result
