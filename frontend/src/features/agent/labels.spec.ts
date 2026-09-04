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
  it('按 MCP 远程工具名匹配中文，不依赖服务器 ID', () => {
    for (const server of ['9ca7ee21603a', 'another-server']) {
      expect(toolLabel(`mcp.${server}.web_search`)).toBe('网页搜索')
      expect(toolLabel(`mcp.${server}.understand_image`)).toBe('图像理解')
      expect(toolDescription(`mcp.${server}.web_search`, 'Search the web')).toContain('搜索关键词')
    }
    expect(toolLabel('text.uppercase')).toBe('文本转大写')
    expect(toolDescription('text.uppercase', 'Convert input text to uppercase.')).toContain('大写')
  })

  it('保留服务端中文，未知工具不编造翻译或套用内置工具语义', () => {
    expect(toolDescription('mcp.server.web_search', '仅搜索指定站点。')).toBe('仅搜索指定站点。')
    expect(toolDescription('mcp.server.custom_action', 'Private action')).toContain('暂无中文说明')
    expect(toolLabel('mcp.server.notes.delete')).toBe('MCP 工具 · notes.delete')
  })
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
