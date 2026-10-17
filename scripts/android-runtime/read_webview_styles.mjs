// Read-only diagnostics on the one forwarded, owned debug WebView. No DOM changes.
const target = new URL(process.argv[2])
if (target.protocol !== 'ws:' || target.hostname !== '127.0.0.1' || !/^\d+$/.test(target.port)
    || !/^\/devtools\/page\/[A-Za-z0-9_-]+$/.test(target.pathname) || target.search || target.hash) {
  throw Error('Refusing an unrelated WebView endpoint')
}
const expression = `JSON.stringify((()=>{
  const box=selector=>{const e=document.querySelector(selector);if(!e)return null;const r=e.getBoundingClientRect();return {
    visible:getComputedStyle(e).display!=='none'&&r.width>0&&r.height>0,left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height,
    clientWidth:e.clientWidth,scrollWidth:e.scrollWidth,position:getComputedStyle(e).position}}
  // Geometry only, scoped to owned navigation controls; no names, paths or input values.
  const targets=selector=>Array.from(document.querySelectorAll(selector)).slice(0,50).map(e=>{
    const r=e.getBoundingClientRect();return {visible:getComputedStyle(e).display!=='none'&&r.width>0&&r.height>0,width:r.width,height:r.height}
  }).filter(e=>e.visible)
  return {
  theme:document.documentElement.dataset.theme,
  root:{primary:getComputedStyle(document.documentElement).getPropertyValue('--color-text-primary'),scheme:getComputedStyle(document.documentElement).colorScheme},
  elements:Array.from(document.querySelectorAll('.app-dialog,.mobile-dialog,.preferences-title h2,.preferences-title button,.theme-select,.theme-info strong,.language select,.badge.success')).slice(0,40).map(e=>({
    tag:e.tagName,label:e.getAttribute('aria-label'),color:getComputedStyle(e).color,background:getComputedStyle(e).backgroundColor,
    primary:getComputedStyle(e).getPropertyValue('--color-text-primary'),forcedAdjust:getComputedStyle(e).forcedColorAdjust
  })),
  layout:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio,rootWidth:document.documentElement.clientWidth,rootScrollWidth:document.documentElement.scrollWidth,
    compact:document.querySelector('.app-shell')?.classList.contains('compact'),editor:box('.visual-editor'),toolbar:box('.markdown-toolbar'),
    floating:box('.floating-formatting'),main:box('.main-content'),files:box('.secondary-sidebar'),
    fileTargets:{rows:targets('.mobile-files .tree-node'),header:targets('.mobile-files .sidebar-header button'),search:targets('.mobile-files .file-search input,.mobile-files .file-search button')}},
  // Only the known synthetic note editor's attributes, never values/content or credential controls.
  editorAttributes:Array.from(document.querySelectorAll('[aria-label="Markdown note body"]')).slice(0,4).map(e=>({
    tag:e.tagName,id:e.id,role:e.getAttribute('role'),ariaLabel:e.getAttribute('aria-label'),contentEditable:e.contentEditable,
    disabled:e.disabled===true,readOnly:e.readOnly===true,display:getComputedStyle(e).display,visible:e.getBoundingClientRect().height>0
  })),
  // Only readiness/geometry in this owned synthetic image fixture. No source
  // URLs, note values, credentials, event injection or DOM mutation.
  imageSave:{
    richImages:Array.from(document.querySelectorAll('.visual-editor .milkdown-host img')).slice(0,4).map(e=>({
      complete:e.complete,width:e.naturalWidth,height:e.naturalHeight,error:e.dataset.workspaceAssetError==='true'
    })),
    canvasImages:Array.from(document.querySelectorAll('.canvas-visual img')).slice(0,4).map(e=>({
      complete:e.complete,width:e.naturalWidth,height:e.naturalHeight
    })),
    busy:document.querySelector('.feedback[aria-busy="true"]')!==null,
    save:Array.from(document.querySelectorAll('.editor-header .save-button')).slice(0,2).map(e=>{
      const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)
      return {enabled:!e.disabled,visible:r.width>0&&r.height>0,hit:hit===e||e.contains(hit),
        left:r.left,top:r.top,width:r.width,height:r.height}
    })
  }
  }
})())`
const result = await new Promise((resolve, reject) => {
  const socket = new WebSocket(target)
  const timer = setTimeout(() => { socket.close(); reject(Error('Read-only WebView observation timed out')) }, 12000)
  socket.addEventListener('open', () => socket.send(JSON.stringify({ id:1, method:'Runtime.evaluate', params:{expression,returnByValue:true} })))
  socket.addEventListener('message', event => {
    const message=JSON.parse(event.data)
    if(message.id!==1)return
    clearTimeout(timer); socket.close()
    if(message.error || message.result?.exceptionDetails || typeof message.result?.result?.value!=='string') reject(Error('WebView style observation failed'))
    else resolve(JSON.parse(message.result.result.value))
  })
  socket.addEventListener('error', () => { clearTimeout(timer); reject(Error('Owned WebView connection failed')) })
})
process.stdout.write(JSON.stringify(result,null,2)+'\n')
