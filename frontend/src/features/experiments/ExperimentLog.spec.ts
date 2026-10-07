// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import ExperimentLog from './ExperimentLog.vue'
import { LOG_WINDOW_SIZE, logBoundary, logChunks } from './experimentLogWindow'
import type { StreamLog } from '@/services/experimentService'

let wrapper: VueWrapper | undefined
function stream(text: string, extra: Partial<StreamLog> = {}): StreamLog {
  const bytes = new TextEncoder().encode(text).length
  return { text, bytes_seen: bytes, retained_bytes: bytes, truncated: false, invalid_utf8: false, complete: true, read_error: false, ...extra }
}
function start(text: string, extra: Partial<StreamLog> = {}) {
  return wrapper = mount(ExperimentLog, { props: { name: 'stdout', stream: stream(text, extra) } })
}
function action(name: string) { return wrapper!.get(`[data-log-action="${name}"]`) }
function preview() { return wrapper!.get('.log-preview') }
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals() })

it('bounds the DOM, escapes output and copies all available text rather than its visible window', async () => {
  const text = '<img src=x onerror=alert(1)>\r\n' + '中文😀 #%\r\n'.repeat(8_000)
  const copy = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('navigator', { clipboard: { writeText: copy } })
  const view = start(text)
  expect(preview().element.textContent!.length).toBeLessThanOrEqual(LOG_WINDOW_SIZE + 1)
  expect(view.findAll('[data-log-chunk]').length).toBeLessThanOrEqual(13)
  expect(view.find('[data-log-notice="window"]').exists()).toBe(true)
  expect(view.find('[data-log-notice="retention"]').exists()).toBe(false)
  await action('first').trigger('click')
  expect(preview().element.textContent).toContain('<img src=x onerror=alert(1)>')
  expect(view.find('img').exists()).toBe(false)
  await action('copy').trigger('click')
  expect(copy).toHaveBeenCalledExactlyOnceWith(text)
})

it('lets every retained character be read across pages without splitting Unicode pairs', async () => {
  const text = ('x'.repeat(2_047) + '😀\r\n').repeat(50)
  start(text)
  await action('first').trigger('click')
  let combined = '', pages = 0
  while (true) {
    const part = preview().element.textContent!
    const roundtrip = (value: string) => new TextDecoder().decode(new TextEncoder().encode(value))
    expect(roundtrip(part)).toBe(part)
    for (const chunk of wrapper!.findAll('[data-log-chunk]')) expect(roundtrip(chunk.element.textContent!)).toBe(chunk.element.textContent)
    combined += part; pages++
    if (action('next').attributes('disabled') !== undefined) break
    await action('next').trigger('click')
    if (pages > 10) throw Error('Log pagination failed to terminate')
  }
  expect(combined).toBe(text)
  expect(pages).toBeGreaterThan(1)
})

it('reuses aligned chunk nodes while the retained end grows', async () => {
  const text = 'a'.repeat(40_000)
  start(text, { complete: false })
  const previous = wrapper!.findAll('[data-log-chunk]').slice(1, -1).map(chunk => ({ key: chunk.attributes('data-log-chunk'), element: chunk.element }))
  await wrapper!.setProps({ stream: stream(text + 'new output', { complete: false }) })
  for (const chunk of previous) expect(wrapper!.get(`[data-log-chunk="${chunk.key}"]`).element).toBe(chunk.element)
  expect(preview().element.textContent).toBe((text + 'new output').slice(-LOG_WINDOW_SIZE))
})

it('freezes a reader selection during incoming output and resumes only when requested', async () => {
  const text = 'a'.repeat(40_000)
  start(text, { complete: false })
  await preview().trigger('pointerdown')
  const retainedView = preview().element.textContent
  await wrapper!.setProps({ stream: stream(text + 'new output', { complete: false }) })
  expect(preview().element.textContent).toBe(retainedView)
  expect(action('follow').attributes('aria-pressed')).toBe('false')
  await action('follow').trigger('click')
  expect(preview().element.textContent).toContain('new output')
  await action('first').trigger('click')
  await wrapper!.setProps({ stream: stream(text + 'new outputlater') })
  expect(preview().attributes('data-start')).toBe('0')
  await action('next').trigger('click')
  expect(Number(preview().attributes('data-start'))).toBe(LOG_WINDOW_SIZE)
})

it('distinguishes discarded bytes, a limited snapshot, invalid encoding and read failures', async () => {
  start('retained prefix', { bytes_seen: 1_000_000, retained_bytes: 16, truncated: true, display_truncated: true, invalid_utf8: true, read_error: true })
  expect(wrapper!.get('[data-log-notice="retention"]').text()).toContain('未保存')
  expect(wrapper!.get('[data-log-notice="snapshot"]').text()).toContain('部分')
  expect(wrapper!.find('[data-log-notice="window"]').exists()).toBe(false)
  expect(wrapper!.text()).toContain('1000000 B')
  expect(wrapper!.text()).toContain('不是原始字节')
  expect(wrapper!.text()).toContain('读取失败')
  expect(action('copy').text()).toContain('已保留文本')
})

it('keeps retained text accessible when the clipboard fails and resets a replaced snapshot', async () => {
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(Error('denied')) } })
  start('a'.repeat(40_000))
  await action('first').trigger('click')
  await action('next').trigger('click')
  await action('copy').trigger('click')
  expect(wrapper!.get('[role="status"]').text()).toContain('复制失败')
  await wrapper!.setProps({ stream: stream('new run') })
  expect(preview().element.textContent).toBe('new run')
  expect(action('follow').attributes('aria-pressed')).toBe('true')
})

it('handles pair boundaries at exact window and chunk edges and empty text', () => {
  const text = 'x'.repeat(LOG_WINDOW_SIZE - 1) + '😀end'
  const boundary = logBoundary(text, LOG_WINDOW_SIZE)
  expect(boundary).toBe(LOG_WINDOW_SIZE - 1)
  expect(logChunks(text, 0, boundary).map(chunk => chunk.text).join('') + logChunks(text, boundary, text.length).map(chunk => chunk.text).join('')).toBe(text)
  expect(logChunks('', 0, 0)).toEqual([])
})
