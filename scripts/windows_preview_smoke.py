"""Measure production streamed Markdown in the verifier-owned native WebView2.

Synthetic Pinia messages exercise rendering only, without model or network calls.
Timing measures the actual WebView main thread; it is not OS input latency.
"""
from __future__ import annotations

import json
from pathlib import Path


def exercise(page, process, work: Path, vault: Path, line_counts=(100, 1000, 5000), *, wrapped_line_counts=(), verify_pending_anchor=False, verify_worker_timing=False, warm_pass=False):
    if vault.resolve() != work.resolve() / 'vault' or process.poll() is not None:
        raise RuntimeError('Preview checks require a live verifier-owned native payload')
    page.evaluate('()=>smokeRouter.push("/chat")')
    page.locator('.composer textarea').wait_for()
    page.evaluate('async()=>await smokePinia._s.get("chat").loadConversations()')
    # Discard the previous workflow's pending tail refresh through the real
    # store lifecycle, so it cannot overwrite synthetic rendering fixtures.
    page.evaluate('async()=>await smokePinia._s.get("chat").createNewConversation()')
    samples = []
    cases = [(count, False, 'typescript') for count in line_counts] + [(count, True, 'typescript') for count in wrapped_line_counts]
    if warm_pass:
        # The ts alias reuses the loaded grammar but has a distinct broker key.
        # Identical source therefore exercises actual tokenization again,
        # rather than timing a cached HTML response as a warm Worker run.
        cases += [(count, wrapped, 'ts') for count, wrapped, _ in cases.copy()]
    for count, wrapped, language in cases:
        result = page.evaluate(r'''async ({lines, wrapped, language, verifyPendingAnchor, verifyWorkerTiming}) => {
            const store=smokePinia._s.get('chat'), frame=()=>new Promise(requestAnimationFrame);
            const preferences=smokePinia._s.get('markdown-preferences');
            const savedPreferences={...preferences.normalized};
            preferences.apply({...savedPreferences,wrapCode:wrapped});
            const suffix=wrapped ? ' / 中文 #%'.repeat(8) : '';
            const source=Array.from({length:lines},(_,i)=>`export const value${i}: number = Math.max(${i}, 1) + 2; // preview${suffix}`).join('\n');
            const markdown='```'+language+'\n'+source+'\n```\n\nStreaming marker';
            const message={message_id:'native-preview-'+lines+'-'+wrapped+'-'+language,conversation_id:store.activeConversationId,role:'assistant',
                content:markdown,created_at:new Date().toISOString(),citations:[],tool_calls:[],activity:[]};
            const tasks=[], frames=[], phases=[];
            let phaseStart=performance.now(), phaseName='initial_text';
            const phase=name=>{
                const end=performance.now();phases.push({phase:phaseName,start:phaseStart,end});
                phaseStart=end;phaseName=name;
            };
            const jobs=[], workers=new WeakSet(), listeners=[], originalPost=Worker.prototype.postMessage;
            const readingAnchors=[], originalPoint=document.elementFromPoint;
            document.elementFromPoint=function(x,y){
                const found=Reflect.apply(originalPoint,this,[x,y]), line=found?.closest('.line');
                if(readingAnchors.length<512)readingAnchors.push({x,y,line:line?.dataset.previewLineNumber ?? null,
                    top:line?.getBoundingClientRect().top ?? null,found:found?.className ?? null});
                return found;
            };
            Worker.prototype.postMessage=function(message,...rest){
                if(!workers.has(this)){
                    workers.add(this);const listener=event=>{
                        const job=jobs.find(job=>job.id===event.data.id && job.elapsed_ms===undefined);
                        if(job){
                            job.elapsed_ms=performance.now()-job.start;job.has_colors=Boolean(event.data.html);
                            job.response_characters=event.data.html?.length ?? 0;
                            const timing=event.data.timing;
                            if(timing){
                                if(!['language_load_ms','highlight_ms','worker_ms'].every(key=>Number.isFinite(timing[key]) && timing[key]>=0))
                                    throw Error('Invalid native Worker timing');
                                job.worker_timing=timing;
                                // Aggregate message cloning, scheduling and handler overhead;
                                // clocks from different realms are never subtracted directly.
                                job.message_and_scheduling_ms=Math.max(0,job.elapsed_ms-timing.worker_ms);
                            }
                        }
                    };this.addEventListener('message',listener);listeners.push([this,listener]);
                }
                const start=performance.now();
                const result=Reflect.apply(originalPost,this,[message,...rest]);
                if(typeof message?.source==='string' && typeof message?.language==='string')
                    jobs.push({id:message.id,characters:message.source.length,start,post_ms:performance.now()-start});
                return result;
            };
            const recordTasks=entries=>tasks.push(...entries.map(e=>({start:e.startTime,duration:e.duration})));
            const observer=new PerformanceObserver(list=>recordTasks(list.getEntries()));
            observer.observe({type:'longtask',buffered:false});
            let watching=true, previous=performance.now();
            const observeFrame=now=>{frames.push(now-previous);previous=now;if(watching)requestAnimationFrame(observeFrame)};
            requestAnimationFrame(observeFrame);
            const start=performance.now();
            const heap=()=>performance.memory?.usedJSHeapSize ?? null, heapBefore=heap();
            store.messages=[message];store.liveMessage=store.messages[0];store.isStreaming=true;
            const dom=()=>document.querySelector(`[data-message-id="${message.message_id}"] .markdown-content`);
            const until=async test=>{const deadline=performance.now()+60000;while(!test()){
                if(performance.now()>deadline)throw Error('Native preview condition timeout');await frame()}};
            try {
                await until(()=>dom()?.textContent.includes('Streaming marker'));
                const textMs=performance.now()-start;
                let pendingAnchorDelta=null;
                let pendingAnchorTop=null;
                let pendingAnchorLine=null, pendingAnchorBefore=null;
                let plainAnchorSettlingDelta=null;
                const pendingLineNumber=String(Math.floor(lines/2)+1);
                if(wrapped && verifyPendingAnchor){
                    phase('plain_viewport_anchor');
                    const plainLine=dom()?.querySelector(`.line[data-preview-line-number="${pendingLineNumber}"]`);
                    if(!plainLine)throw Error('Plain code has no reading anchor');
                    plainLine.scrollIntoView({block:'center'});await frame();await frame();
                    const initialPlainTop=plainLine.getBoundingClientRect().top;
                    // content-visibility can refine estimated heights after
                    // scrollIntoView. Establish a stable *plain* reading position
                    // before timing the separate coloring operation. Keep the
                    // same two-pixel coloring threshold and report settling.
                    plainLine.closest('pre').getBoundingClientRect();
                    let previousTop=plainLine.getBoundingClientRect().top, stableFrames=0;
                    const settlingDeadline=performance.now()+2000;
                    while(stableFrames<5){
                        if(dom()?.querySelector('.markdown-code-block[data-highlight-state="complete"]'))
                            throw Error('Coloring completed before a stable plain reading position could be measured');
                        if(performance.now()>settlingDeadline)throw Error('Plain reading position did not settle');
                        await frame();const top=plainLine.getBoundingClientRect().top;
                        stableFrames=Math.abs(top-previousTop)<=0.05 ? stableFrames+1 : 0;previousTop=top;
                    }
                    plainAnchorSettlingDelta=Math.abs(previousTop-initialPlainTop);
                    pendingAnchorTop=plainLine.getBoundingClientRect().top;
                    pendingAnchorLine=plainLine;
                    pendingAnchorBefore={top:pendingAnchorTop,scroll_top:document.querySelector('.message-timeline').scrollTop,
                        chunk_top:plainLine.parentElement.getBoundingClientRect().top,
                        line_height:plainLine.getBoundingClientRect().height};
                }
                phase('initial_color');
                await until(()=>dom()?.querySelector('.markdown-code-block[data-highlight-state="complete"]') &&
                    dom()?.querySelector('span[style*="--shiki-light"]'));
                const colorMs=performance.now()-start;
                if(pendingAnchorTop!==null){
                    await frame();await frame();
                    const coloredLine=dom()?.querySelector(`.line[data-preview-line-number="${pendingLineNumber}"]`);
                    if(!coloredLine)throw Error('Colored code lost the reading anchor');
                    pendingAnchorDelta=Math.abs(coloredLine.getBoundingClientRect().top-pendingAnchorTop);
                    window.nativePreviewDiagnosis={lines,wrapped,before:pendingAnchorBefore,
                        after:{top:coloredLine.getBoundingClientRect().top,scroll_top:document.querySelector('.message-timeline').scrollTop,
                            chunk_top:coloredLine.parentElement.getBoundingClientRect().top,line_height:coloredLine.getBoundingClientRect().height},
                            same_line_node:pendingAnchorLine===coloredLine,delta_px:pendingAnchorDelta,reading_anchor_calls:readingAnchors};
                    if(pendingAnchorDelta>2)throw Error('Coloring displaced the wrapped reading anchor by '+pendingAnchorDelta+' pixels');
                }
                phase('integrity_validation');
                const code=()=>Array.from(dom().querySelectorAll('pre code .line'),line=>line.textContent).join('\n');
                if(code()!==source+'\n')throw Error('Native colored code differs from the original source');
                if(dom().querySelector('.markdown-code-source')?.textContent!==source+'\n')throw Error('Native copyable code differs from the original source');
                const initialCode=dom().querySelector('.shiki');
                const initialLines=Array.from(initialCode.querySelectorAll('code .line'));
                const initialTokens=dom().querySelectorAll('pre code span:not(.markdown-code-chunk)').length;
                const heapColored=heap();
                const jobsAfterInitial=jobs.length;
                if(verifyWorkerTiming && (!jobsAfterInitial || jobs.slice(0,jobsAfterInitial).some(job=>job.has_colors && !job.worker_timing)))
                    throw Error('Actual colored preview has no Worker measurement');
                let codeMutations=0;
                const changed=records=>{for(const record of records){if(initialCode.contains(record.target))codeMutations++}};
                const mutations=new MutationObserver(changed);mutations.observe(dom(),{childList:true,subtree:true});
                const changes=[];
                phase('tail_updates');
                for(let i=0;i<12;i++){
                    const before=performance.now();store.messages[0].content=markdown+' '+i;
                    await frame();changes.push(performance.now()-before);
                }
                store.isStreaming=false;
                phase('tail_settle');
                await until(()=>dom()?.textContent.includes('Streaming marker 11') &&
                    dom()?.querySelector('.markdown-code-block[data-highlight-state="complete"]'));
                changed(mutations.takeRecords());mutations.disconnect();
                phase('retained_code_validation');
                if(code()!==source+'\n')throw Error('Unchanged source was lost during streaming');
                const unchangedCodeIdentity=initialCode===dom().querySelector('.shiki');
                if(!unchangedCodeIdentity)throw Error('Unchanged code DOM was rebuilt during streaming');
                if(codeMutations)throw Error('Unchanged code children were mutated during streaming');
                const finalLines=Array.from(initialCode.querySelectorAll('code .line'));
                if(initialLines.some((line,index)=>line!==finalLines[index]))throw Error('Unchanged code lines were rebuilt');
                const unchangedWorkerJobs=jobs.length-jobsAfterInitial;
                if(unchangedWorkerJobs)throw Error('Unchanged code was submitted to the Worker again');
                const heapTail=heap();
                phase('offscreen_browser_search');
                const probe='value'+Math.floor(lines/2);
                if(!window.find(probe) || getSelection()?.toString()!==probe)throw Error('Browser search could not reach offscreen code');
                getSelection().removeAllRanges();
                await frame();
                phase('viewport_anchor');
                const timeline=document.querySelector('.message-timeline');
                const anchor=finalLines[Math.floor(lines/2)];
                if(!timeline || !anchor)throw Error('Native preview has no reading viewport');
                anchor.scrollIntoView({block:'center'});await frame();await frame();
                const anchorBefore=anchor.getBoundingClientRect().top;
                const timelineBefore=timeline.scrollTop;
                const codeJobsBeforeAnchor=jobs.length;
                store.isStreaming=true;store.messages[0].content=markdown+' anchor update';
                await until(()=>dom()?.textContent.includes('Streaming marker anchor update'));
                await frame();await frame();
                const anchorAfter=anchor.getBoundingClientRect().top;
                const anchorDelta=Math.abs(anchorAfter-anchorBefore);
                if(anchorDelta>2 || Math.abs(timeline.scrollTop-timelineBefore)>2)
                    throw Error('Streaming displaced the reading anchor by '+anchorDelta+' pixels');
                if(initialCode!==dom().querySelector('.shiki') || jobs.length!==codeJobsBeforeAnchor)
                    throw Error('A tail update rebuilt or recolored the anchored code');
                if(wrapped && getComputedStyle(initialCode.querySelector('code')).whiteSpace!=='pre-wrap')
                    throw Error('Native wrapped fixture did not enable wrapping');
                phase('obsolete_render');
                // A cancelled render must not overwrite the user's final text.
                const obsoleteJobStart=jobs.length;
                store.isStreaming=true;store.messages[0].content='```typescript\n'+source+'\nconst obsolete = 1;\n```';
                await new Promise(resolve=>setTimeout(resolve,60));
                store.messages[0].content='Final replacement '+lines;store.isStreaming=false;
                await until(()=>dom()?.textContent.trim()==='Final replacement '+lines);
                await until(()=>jobs.slice(obsoleteJobStart).every(job=>job.elapsed_ms!==undefined));
                await new Promise(resolve=>setTimeout(resolve,50));
                if(dom()?.textContent.trim()!=='Final replacement '+lines)throw Error('Stale preview replaced final text');
                await frame();
                phase('completed');recordTasks(observer.takeRecords());
                const taskAttribution=tasks.map(task=>{
                    const overlaps=phases.map(({phase,start,end})=>({phase,
                        overlap_ms:Math.max(0,Math.min(end,task.start+task.duration)-Math.max(start,task.start))}));
                    overlaps.sort((a,b)=>b.overlap_ms-a.overlap_ms);
                    return {...task,phase:overlaps[0]?.phase ?? 'unclassified',
                        phase_overlap_ms:overlaps[0]?.overlap_ms ?? 0};
                });
                const longTaskPhases=phases.map(({phase,start,end})=>{
                    const assigned=taskAttribution.filter(task=>task.phase===phase);
                    return {phase,elapsed_ms:end-start,long_tasks:assigned.length,
                        max_long_task_ms:Math.max(0,...assigned.map(task=>task.duration))};
                });
                return {lines,wrapped,source_characters:source.length,text_ms:textMs,color_ms:colorMs,code_tokens:initialTokens,
                    unchanged_code_node_retained:unchangedCodeIdentity,unchanged_code_child_mutations:codeMutations,
                    unchanged_worker_jobs:unchangedWorkerJobs,worker_jobs:jobs.map(({start,...job})=>job),next_frame_ms:changes,
                    renderer_js_heap_bytes:{before:heapBefore,colored:heapColored,tail:heapTail},
                    offscreen_code_search_preserved:true,
                    reading_anchor_delta_px:anchorDelta,reading_anchor_preserved:true,
                    pending_color_anchor_delta_px:pendingAnchorDelta,
                    plain_anchor_settling_delta_px:plainAnchorSettlingDelta,
                    obsolete_worker_jobs_completed:jobs.slice(obsoleteJobStart).every(job=>job.elapsed_ms!==undefined),
                    long_tasks:tasks.length,max_long_task_ms:Math.max(0,...tasks.map(task=>task.duration)),
                    long_task_phases:longTaskPhases,
                    long_task_attribution:taskAttribution,
                    attribution_method:'Assign each observed task once, to its greatest time overlap with a measured phase',
                    max_frame_gap_ms:Math.max(0,...frames),final_text_preserved:true,copy_source_preserved:true};
            } finally {
                watching=false;observer.disconnect();store.isStreaming=false;Worker.prototype.postMessage=originalPost;
                document.elementFromPoint=originalPoint;
                preferences.apply(savedPreferences);
                for(const [worker,listener] of listeners)worker.removeEventListener('message',listener);
            }
        }''', {'lines':count,'wrapped':wrapped,'language':language,'verifyPendingAnchor':verify_pending_anchor,'verifyWorkerTiming':verify_worker_timing})
        result['highlight_language'] = language
        result['measurement_pass'] = 'grammar_reuse' if language == 'ts' else 'initial'
        samples.append(result)
        print('NATIVE_PREVIEW_SAMPLE', json.dumps(result), flush=True)
        (work / 'native-preview-samples.json').write_text(json.dumps(samples, indent=2) + '\n', encoding='utf-8')
    page.screenshot(path=str(work / 'native-preview.png'))
    return {'passed': True, 'samples': samples,
        'measurement': 'Actual WebView2 production Markdown component with synthetic streamed messages. Worker timings are elapsed grammar-load and synchronous-tokenizer durations, not OS CPU usage; round trip minus Worker time includes message cloning and scheduling, not pure transfer time.'}
