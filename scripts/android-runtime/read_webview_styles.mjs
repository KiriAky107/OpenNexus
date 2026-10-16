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
  return {
  theme:document.documentElement.dataset.theme,
  root:{primary:getComputedStyle(document.documentElement).getPropertyValue('--color-text-primary'),scheme:getComputedStyle(document.documentElement).colorScheme},
  elements:Array.from(document.querySelectorAll('.app-dialog,.mobile-dialog,.preferences-title h2,.preferences-title button,.theme-select,.theme-info strong,.language select,.badge.success')).slice(0,40).map(e=>({
    tag:e.tagName,label:e.getAttribute('aria-label'),color:getComputedStyle(e).color,background:getComputedStyle(e).backgroundColor,
    primary:getComputedStyle(e).getPropertyValue('--color-text-primary'),forcedAdjust:getComputedStyle(e).forcedColorAdjust
  })),
  layout:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio,rootWidth:document.documentElement.clientWidth,rootScrollWidth:document.documentElement.scrollWidth,
    compact:document.querySelector('.app-shell')?.classList.contains('compact'),editor:box('.visual-editor'),toolbar:box('.markdown-toolbar'),
    floating:box('.floating-formatting'),main:box('.main-content'),files:box('.secondary-sidebar')}
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
