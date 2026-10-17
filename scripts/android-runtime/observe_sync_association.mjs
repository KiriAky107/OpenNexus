import { installAssociationObserver, readAssociationObserver } from './sync_association_observer.mjs'

let target
try { target = new URL(process.argv[2]) } catch { throw Error('Refusing an unrelated owned observer scope') }
const mode = process.argv[3], id = process.argv[4]
if (target.protocol !== 'ws:' || target.hostname !== '127.0.0.1' || !/^\d+$/.test(target.port)
    || Number(target.port) < 1024 || Number(target.port) > 65535 || target.username || target.password
    || !/^\/devtools\/page\/[A-Za-z0-9_-]+$/.test(target.pathname) || target.search || target.hash
    || !['install', 'read'].includes(mode)
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)) {
  throw Error('Refusing an unrelated owned observer scope')
}
const selected = mode === 'install' ? installAssociationObserver : readAssociationObserver
const expression = `JSON.stringify((${selected.toString()})(${JSON.stringify(id)}))`
const result = await new Promise((resolve, reject) => {
  const socket = new WebSocket(target)
  const timer = setTimeout(() => { socket.close(); reject(Error('Owned observer timed out')) }, 12000)
  socket.addEventListener('open', () => socket.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate',
    params: { expression, returnByValue: true } })))
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data)
    if (message.id !== 1) return
    clearTimeout(timer); socket.close()
    if (message.error || message.result?.exceptionDetails || typeof message.result?.result?.value !== 'string') {
      reject(Error('Owned observer unavailable'))
    } else resolve(JSON.parse(message.result.result.value))
  })
  socket.addEventListener('error', () => { clearTimeout(timer); reject(Error('Owned observer connection failed')) })
})
process.stdout.write(JSON.stringify(result) + '\n')
