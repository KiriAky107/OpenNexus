// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MessageActivity from './MessageActivity.vue'
vi.mock('@/components/common/MarkdownContent.vue', () => ({ default: { props: ['source'], template: '<p data-text>{{ source }}</p>' } }))
vi.mock('./ToolActivity.vue', () => ({ default: { props: ['call'], template: '<section data-tool>{{ call.name }}</section>' } }))
describe('MessageActivity', () => {
  const base = { message_id: 'answer', conversation_id: 'chat', role: 'assistant' as const, content: 'firstlast', created_at: '',
    tool_calls: [{ tool_call_id: 'call', name: 'agent.start', parameters: {}, status: 'completed' as const }] }
  it('interleaves reasoning, tool calls and answer text in event order', () => {
    const wrapper = mount(MessageActivity, { props: { message: { ...base, activity: [
      { type: 'thinking', text: 'first thought', sequence: 1 }, { type: 'text', text: 'first', sequence: 2 },
      { type: 'tool', tool_call_id: 'call', sequence: 4 }, { type: 'thinking', text: 'second thought', sequence: 5 },
      { type: 'text', text: 'last', sequence: 7 },
    ] } } })
    expect(wrapper.findAll('.thinking')).toHaveLength(2)
    expect(wrapper.findAll('.thinking').map(item => item.element.textContent)).toEqual(['思考过程first thought', '思考过程second thought'])
    expect([...wrapper.element.querySelectorAll('.thinking, [data-tool], [data-text]')].map(item => item.textContent)).toEqual([
      '思考过程first thought', 'first', 'agent.start', '思考过程second thought', 'last',
    ])
    expect(wrapper.findAll('[data-tool]')).toHaveLength(1)
  })
  it('retains old history without manufacturing missing event order', () => {
    const wrapper = mount(MessageActivity, { props: { message: base } })
    expect(wrapper.findAll('[data-tool]')).toHaveLength(1)
    expect(wrapper.get('[data-text]').text()).toBe('firstlast')
  })
  it('shows aggregate legacy reasoning once when no reasoning events were stored', () => {
    const wrapper = mount(MessageActivity, { props: { message: { ...base, thinking: 'older thought', activity: [{ type: 'tool', tool_call_id: 'call' }] } } })
    expect(wrapper.findAll('.thinking')).toHaveLength(1)
    expect([...wrapper.element.querySelectorAll('.thinking, [data-tool], [data-text]')].map(item => item.textContent)).toEqual([
      '思考过程older thought', 'agent.start', 'firstlast',
    ])
    expect(wrapper.get('.legacy-order-note').text()).toContain('未记录正文片段的发生顺序')
    expect(wrapper.get('[data-text]').text()).toBe('firstlast')
  })
  it('does not create a tool card from model claims', () => {
    const wrapper = mount(MessageActivity, { props: { message: { ...base, tool_calls: [], content: 'Agent 已完成所有工作' } } })
    expect(wrapper.find('[data-tool]').exists()).toBe(false)
  })
})
