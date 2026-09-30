// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import ConversationSearch from './ConversationSearch.vue'
import { searchConversation } from '@/services/chatService'
const state = vi.hoisted(() => ({ workspace: null as unknown as { vaultId: string } }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => state.workspace }))
vi.mock('@/services/chatService', () => ({ searchConversation: vi.fn() }))
const hit = { message_id: 'old', position: 2, entry_index: 0, kind: 'text' as const, role: 'assistant', snippet: 'needle passage', created_at: '2026-09-30T00:00:00Z' }
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); state.workspace = reactive({ vaultId: 'one' }); vi.mocked(searchConversation).mockResolvedValue({ items: [hit], has_more: false }) })
afterEach(() => vi.useRealTimers())
it('debounces queries, pages results, emits stable locators and clears without navigation', async () => {
  const wrapper = mount(ConversationSearch, { props: { conversationId: 'chat' } })
  await wrapper.get('input').setValue('nee'); await wrapper.get('input').setValue('needle')
  vi.mocked(searchConversation).mockResolvedValueOnce({ items: [hit], has_more: true })
  await vi.advanceTimersByTimeAsync(200); await flushPromises()
  expect(searchConversation).toHaveBeenCalledTimes(1)
  await wrapper.get('.search-hit').trigger('click')
  expect(wrapper.emitted('locate')?.[0]).toEqual([hit, 'needle'])
  await wrapper.findAll('button').find(b => b.text() === '更多结果')!.trigger('click'); await flushPromises()
  expect(searchConversation).toHaveBeenLastCalledWith('chat', 'needle', 1)
  await wrapper.get('input').setValue('')
  expect(wrapper.emitted('clear')).toHaveLength(1)
  wrapper.unmount()
})
it('ignores responses after switching vaults and never mixes previous results', async () => {
  let resolve!: (value: { items: typeof hit[]; has_more: boolean }) => void
  vi.mocked(searchConversation).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  const wrapper = mount(ConversationSearch, { props: { conversationId: 'chat' } })
  await wrapper.get('input').setValue('needle'); await vi.advanceTimersByTimeAsync(200)
  state.workspace.vaultId = 'two'; await flushPromises(); resolve({ items: [hit], has_more: false }); await flushPromises()
  expect(wrapper.find('.search-hit').exists()).toBe(false)
  expect((wrapper.get('input').element as HTMLInputElement).value).toBe('')
  wrapper.unmount()
})
it('searches live text and real tool summaries without indexing reasoning or parameters', async () => {
  vi.mocked(searchConversation).mockResolvedValue({ items: [], has_more: false })
  const message = { message_id: 'live', conversation_id: 'chat', role: 'assistant' as const, created_at: '', content: 'needle', thinking: 'hidden needle', tool_calls: [{ tool_call_id: 't', name: 'rag.search', status: 'completed' as const, parameters: { secret: 'private' }, result: '{"title":"orchard"}' }] }
  const wrapper = mount(ConversationSearch, { props: { conversationId: 'chat', liveMessage: message } })
  await wrapper.get('input').setValue('检索'); await vi.advanceTimersByTimeAsync(200); await flushPromises()
  expect(wrapper.get('.search-hit').text()).toContain('orchard')
  await wrapper.get('.search-hit').trigger('click')
  expect((wrapper.emitted('locate')![0]![0] as typeof hit & { tool_call_id: string }).tool_call_id).toBe('t')
  await wrapper.get('input').setValue('private'); await vi.advanceTimersByTimeAsync(200); await flushPromises()
  expect(wrapper.find('.search-hit').exists()).toBe(false)
  wrapper.unmount()
})
