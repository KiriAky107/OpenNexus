"""Measure production streamed Markdown in the verifier-owned native WebView2.

Synthetic Pinia messages exercise rendering only, without model or network calls.
Timing measures the actual WebView main thread; it is not OS input latency.
"""
from __future__ import annotations

import json
from pathlib import Path


def exercise(page, process, work: Path, vault: Path):
    if vault.resolve() != work.resolve() / 'vault' or process.poll() is not None:
        raise RuntimeError('Preview checks require a live verifier-owned native payload')
    page.evaluate('()=>smokeRouter.push("/chat")')
    page.locator('.composer textarea').wait_for()
    page.evaluate('async()=>await smokePinia._s.get("chat").loadConversations()')
    # Discard the previous workflow's pending tail refresh through the real
    # store lifecycle, so it cannot overwrite synthetic rendering fixtures.
    page.evaluate('async()=>await smokePinia._s.get("chat").createNewConversation()')
    samples = []
    for count in (100, 1000, 5000):
        result = page.evaluate(r'''async lines => {
            const store=smokePinia._s.get('chat'), frame=()=>new Promise(requestAnimationFrame);
            const source=Array.from({length:lines},(_,i)=>`export const value${i}: number = Math.max(${i}, 1) + 2; // preview`).join('\n');
            const markdown='```typescript\n'+source+'\n```\n\nStreaming marker';
            const message={message_id:'native-preview-'+lines,conversation_id:store.activeConversationId,role:'assistant',
                content:markdown,created_at:new Date().toISOString(),citations:[],tool_calls:[],activity:[]};
            const tasks=[], frames=[];
            const observer=new PerformanceObserver(list=>tasks.push(...list.getEntries().map(e=>e.duration)));
            observer.observe({type:'longtask',buffered:false});
            let watching=true, previous=performance.now();
            const observeFrame=now=>{frames.push(now-previous);previous=now;if(watching)requestAnimationFrame(observeFrame)};
            requestAnimationFrame(observeFrame);
            const start=performance.now();
            store.messages=[message];store.liveMessage=store.messages[0];store.isStreaming=true;
            const dom=()=>document.querySelector(`[data-message-id="${message.message_id}"] .markdown-content`);
            const until=async test=>{const deadline=performance.now()+60000;while(!test()){
                if(performance.now()>deadline)throw Error('Native preview condition timeout');await frame()}};
            try {
                await until(()=>dom()?.textContent.includes('Streaming marker'));
                const textMs=performance.now()-start;
                await until(()=>dom()?.querySelector('span[style*="--shiki-light"]'));
                const colorMs=performance.now()-start;
                const code=()=>Array.from(dom().querySelectorAll('pre code > .line'),line=>line.textContent).join('\n').trimEnd();
                if(code()!==source)throw Error('Native colored code differs from the original source');
                if(dom().querySelector('.markdown-code-source')?.textContent.trimEnd()!==source)throw Error('Native copyable code differs from the original source');
                const initialCode=dom().querySelector('pre');
                const initialTokens=dom().querySelectorAll('pre code span').length;
                const changes=[];
                for(let i=0;i<12;i++){
                    const before=performance.now();store.messages[0].content=markdown+' '+i;
                    await frame();changes.push(performance.now()-before);
                }
                store.isStreaming=false;
                await until(()=>dom()?.textContent.includes('Streaming marker 11') && dom()?.querySelector('span[style*="--shiki-light"]'));
                if(code()!==source)throw Error('Unchanged source was lost during streaming');
                const unchangedCodeIdentity=initialCode===dom().querySelector('pre');
                // A cancelled render must not overwrite the user's final text.
                store.isStreaming=true;store.messages[0].content='```typescript\n'+source+'\nconst obsolete = 1;\n```';
                await new Promise(resolve=>setTimeout(resolve,60));
                store.messages[0].content='Final replacement '+lines;store.isStreaming=false;
                await until(()=>dom()?.textContent.trim()==='Final replacement '+lines);
                await new Promise(resolve=>setTimeout(resolve,500));
                if(dom()?.textContent.trim()!=='Final replacement '+lines)throw Error('Stale preview replaced final text');
                await frame();
                return {lines,text_ms:textMs,color_ms:colorMs,code_tokens:initialTokens,
                    unchanged_code_node_retained:unchangedCodeIdentity,next_frame_ms:changes,
                    long_tasks:tasks.length,max_long_task_ms:Math.max(0,...tasks),
                    max_frame_gap_ms:Math.max(0,...frames),final_text_preserved:true,copy_source_preserved:true};
            } finally {watching=false;observer.disconnect();store.isStreaming=false;}
        }''', count)
        samples.append(result)
        print('NATIVE_PREVIEW_SAMPLE', json.dumps(result), flush=True)
        (work / 'native-preview-samples.json').write_text(json.dumps(samples, indent=2) + '\n', encoding='utf-8')
    page.screenshot(path=str(work / 'native-preview.png'))
    return {'passed': True, 'samples': samples,
        'measurement': 'Actual WebView2 production Markdown component with synthetic streamed messages; main-thread and frame observations, not OS input latency'}
