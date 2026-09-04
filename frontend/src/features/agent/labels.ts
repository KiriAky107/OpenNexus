import type { AgentEventType, AgentRunStatus } from '@/contracts'

const runStatusLabels: Record<AgentRunStatus, string> = {
  queued: '排队中',
  running: '运行中',
  waiting_permission: '等待授权',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const eventLabels: Record<AgentEventType, string> = {
  RunStarted: '运行开始',
  TextDelta: '回复内容',
  ThinkingDelta: '思考过程',
  ToolCall: '调用工具',
  ToolResult: '工具结果',
  PermissionRequired: '请求权限',
  Usage: '用量统计',
  Citation: '引用来源',
  ModelCallStarted: '模型调用开始',
  ModelCallCompleted: '模型调用完成',
  ModelCallFailed: '模型调用失败',
  PermissionResolved: '权限已处理',
  RunCompleted: '运行完成',
  RunFailed: '运行失败',
  RunCancelled: '运行取消',
}

const toolLabels: Record<string, string> = {
  'system.echo': '回显测试',
  'math.add': '数值相加',
  'notes.search': '搜索笔记',
  'rag.search': '知识检索',
  'notes.read': '读取笔记',
  'notes.create': '创建笔记',
  'notes.update': '更新笔记',
  'notes.list': '列出笔记',
  'notes.move': '移动笔记',
  'tasks.create': '创建任务',
  'tasks.update': '更新任务',
  'tasks.list': '列出任务',
  'attachments.read': '读取附件',
  'audio.transcribe': '音频转写',
  'text.uppercase': '文本转大写',
}

const toolDescriptions: Record<string, string> = {
  'system.echo': '回显文本，用于本地智能体集成测试。',
  'math.add': '计算两个数的和，不产生外部副作用。',
  'notes.search': '搜索已建立索引的笔记，并返回摘要和引用。',
  'rag.search': '检索与当前任务相关的笔记内容块和引用。',
  'notes.read': '根据笔记 ID 读取笔记及其内容块。',
  'notes.create': '在当前知识库中创建 Markdown 笔记。',
  'notes.update': '更新已有 Markdown 笔记。',
  'notes.list': '按文件夹和标签筛选并列出笔记摘要。',
  'notes.move': '移动笔记到其他文件夹并保留笔记 ID。',
  'tasks.create': '创建并持久化任务。',
  'tasks.update': '更新已有任务。',
  'tasks.list': '列出已持久化的任务。',
  'attachments.read': '读取由宿主管理的 UTF-8 附件。',
  'audio.transcribe': '将音频转写为文本，按模型路由使用 API 或本地后端。',
  'text.uppercase': '将输入文本中的字母转换为大写。',
}

// MCP IDs contain a server-specific namespace. Localize the remote tool name
// for presentation only; requests must keep using the complete original ID.
const mcpTools: Record<string, { label: string; description: string }> = {
  web_search: {
    label: '网页搜索',
    description: '搜索实时或外部网页信息。输入搜索关键词；结果包含标题、链接、摘要等信息。时效性问题可在关键词中加入日期，完整参数以服务原文为准。',
  },
  understand_image: {
    label: '图像理解',
    description: '根据提示词分析图片、描述内容或提取信息。输入分析要求和图片地址或本地路径；支持的格式与路径规则请查看服务原文。',
  },
}

function mcpName(name: string): string | undefined {
  return /^mcp\.[^.]+\.(.+)$/.exec(name)?.[1]
}

const permissionLabels: Record<string, string> = {
  'notes.search': '搜索笔记',
  'notes.read': '读取笔记',
  'notes.write': '修改笔记',
  'tasks.read': '读取任务',
  'tasks.write': '修改任务',
  'attachments.read': '读取附件',
  'network.request': '访问网络',
  'secrets.use': '使用密钥',
}

const detailLabels: Record<string, string> = {
  tool_call_id: '工具调用 ID',
  name: '工具名称',
  arguments: '参数',
  parameters: '参数',
  success: '是否成功',
  output: '输出',
  result: '结果',
  error_code: '错误代码',
  error_message: '错误信息',
  note_id: '笔记 ID',
  block_id: '内容块 ID',
  heading_path: '标题路径',
  input_tokens: '输入令牌',
  output_tokens: '输出令牌',
  total_tokens: '令牌总数',
  status: '状态',
  duration_ms: '耗时（毫秒）',
  model_call_id: '模型调用 ID',
  parent_model_call_id: '上级模型调用 ID',
  finish_reason: '结束原因',
  decision: '授权决定',
}

export function runStatusLabel(status?: AgentRunStatus): string {
  return status ? runStatusLabels[status] : '未知状态'
}

export function eventLabel(event: AgentEventType): string {
  return eventLabels[event]
}

export function toolLabel(name: string): string {
  const remote = mcpName(name)
  if (remote) return mcpTools[remote]?.label ?? `MCP 工具 · ${remote}`
  return toolLabels[name] ?? name
}

export function toolDescription(name: string, fallback: string): string {
  const remote = mcpName(name)
  if (remote) {
    if (/\p{Script=Han}/u.test(fallback)) return fallback
    return mcpTools[remote]?.description ?? '暂无中文说明，请展开查看服务原文。'
  }
  return toolDescriptions[name] ?? fallback
}

export function permissionLabel(permission: string): string {
  return permissionLabels[permission] ?? permission
}

function localizeValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(localizeValue)
  if (value && typeof value === 'object') return localizeDetails(value as Record<string, unknown>)
  if (value === true) return '是'
  if (value === false) return '否'
  if (typeof value === 'string' && value in runStatusLabels) {
    return runStatusLabels[value as AgentRunStatus]
  }
  return value
}

export function localizeDetails(data: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(data).map(([key, value]) => [detailLabels[key] ?? key, localizeValue(value)])
  )
}
