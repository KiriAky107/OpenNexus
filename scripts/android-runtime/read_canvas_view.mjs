// A single read-only CDP request at the exact owned ADB-forwarded WebView.
import { canvasViewExpression } from './canvas_view_expression.mjs'

try {
  const target = new URL(process.argv[2]), dispatch = process.argv[3], stage = process.argv[4]
  if (target.protocol !== 'ws:' || target.hostname !== '127.0.0.1'
      || !/^\d+$/.test(target.port) || Number(target.port)<1024 || Number(target.port)>65535
      || !/^\/devtools\/page\/[A-Za-z0-9_-]+$/.test(target.pathname) || target.search || target.hash
      || target.username || target.password) throw Error('CANVAS_VIEW_SCOPE_REFUSED')
  const expression = canvasViewExpression(dispatch, stage)
  const value = await new Promise((resolve, reject) => {
    const socket = new WebSocket(target)
    const timer = setTimeout(() => { socket.close(); reject(Error('CANVAS_VIEW_READ_TIMEOUT')) }, 12000)
    socket.addEventListener('open', () => socket.send(JSON.stringify({id:1,method:'Runtime.evaluate',params:{expression,returnByValue:true}})))
    socket.addEventListener('message', event => {
      if (typeof event.data!=='string' || event.data.length>65536) { clearTimeout(timer);socket.close();reject(Error('CANVAS_VIEW_RESULT_REFUSED'));return }
      let message
      try { message=JSON.parse(event.data) } catch { clearTimeout(timer);socket.close();reject(Error('CANVAS_VIEW_RESULT_REFUSED'));return }
      if(message.id!==1)return
      clearTimeout(timer);socket.close()
      if(message.error || message.result?.exceptionDetails || typeof message.result?.result?.value!=='string'
          || message.result.result.value.length>16384) reject(Error('CANVAS_VIEW_RESULT_REFUSED'))
      else { try { resolve(JSON.parse(message.result.result.value)) } catch { reject(Error('CANVAS_VIEW_RESULT_REFUSED')) } }
    })
    socket.addEventListener('error', () => { clearTimeout(timer);reject(Error('CANVAS_VIEW_READ_UNAVAILABLE')) })
  })
  process.stdout.write(JSON.stringify(value)+'\n')
} catch(error) {
  const code=/^CANVAS_VIEW_[A-Z_]+$/.test(error?.message) ? error.message : 'CANVAS_VIEW_READ_UNAVAILABLE'
  process.stdout.write(JSON.stringify({passed:false,code,raw_error_returned:false})+'\n')
  process.exitCode=1
}
