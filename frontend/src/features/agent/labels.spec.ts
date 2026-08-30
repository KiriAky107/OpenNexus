import { describe, expect, it } from 'vitest'
import {
  eventLabel,
  localizeDetails,
  permissionLabel,
  runStatusLabel,
  toolDescription,
  toolLabel,
} from './labels'

describe('智能体页面中文标签', () => {
  it('转换运行状态和事件名称', () => {
    expect(runStatusLabel('waiting_permission')).toBe('等待授权')
    expect(eventLabel('ToolCall')).toBe('调用工具')
  })

  it('转换工具、权限和工具说明', () => {
    expect(toolLabel('notes.search')).toBe('搜索笔记')
    expect(permissionLabel('notes.write')).toBe('修改笔记')
    expect(toolDescription('math.add', 'fallback')).toContain('两个数')
    expect(toolLabel('custom.tool')).toBe('custom.tool')
  })

  it('递归转换事件详情中的键名、状态和布尔值', () => {
    expect(localizeDetails({
      tool_call_id: 'call-1',
      success: true,
      result: { status: 'completed' },
    })).toEqual({
      '工具调用 ID': 'call-1',
      '是否成功': '是',
      '结果': { '状态': '已完成' },
    })
  })
})
