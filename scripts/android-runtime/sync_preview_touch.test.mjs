import assert from 'node:assert/strict'
import test from 'node:test'
import { admitPreview, previewExpression, touchPreview } from './sync_preview_touch.mjs'

const nonce = 'a326b9cd-165c-4444-b1c2-5b05b48f1994'
const target = { left: 34, top: 558, width: 335, height: 44, enabled: true, visible: true, hit: true }
const view = () => ({ observer: true, focused: true, visible: true, busy: false, width: 412, height: 915, targets: [{ ...target }] })
test('admission rejects occlusion, wrong observer, hidden pages, ambiguity and targets outside the real viewport', () => {
  assert.deepEqual(admitPreview(view()), { left: 34, top: 558, width: 335, height: 44 })
  for (const change of [{ observer: false }, { focused: false }, { visible: false }, { busy: true },
    { targets: [] }, { targets: [target, target] }, { width: NaN }]) assert.throws(() => admitPreview({ ...view(), ...change }))
  for (const change of [{ hit: false }, { enabled: false }, { visible: false }, { top: -1 },
    { top: 900 }, { height: 43 }, { width: 43 }, { left: Infinity }]) {
    assert.throws(() => admitPreview({ ...view(), targets: [{ ...target, ...change }] }))
  }
  assert.throws(() => previewExpression('unrelated-string'))
  assert.doesNotMatch(previewExpression(nonce), /\.click\(|\.value|invoke\(/)
})
test('one admitted browser touch pair uses stable original geometry and creates no JS click or IPC', async () => {
  let time = 0; const calls = [], trace = {}
  const cdp = async (method, params) => {
    calls.push([method, params]); return method === 'Runtime.evaluate' ? { result: { value: view() } } : {}
  }
  await touchPreview(cdp, nonce, trace, { now: () => time, sleep: async n => { time += n } })
  const input = calls.filter(([method]) => method !== 'Runtime.evaluate')
  assert.equal(input.length, 2)
  assert.deepEqual(input.map(([method, params]) => [method, params.type]), [
    ['Input.dispatchTouchEvent', 'touchStart'], ['Input.dispatchTouchEvent', 'touchEnd'],
  ])
  assert.deepEqual(input[0][1].touchPoints[0], { x: 201.5, y: 580, id: 1, radiusX: 1, radiusY: 1, force: 1 })
  assert.equal(trace.touchStart, 'accepted'); assert.equal(trace.touchEnd, 'accepted')
  assert.equal(trace.actual_UI_or_native_result_verified, false)
})
test('unknown dispatch results are retained without another touch or input fallback', async () => {
  for (const fail of ['touchStart', 'touchEnd']) {
    let time = 0; const calls = [], trace = {}
    const cdp = async (method, params) => {
      if (method === 'Runtime.evaluate') return { result: { value: view() } }
      calls.push(params.type); if (params.type === fail) throw Error('original transport result unknown')
      return {}
    }
    await assert.rejects(touchPreview(cdp, nonce, trace, { now: () => time, sleep: async n => { time += n } }))
    assert.deepEqual(calls, fail === 'touchStart' ? ['touchStart'] : ['touchStart', 'touchEnd'])
    assert.equal(trace[fail], 'attempted_without_confirmation')
  }
})
test('busy or moving original geometry receives no input before a fresh stable interval', async () => {
  let time = 0, reads = 0; const input = [], trace = {}
  const cdp = async (method, params) => {
    if (method === 'Runtime.evaluate') {
      const value = view(); reads++
      if (reads <= 2) value.busy = true
      if (reads === 4) value.targets[0].top += 8
      return { result: { value } }
    }
    input.push({ at: time, type: params.type }); return {}
  }
  await touchPreview(cdp, nonce, trace, { now: () => time, sleep: async n => { time += n } })
  assert.ok(reads >= 8); assert.ok(input[0].at >= 640)
  assert.deepEqual(input.map(x => x.type), ['touchStart', 'touchEnd'])
})
