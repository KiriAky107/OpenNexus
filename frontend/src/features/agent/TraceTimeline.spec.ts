// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { AgentEvent, AgentEventType } from '@/contracts'
import TraceTimeline from './TraceTimeline.vue'

let sequence = 0

function event(type: AgentEventType, data: Record<string, unknown> = {}): AgentEvent {
  return {
    event: type,
    sequence: ++sequence,
    run_id: 'run-1',
    data,
    timestamp: '2026-01-01T00:00:00.000Z',
  }
}

/** 一次带工具调用的运行：模型调用有子节点，Usage / 引用是叶子。 */
function sampleEvents(): AgentEvent[] {
  return [
    event('RunStarted'),
    event('ModelCallStarted', { model_call_id: 'mc-1', model: 'mock-1' }),
    event('ModelCallCompleted', { model_call_id: 'mc-1', duration_ms: 800 }),
    event('ToolCall', { tool_call_id: 'tc-1', name: 'read_note', parent_model_call_id: 'mc-1' }),
    event('ToolResult', { tool_call_id: 'tc-1', name: 'read_note', success: true, parent_model_call_id: 'mc-1' }),
    event('Citation', { file_path: 'notes/a.md', block_id: 'blk-1', heading_path: 'A > B' }),
    event('RunCompleted'),
  ]
}

function mountTree(events: AgentEvent[]) {
  const wrapper = mount(TraceTimeline, { props: { events } })
  return wrapper
}

async function switchToTree(wrapper: ReturnType<typeof mountTree>) {
  const treeButton = wrapper.findAll('button').find((b) => b.text() === '树形')
  await treeButton!.trigger('click')
  return wrapper
}

describe('TraceTimeline 树形视图', () => {
  it('filters errors while retaining tree ancestors and final tool data', async () => {
    const events = sampleEvents()
    const result = events.find(item => item.event === 'ToolResult')!
    result.data.success = false
    result.data.error_code = 'TIMEOUT'
    const wrapper = mountTree(events)
    await wrapper.get('input[type="checkbox"]').setValue(true)
    expect(wrapper.findAll('.event-card')).toHaveLength(1)
    await switchToTree(wrapper)
    expect(wrapper.findAll('.tree-node')).toHaveLength(2)
    expect(wrapper.text()).toContain('TIMEOUT')
    await wrapper.get('[aria-label="搜索执行轨迹"]').setValue('no-match')
    expect(wrapper.text()).toContain('没有匹配的事件')
    wrapper.unmount()
  })
  it('filters a tool including its result and keeps citation navigation usable', async () => {
    const wrapper = mountTree(sampleEvents())
    await wrapper.get('[aria-label="工具筛选"]').setValue('read_note')
    expect(wrapper.findAll('.event-card')).toHaveLength(2)
    await wrapper.findAll('button').find(button => button.text() === '清除筛选')!.trigger('click')
    await wrapper.get('[aria-label="事件类型"]').setValue('Citation')
    await wrapper.get('.event-citation').trigger('click')
    expect(wrapper.emitted('open-citation')).toHaveLength(1)
    wrapper.unmount()
  })
  it('叶子节点点击后能看到自己的数据', async () => {
    // 回归：之前行的 click 是 `children.length && toggleExpand(id)`，
    // 而详情 v-if 又要求 children.length === 0 —— 两个条件互斥，
    // 叶子节点永远打不开详情。
    const wrapper = await switchToTree(mountTree(sampleEvents()))

    const rows = wrapper.findAll('.node-row')
    const citationRow = rows.find((row) => row.text().includes('引用来源'))
    expect(citationRow).toBeTruthy()
    expect(wrapper.find('.node-detail').exists()).toBe(false)

    await citationRow!.trigger('click')

    const detail = wrapper.find('.node-detail')
    expect(detail.exists()).toBe(true)
    expect(detail.text()).toContain('notes/a.md')
  })

  it('有子节点的节点也能查看自己的数据，不只是展开子树', async () => {
    const wrapper = await switchToTree(mountTree(sampleEvents()))

    const modelRow = wrapper.findAll('.node-row').find((row) => row.text().includes('模型调用'))
    await modelRow!.trigger('click')

    const detail = wrapper.find('.node-detail')
    expect(detail.exists()).toBe(true)
    expect(detail.text()).toContain('mc-1')
  })

  it('展开箭头只切子树，不会连带打开详情', async () => {
    const wrapper = await switchToTree(mountTree(sampleEvents()))

    // 初始只有顶层节点：运行开始、模型调用、引用、运行完成
    expect(wrapper.findAll('.node-row')).toHaveLength(4)

    const arrow = wrapper.find('.expand-icon:not(.placeholder)')
    expect(arrow.exists()).toBe(true)
    await arrow.trigger('click')

    // 子节点出现，但没有任何详情面板被打开
    expect(wrapper.findAll('.node-row')).toHaveLength(5)
    expect(wrapper.text()).toContain('工具调用：read_note')
    expect(wrapper.find('.node-detail').exists()).toBe(false)
  })

  it('键盘 Enter 与空格可以打开详情', async () => {
    const wrapper = await switchToTree(mountTree(sampleEvents()))
    const row = wrapper.findAll('.node-row').find((r) => r.text().includes('运行开始'))!

    await row.trigger('keydown.enter')
    expect(wrapper.find('.node-detail').exists()).toBe(true)

    await row.trigger('keydown.space')
    expect(wrapper.find('.node-detail').exists()).toBe(false)
  })

  it('行的 aria-expanded 跟随详情开合', async () => {
    const wrapper = await switchToTree(mountTree(sampleEvents()))
    const row = wrapper.findAll('.node-row').find((r) => r.text().includes('运行开始'))!

    expect(row.attributes('aria-expanded')).toBe('false')
    await row.trigger('click')
    expect(row.attributes('aria-expanded')).toBe('true')
  })

  it('引用节点带「定位」按钮，点击后抛出 open-citation 且不打开详情', async () => {
    const wrapper = await switchToTree(mountTree(sampleEvents()))

    const locate = wrapper.find('.node-locate')
    expect(locate.exists()).toBe(true)
    await locate.trigger('click')

    const emitted = wrapper.emitted('open-citation')
    expect(emitted).toHaveLength(1)
    expect((emitted![0][0] as Record<string, unknown>).file_path).toBe('notes/a.md')
    // @click.stop 生效，行的详情不该被顺带打开
    expect(wrapper.find('.node-detail').exists()).toBe(false)
  })

  it('引用缺少 file_path 时不显示定位按钮', async () => {
    const wrapper = await switchToTree(mountTree([event('Citation', { heading_path: 'A' })]))

    expect(wrapper.find('.node-locate').exists()).toBe(false)
  })
})

describe('TraceTimeline 时间线视图', () => {
  it('Usage 卡片读后端真实字段 token_usage', () => {
    // 后端只发累计的 token_usage（runtime.py），没有 input/output/total_tokens。
    const wrapper = mountTree([event('Usage', { token_usage: 1024 })])

    expect(wrapper.find('.event-usage').text()).toContain('1024')
  })

  it('Usage 缺字段时显示占位符而不是 undefined', () => {
    const wrapper = mountTree([event('Usage', {})])

    const text = wrapper.find('.event-usage').text()
    expect(text).toContain('-')
    expect(text).not.toContain('undefined')
  })

  it('点击引用卡片抛出 open-citation', async () => {
    const wrapper = mountTree([event('Citation', { file_path: 'notes/a.md', heading_path: 'A' })])

    await wrapper.find('.event-citation').trigger('click')

    expect(wrapper.emitted('open-citation')).toHaveLength(1)
  })

  it('工具调用统计按 ToolResult 显示最终状态，不停在 running', () => {
    const wrapper = mountTree([
      event('ToolCall', { tool_call_id: 'tc-9', name: 'write_note' }),
      event('ToolResult', { tool_call_id: 'tc-9', name: 'write_note', success: false, error_code: 'TOOL_DENIED' }),
    ])

    const item = wrapper.find('.tool-call-item')
    expect(item.classes()).toContain('error')
    expect(item.text()).toContain('失败')
  })

  it('没有事件时显示等待态', () => {
    const wrapper = mountTree([])

    expect(wrapper.find('.empty-state').text()).toContain('等待执行轨迹')
  })
})
