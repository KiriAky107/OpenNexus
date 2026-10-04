// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { renderMarkdown } from '@/utils/markdown'
import MarkdownContent from './MarkdownContent.vue'

vi.mock('@/utils/markdown', () => ({ renderMarkdown: vi.fn() }))
vi.mock('./DiagramInteractions.vue', () => ({ default: { template: '<div><slot /></div>' } }))
const pending: { source: string; signal?: AbortSignal; resolve: (html: string) => void }[] = []
beforeEach(() => {
  setActivePinia(createPinia()); vi.useFakeTimers(); pending.length = 0
  vi.mocked(renderMarkdown).mockReset().mockImplementation((source, options) => options?.previewOnly
    ? Promise.resolve(`<p>plain ${source}</p>`)
    : new Promise(resolve => { pending.push({ source, signal: options?.signal, resolve }) }))
})
afterEach(() => { vi.useRealTimers() })

it('shows streaming text, coalesces intermediate versions and ignores stale colors', async () => {
  const wrapper = mount(MarkdownContent, { props: { source: 'initial', streaming: true } })
  await flushPromises()
  expect(wrapper.text()).toBe('plain initial')
  for (let index = 0; index < 20; index++) await wrapper.setProps({ source: `revision-${index}` })
  expect(pending).toHaveLength(1)
  expect(pending[0]!.signal?.aborted).toBe(true)
  await vi.advanceTimersByTimeAsync(40); await flushPromises()
  expect(pending).toHaveLength(2)
  expect(pending[1]!.source).toBe('revision-19')
  expect(wrapper.text()).toBe('plain revision-19')
  pending[0]!.resolve('<p>stale</p>'); await flushPromises()
  expect(wrapper.text()).toBe('plain revision-19')
  pending[1]!.resolve('<p>latest colored</p>'); await flushPromises()
  expect(wrapper.text()).toBe('latest colored')
  wrapper.unmount()
})

it('renders the final stream snapshot immediately and cancels work when unmounted', async () => {
  const wrapper = mount(MarkdownContent, { props: { source: 'first', streaming: true } })
  await flushPromises()
  await wrapper.setProps({ source: 'last' })
  await wrapper.setProps({ streaming: false }); await flushPromises()
  expect(pending.at(-1)!.source).toBe('last')
  expect(pending).toHaveLength(2)
  wrapper.unmount()
  expect(pending.at(-1)!.signal?.aborted).toBe(true)
  await vi.advanceTimersByTimeAsync(100)
  expect(pending).toHaveLength(2)
})
