// Input in the sole owned debug WebView, explicitly separate from physical
// touch, Android accessibility, JS element.click and direct application IPC.
import { pathToFileURL } from 'node:url'

function requireValue(ok) { if (!ok) throw Error('OWNED_PREVIEW_INPUT_REFUSED') }
const validNonce = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)

export function previewExpression(nonce) {
  requireValue(validNonce(nonce))
  return `(()=>{
    const buttons=Array.from(document.querySelectorAll('.association button')).filter(e=>e.textContent?.trim()==='Preview merge');
    const targets=buttons.map(e=>{const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
      const style=getComputedStyle(e);return {left:r.left,top:r.top,width:r.width,height:r.height,enabled:!e.disabled,
        visible:style.display!=='none'&&style.visibility!=='hidden',hit:hit===e||e.contains(hit)}});
    return {observer:window.__onxOwnedAssociationObserver?.observerId===${JSON.stringify(nonce)},
      focused:document.hasFocus(),visible:document.visibilityState==='visible',
      busy:document.querySelector('.association')?.getAttribute('aria-busy')==='true',
      width:innerWidth,height:innerHeight,targets};
  })()`
}

export function installCycleObserverExpression(nonce) {
  requireValue(validNonce(nonce))
  return `(()=>{
    const nonce=${JSON.stringify(nonce)},roots=document.querySelectorAll('.mobile-sync-cycle');
    if(roots.length!==1)throw Error('OWNED_CYCLE_INPUT_REFUSED');
    if(!window.__onxOwnedCycleObservers)Object.defineProperty(window,'__onxOwnedCycleObservers',{value:Object.create(null)});
    const all=window.__onxOwnedCycleObservers;
    if(Object.hasOwn(all,nonce)||Object.keys(all).length>=8)throw Error('OWNED_CYCLE_INPUT_REFUSED');
    const original={nonce,events:[],overflow:false};all[nonce]=original;
    for(const kind of ['touchstart','touchend','click'])roots[0].addEventListener(kind,event=>{
      const button=event.target instanceof Element?event.target.closest('button'):null;
      if(!button||!roots[0].contains(button)||button.textContent?.trim()!=='Sync one cycle now')return;
      if(original.events.length>=8){original.overflow=true;return;}
      original.events.push({kind,trusted:event.isTrusted===true});
    },true);
    return {installed:true,nonce};
  })()`
}

export function cycleExpression(nonce) {
  requireValue(validNonce(nonce))
  return `(()=>{
    const roots=document.querySelectorAll('.mobile-sync-cycle'),root=roots.length===1?roots[0]:null;
    const buttons=root?Array.from(root.querySelectorAll('button')).filter(e=>e.textContent?.trim()==='Sync one cycle now'):[];
    const targets=buttons.map(e=>{const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2),style=getComputedStyle(e);
      return {left:r.left,top:r.top,width:r.width,height:r.height,enabled:!e.disabled,
        visible:style.display!=='none'&&style.visibility!=='hidden',hit:hit===e||e.contains(hit)}});
    const original=window.__onxOwnedCycleObservers?.[${JSON.stringify(nonce)}];
    return {observer:original?.nonce===${JSON.stringify(nonce)}&&!original.overflow&&original.events.length===0,
      focused:document.hasFocus(),visible:document.visibilityState==='visible',busy:!root,
      width:innerWidth,height:innerHeight,targets};
  })()`
}

export function cycleResultExpression(nonce) {
  requireValue(validNonce(nonce))
  return `(()=>{const value=window.__onxOwnedCycleObservers?.[${JSON.stringify(nonce)}];
    return value?{nonce:value.nonce,overflow:value.overflow,events:value.events.slice(0,8)}:null})()`
}

export function admitCycleResult(value, nonce) {
  requireValue(value?.nonce === nonce && value.overflow === false && Array.isArray(value.events) && value.events.length === 3)
  requireValue(['touchstart', 'touchend', 'click'].every((kind, index) => value.events[index]?.kind === kind && value.events[index].trusted === true))
  return value
}

export function admitPreview(value) {
  requireValue(value?.observer === true && value.focused === true && value.visible === true && value.busy === false)
  requireValue(Number.isFinite(value.width) && Number.isFinite(value.height) && value.width >= 320 && value.width <= 2048
    && value.height >= 320 && value.height <= 4096 && Array.isArray(value.targets) && value.targets.length === 1)
  const r = value.targets[0]
  requireValue(r.enabled === true && r.visible === true && r.hit === true
    && ['left', 'top', 'width', 'height'].every(k => Number.isFinite(r[k]))
    && r.width >= 44 && r.height >= 44 && r.left >= 0 && r.top >= 0
    && r.left + r.width <= value.width && r.top + r.height <= value.height)
  return { left: r.left, top: r.top, width: r.width, height: r.height }
}

export async function touchPreview(cdp, nonce, trace, options = {}) {
  const now = options.now ?? (() => performance.now())
  const sleep = options.sleep ?? (ms => new Promise(resolve => setTimeout(resolve, ms)))
  const deadline = now() + 5000
  let previous = null, since = null, box
  while (true) {
    const reply = await cdp('Runtime.evaluate', { expression: options.cycle === true ? cycleExpression(nonce) : previewExpression(nonce), returnByValue: true })
    requireValue(!reply.exceptionDetails)
    try { box = admitPreview(reply.result?.value) }
    catch {
      // Only bounded read-only readiness observations; no input has been
      // attempted, and a later ready frame must still settle for 300ms.
      previous = null; since = null
      requireValue(now() < deadline)
      await sleep(80)
      continue
    }
    const at = now()
    if (previous && Object.keys(box).every(k => Math.abs(previous[k] - box[k]) <= 0.5)) {
      if (at - since >= 300) break
    } else { previous = box; since = at }
    requireValue(at < deadline)
    await sleep(80)
  }
  trace.geometry = box
  trace.touchStart = 'attempted_without_confirmation'
  await cdp('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [
    { x: box.left + box.width / 2, y: box.top + box.height / 2, id: 1, radiusX: 1, radiusY: 1, force: 1 },
  ] })
  trace.touchStart = 'accepted'
  await sleep(50)
  trace.touchEnd = 'attempted_without_confirmation'
  await cdp('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  trace.touchEnd = 'accepted'
  trace.input_pair_accepted = true
  trace.actual_UI_or_native_result_verified = false
}

async function main() {
  const trace = { input_backend: 'Chromium Input.dispatchTouchEvent', physical_touch: false,
    JS_click_or_direct_IPC: false, automatic_retry: false, touchStart: 'not_sent', touchEnd: 'not_sent' }
  let socket
  try {
    const target = new URL(process.argv[2]), nonce = process.argv[3], mode = process.argv[4] ?? 'preview'
    requireValue(mode === 'preview' || mode === 'cycle')
    requireValue(target.protocol === 'ws:' && target.hostname === '127.0.0.1' && /^\d+$/.test(target.port)
      && /^\/devtools\/page\/[A-Za-z0-9_-]+$/.test(target.pathname) && !target.search && !target.hash && validNonce(nonce))
    socket = new WebSocket(target)
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('OWNED_PREVIEW_INPUT_TIMEOUT')), 5000)
      socket.addEventListener('open', () => { clearTimeout(timer); resolve() }, { once: true })
      socket.addEventListener('error', () => { clearTimeout(timer); reject(Error('OWNED_PREVIEW_INPUT_UNAVAILABLE')) }, { once: true })
    })
    let id = 0
    const cdp = (method, params) => new Promise((resolve, reject) => {
      const original = ++id
      const timer = setTimeout(() => { socket.removeEventListener('message', receive); reject(Error('OWNED_PREVIEW_INPUT_TIMEOUT')) }, 5000)
      const receive = event => {
        const message = JSON.parse(event.data)
        if (message.id !== original) return
        clearTimeout(timer); socket.removeEventListener('message', receive)
        if (message.error) reject(Error('OWNED_PREVIEW_INPUT_UNKNOWN'))
        else resolve(message.result)
      }
      socket.addEventListener('message', receive)
      socket.send(JSON.stringify({ id: original, method, params }))
    })
    if (mode === 'cycle') {
      const ready = await cdp('Runtime.evaluate', { expression: installCycleObserverExpression(nonce), returnByValue: true })
      requireValue(!ready.exceptionDetails && ready.result?.value?.installed === true && ready.result.value.nonce === nonce)
    }
    await touchPreview(cdp, nonce, trace, { cycle: mode === 'cycle' })
    if (mode === 'cycle') {
      // Read the original event result only. A missed/unknown click receives
      // no second input and is never turned into a product invocation.
      const deadline = performance.now() + 2000
      while (true) {
        const result = await cdp('Runtime.evaluate', { expression: cycleResultExpression(nonce), returnByValue: true })
        requireValue(!result.exceptionDetails)
        trace.original_UI_events = result.result?.value
        try { admitCycleResult(trace.original_UI_events, nonce); break }
        catch { requireValue(performance.now() < deadline); await new Promise(resolve => setTimeout(resolve, 80)) }
      }
      trace.one_trusted_UI_click_observed = true
    }
  } catch {
    trace.failed = true; trace.raw_error_returned = false; process.exitCode = 1
  } finally {
    socket?.close()
    process.stdout.write(JSON.stringify(trace) + '\n')
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main()
