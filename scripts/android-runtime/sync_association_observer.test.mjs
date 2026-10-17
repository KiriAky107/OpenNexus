import assert from 'node:assert/strict'
import vm from 'node:vm'
import test from 'node:test'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { installAssociationObserver, readAssociationObserver } from './sync_association_observer.mjs'

const id = '11111111-2222-4333-8444-555555555555'
function scope(invoke, writable = true) {
  const listeners = new Map(), internal = {}
  Object.defineProperty(internal, 'invoke', { value: invoke, writable })
  const app = { __vue_app__: {}, contains: () => true }
  const context = vm.createContext({ window: { __TAURI_INTERNALS__: internal }, performance: { now: () => 1 },
    document: { querySelector: selector => selector === '#app' ? app : { getAttribute: () => 'false' },
      addEventListener: (type, listener) => listeners.set(type, listener) }, Error })
  const install = () => vm.runInContext(`(${installAssociationObserver.toString()})(${JSON.stringify(id)})`, context)
  const read = () => JSON.parse(vm.runInContext(`JSON.stringify((${readAssociationObserver.toString()})(${JSON.stringify(id)}))`, context))
  return { context, listeners, internal, install, read }
}

test('passive observation retains the exact original receiver, arguments and promise', async () => {
  const result = Promise.resolve({ kind: 'preview', privateContent: 'PRIVATE_NOTE' }), calls = []
  const own = scope(function (...args) { calls.push({ receiver: this, args }); return result })
  own.install()
  const args = { request: { action: 'preview', expected: { account: 'PRIVATE_ACCOUNT' } } }, options = { x: 1 }
  const receiver = { original: true }
  assert.equal(own.internal.invoke.call(receiver, 'mobile_sync_association', args, options), result)
  await result
  assert.equal(calls.length, 1); assert.equal(calls[0].receiver, receiver)
  assert.equal(calls[0].args[1], args); assert.equal(calls[0].args[2], options)
  const observed = own.read()
  assert.equal(observed.calls[0].replyKind, 'preview')
  assert.equal(observed.calls[0].outcome, 'fulfilled')
  assert.doesNotMatch(JSON.stringify(observed), /PRIVATE_/)
})

test('only public button events and enum errors are retained, never inputs or authentication payloads', async () => {
  const secret = 'PRIVATE_PASSWORD_OR_NOTE'
  const rejected = Promise.reject(new Error(secret))
  const own = scope(() => rejected)
  own.install()
  const button = name => ({ textContent: name, disabled: false })
  own.listeners.get('click')({ target: { closest: () => button('Preview merge') }, isTrusted: true })
  own.listeners.get('click')({ target: { closest: () => button(secret) }, isTrusted: true })
  own.listeners.get('click')({ target: { closest: () => null, get value() { throw Error('Input values must not be read') } } })
  const auth = { get request() { throw Error('Auth arguments must not be inspected') } }
  assert.equal(own.internal.invoke('mobile_sync_auth', auth), rejected)
  const same = own.internal.invoke('mobile_sync_association', { request: { action: 'preview', password: secret } })
  await assert.rejects(same)
  const observed = own.read()
  assert.equal(observed.events.length, 1); assert.equal(observed.events[0].trusted, true)
  assert.equal(observed.calls[0].errorCode, 'OTHER_ERROR')
  assert.doesNotMatch(JSON.stringify(observed), /PRIVATE_|Input values/)
})

test('bounded original history survives repeated admission and readonly invoke bindings are not bypassed', async () => {
  const own = scope(() => Promise.resolve({ kind: 'status' }))
  own.install()
  for (let n = 0; n < 50; n++) {
    own.listeners.get('click')({ target: { closest: () => ({ textContent: 'Preview merge', disabled: false }) } })
    await own.internal.invoke('mobile_sync_association', { request: { action: 'status' } })
  }
  assert.equal(own.install().alreadyInstalled, true)
  const observed = own.read()
  assert.equal(observed.events.length, 32); assert.equal(observed.eventOverflow, 18)
  assert.equal(observed.calls.length, 16); assert.equal(observed.callOverflow, 34)
  const frozen = scope(() => Promise.resolve({ kind: 'status' }), false)
  assert.equal(frozen.install().callsAvailable, false)
  assert.equal(frozen.read().calls.length, 0)
})

test('CLI refuses unrelated endpoints and redacts invalid URL input before any connection', () => {
  const cli = fileURLToPath(new URL('./observe_sync_association.mjs', import.meta.url))
  for (const url of ['ws://unrelated.invalid:19005/devtools/page/a',
    'ws://PRIVATE_USER:PRIVATE_PASSWORD@127.0.0.1:19005/devtools/page/a', 'not-a-url-PRIVATE_PASSWORD',
    'ws://127.0.0.1:80/devtools/page/a', 'ws://127.0.0.1:19005/devtools/page/a?auth=PRIVATE_PASSWORD']) {
    const result = spawnSync(process.execPath, ['--max-old-space-size=128', cli, url, 'install', id], {encoding: 'utf8'})
    assert.notEqual(result.status, 0)
    assert.match(result.stderr, /Refusing an unrelated owned observer scope/)
    assert.doesNotMatch(result.stdout + result.stderr, /PRIVATE_/)
  }
})
