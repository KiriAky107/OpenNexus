// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MessageActivity from './MessageActivity.vue'
vi.mock('@/components/common/MarkdownContent.vue', () => ({ default: { props: ['source'], template: '<p data-text>{{ source }}</p>' } }))
vi.mock('./ToolActivity.vue', () => ({ default: { props: ['call'], template: '<section data-tool>{{ call.name }}</section>' } }))
describe('MessageActivity', () => {
  const base = { message_id: 'answer', conversation_id: 'chat', role: 'assistant' as const, content: 'firstlast', created_at: '',
    tool_calls: [{ tool_call_id: 'call', name: 'agent.start', parameters: {}, status: 'completed' as const }] }
  it('keeps real text/tool order and excludes reasoning', () => {
    const wrapper = mount(MessageActivity, { props: { message: { ...base, activity: [
      { type: 'text', text: 'first', sequence: 1 }, { type: 'thinking', text: 'private reasoning' },
      { type: 'tool', tool_call_id: 'call', sequence: 4 }, { type: 'text', text: 'last', sequence: 7 },
    ] } } })
    expect(wrapper.text()).toBe('firstagent.startlast')
    expect(wrapper.findAll('[data-tool]')).toHaveLength(1)
  })
  it('retains old history without manufacturing missing event order', () => {
    const wrapper = mount(MessageActivity, { props: { message: base } })
    expect(wrapper.findAll('[data-tool]')).toHaveLength(1)
    expect(wrapper.get('[data-text]').text()).toBe('firstlast')
  })
  it('does not create a tool card from model claims', () => {
    const wrapper = mount(MessageActivity, { props: { message: { ...base, tool_calls: [], content: 'Agent 已完成所有工作' } } })
    expect(wrapper.find('[data-tool]').exists()).toBe(false)
  })
})
