// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import LogsView from './LogsView.vue'
const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/apiClient', () => ({ default: { get } }))
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks() })
const page = (id: number, next: number | null) => ({ items: [{ id, timestamp: '2026-09-06T01:00:00Z', level: 'ERROR', source: 'vectors', event: 'embedding.failed', details: { error_code: 'LOCAL_CUDA_OOM' } }], next_cursor: next, sources: ['vectors'], pending: 0, dropped: 0, write_failures: 0, retention: 20000 })
it('loads older logs without paging away and applies filters', async () => {
  get.mockResolvedValueOnce(page(4, 4)).mockResolvedValueOnce(page(2, null)).mockResolvedValue(page(8, null))
  const wrapper = mount(LogsView)
  await flushPromises()
  expect(wrapper.get('details').classes()).toContain('ui-disclosure')
  await wrapper.findAll('button').find(button => button.text() === '向上滚动加载更早日志')!.trigger('click')
  await flushPromises()
  expect(get.mock.lastCall![1].params.before).toBe(4)
  expect(wrapper.findAll('details').map(row => row.attributes('data-log-id'))).toEqual(['2', '4'])
  await wrapper.get('input[maxlength="200"]').setValue('LOCAL_CUDA_OOM')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(get.mock.lastCall![1].params).toMatchObject({ before: undefined, q: 'LOCAL_CUDA_OOM' })
  wrapper.unmount()
})
it('follows at the bottom, pauses while reading, and loads history on scroll', async () => {
  vi.useFakeTimers()
  get.mockResolvedValue(page(10, 10))
  const wrapper = mount(LogsView)
  await flushPromises()
  const viewport = wrapper.get('.log-list').element as HTMLElement
  Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: 1000 })
  Object.defineProperty(viewport, 'clientHeight', { configurable: true, value: 200 })
  viewport.scrollTop = 800
  await wrapper.get('.log-list').trigger('scroll')
  get.mockResolvedValueOnce({ ...page(11, 10), items: [...page(11, 10).items, ...page(10, 10).items] })
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(wrapper.findAll('details').map(row => row.attributes('data-log-id'))).toEqual(['10', '11'])
  expect(viewport.scrollTop).toBe(1000)
  viewport.scrollTop = 400
  await wrapper.get('.log-list').trigger('scroll')
  const calls = get.mock.calls.length
  await vi.advanceTimersByTimeAsync(5000)
  expect(get).toHaveBeenCalledTimes(calls)
  get.mockResolvedValueOnce(page(9, null))
  viewport.scrollTop = 0
  await wrapper.get('.log-list').trigger('scroll')
  await flushPromises()
  expect(get.mock.lastCall![1].params.before).toBe(10)
  expect(wrapper.findAll('details').map(row => row.attributes('data-log-id'))).toEqual(['9', '10', '11'])
  wrapper.unmount()
})
it('surfaces storage loss and transport errors without polling after unmount', async () => {
  vi.useFakeTimers()
  get.mockResolvedValueOnce({ ...page(1, null), dropped: 3, write_failures: 1 }).mockRejectedValue(new Error('offline'))
  const wrapper = mount(LogsView)
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('3')
  await wrapper.get('input[type="checkbox"]').setValue(true)
  await vi.advanceTimersByTimeAsync(5000)
  expect(wrapper.text()).toContain('offline')
  wrapper.unmount()
  const calls = get.mock.calls.length
  await vi.advanceTimersByTimeAsync(10000)
  expect(get).toHaveBeenCalledTimes(calls)
})
