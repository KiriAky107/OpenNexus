// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { revealSearchTarget } from './revealSearchTarget'

let callbacks: Map<number, FrameRequestCallback>, nextFrame: number, now: number
beforeEach(() => {
  callbacks = new Map(); nextFrame = 0; now = 0
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callbacks.set(++nextFrame, callback); return nextFrame })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { callbacks.delete(id) })
})
afterEach(() => { document.body.replaceChildren(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })
function frame() {
  now += 16
  const pending = [...callbacks.values()]; callbacks.clear()
  for (const callback of pending) callback(performance.now())
}
function fixture(position = 1000) {
  const viewport = document.createElement('div'), target = document.createElement('span')
  viewport.append(target); document.body.append(viewport)
  Object.defineProperties(viewport, { clientHeight: { value: 200 }, clientTop: { value: 0 }, scrollHeight: { value: 6000 } })
  viewport.getBoundingClientRect = () => ({ top: 10, height: 200 } as DOMRect)
  target.getBoundingClientRect = () => ({ top: position - viewport.scrollTop, height: 20 } as DOMRect)
  return { viewport, target, shift: (pixels: number) => { position += pixels } }
}
it('keeps the matched line centered after a delayed 2100px chunk expansion, then releases the viewport', () => {
  const { viewport, target, shift } = fixture()
  revealSearchTarget(target, viewport)
  expect(viewport.scrollTop).toBe(900)
  shift(2100); frame()
  expect(viewport.scrollTop).toBe(3000)
  for (let i = 0; i < 24; i++) frame()
  expect(callbacks.size).toBe(0)
  shift(500); frame()
  expect(viewport.scrollTop).toBe(3000)
})
it('corrects a late layout shift after more than five initially quiet frames', () => {
  const { viewport, target, shift } = fixture()
  revealSearchTarget(target, viewport)
  for (let i = 0; i < 9; i++) frame()
  shift(21); frame()
  expect(viewport.scrollTop).toBe(921)
  for (let i = 0; i < 24; i++) frame()
  expect(callbacks.size).toBe(0)
})
it.each(['wheel', 'touchstart', 'pointerdown'])('lets %s stop placement immediately', event => {
  const { viewport, target, shift } = fixture()
  revealSearchTarget(target, viewport)
  viewport.dispatchEvent(new Event(event)); shift(2100); frame()
  expect(viewport.scrollTop).toBe(900)
  expect(callbacks.size).toBe(0)
})
it('lets keyboard scrolling and a replacement search cancel pending geometry work', () => {
  const first = fixture(), second = fixture()
  revealSearchTarget(first.target, first.viewport)
  first.viewport.dispatchEvent(new KeyboardEvent('keydown', { key: 'PageDown' }))
  const cancel = revealSearchTarget(second.target, second.viewport)
  cancel(); first.shift(200); second.shift(200); frame()
  expect(first.viewport.scrollTop).toBe(900)
  expect(second.viewport.scrollTop).toBe(900)
  expect(callbacks.size).toBe(0)
})
it('does not chase a detached target or keep a live changing response in a scroll loop', () => {
  const first = fixture(), second = fixture()
  revealSearchTarget(first.target, first.viewport); first.target.remove(); frame()
  expect(callbacks.size).toBe(0)
  revealSearchTarget(second.target, second.viewport)
  for (let i = 0; i < 130; i++) { second.shift(10); frame() }
  expect(callbacks.size).toBe(0)
})
it('accepts a viewport edge when the result cannot be centered', () => {
  const { viewport, target } = fixture(20)
  revealSearchTarget(target, viewport)
  for (let i = 0; i < 24; i++) frame()
  expect(viewport.scrollTop).toBe(0)
  expect(callbacks.size).toBe(0)
})
it('releases listeners and a queued frame when the window stops producing frames', () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const { viewport, target } = fixture()
  revealSearchTarget(target, viewport)
  expect(callbacks.size).toBe(1)
  vi.advanceTimersByTime(2000)
  expect(callbacks.size).toBe(0)
})
