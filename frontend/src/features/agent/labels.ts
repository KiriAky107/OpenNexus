import type { AgentEventType, AgentRunStatus } from '@/contracts'
import { appLocale, t } from '@/i18n'

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

const runStatusLabelsEn: Record<AgentRunStatus, string> = {
  queued: 'Queued', running: 'Running', waiting_permission: 'Waiting for permission',
  completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled',
}

const eventLabelsEn: Record<AgentEventType, string> = {
  RunStarted: 'Run started', TextDelta: 'Response', ThinkingDelta: 'Reasoning',
  ToolCall: 'Tool call', ToolResult: 'Tool result', PermissionRequired: 'Permission required',
  Usage: 'Usage', Citation: 'Citation', ModelCallStarted: 'Model call started',
  ModelCallCompleted: 'Model call completed', ModelCallFailed: 'Model call failed',
  PermissionResolved: 'Permission resolved', RunCompleted: 'Run completed',
  RunFailed: 'Run failed', RunCancelled: 'Run cancelled',
}

const toolLabels: Record<string, string> = {
  'markdown.catalog': 'Markdown 格式目录',
  'markdown.compose': '生成 Markdown 片段',
  'notes.patch_markdown': '局部修改 Markdown',
  'system.echo': '回显测试',
  'math.add': '数值相加',
  'notes.search': '搜索笔记',
  'rag.search': '知识检索',
  'notes.read': '读取笔记',
  'notes.create': '创建笔记',
  'notes.update': '更新笔记',
  'notes.list': '列出笔记',
  'notes.move': '移动笔记',
  'notes.rename': '重命名笔记',
  'notes.delete': '删除笔记',
  'tasks.create': '创建任务',
  'tasks.update': '更新任务',
  'tasks.list': '列出任务',
  'tasks.read': '读取任务',
  'tasks.delete': '删除任务',
  'attachments.read': '读取附件',
  'audio.transcribe': '音频转写',
  'audio.transcription_status': '读取转写结果',
  'function_plot.compose': '生成函数图',
  'skills.list': '列出自定义 Skill',
  'skills.create': '创建自定义 Skill',
  'skills.update': '更新自定义 Skill',
  'plugins.list': '列出 Plugin',
  'plugins.create': '创建声明式 Plugin',
  'text.uppercase': '文本转大写',
}

const toolDescriptions: Record<string, string> = {
  'markdown.catalog': '查询支持的 Markdown 格式、警告框类型及渲染限制。',
  'markdown.compose': '生成标题、列表、表格、警告框、公式、Mermaid 和元数据等片段，不直接写入笔记。',
  'notes.patch_markdown': '根据内容版本精确替换唯一片段，避免误改重复内容或覆盖并发编辑。',
  'system.echo': '回显文本，用于本地智能体集成测试。',
  'math.add': '计算两个数的和，不产生外部副作用。',
  'notes.search': '搜索已建立索引的笔记，并返回摘要和引用。',
  'rag.search': '检索与当前任务相关的笔记内容块和引用。',
  'notes.read': '根据笔记 ID 读取笔记及其内容块。',
  'notes.create': '在当前知识库中创建 Markdown 笔记。',
  'notes.update': '更新已有 Markdown 笔记。',
  'notes.list': '按文件夹和标签筛选并列出笔记摘要。',
  'notes.move': '移动笔记到其他文件夹并保留笔记 ID。',
  'notes.rename': '重命名 Markdown 文件并保留笔记 ID 和索引身份。',
  'notes.delete': '删除当前知识库中的指定笔记。',
  'tasks.create': '创建并持久化任务。',
  'tasks.update': '更新已有任务。',
  'tasks.list': '列出已持久化的任务。',
  'tasks.read': '根据任务 ID 读取完整任务。',
  'tasks.delete': '删除指定的持久化任务。',
  'attachments.read': '读取由宿主管理的 UTF-8 附件。',
  'audio.transcribe': '将音频转写为文本，按模型路由使用 API 或本地后端。',
  'audio.transcription_status': '读取转写任务状态、文本、分段和错误信息。',
  'function_plot.compose': '根据表达式生成并校验安全的 function-plot Markdown 代码块。',
  'skills.list': '列出当前知识库中的自定义 Skill 及依赖状态。',
  'skills.create': '把提示词、工具和权限声明保存为当前知识库的自定义 Skill。',
  'skills.update': '使用当前版本号更新自定义 Skill，防止覆盖并发修改。',
  'plugins.list': '列出已安装 Plugin 及生命周期状态。',
  'plugins.create': '生成并安装仅使用宿主白名单处理器的声明式 Plugin；创建后需在 Plugin 页面检查并启用。',
  'text.uppercase': '将输入文本中的字母转换为大写。',
}

// MCP ID 包含特定于服务器的命名空间。本地化远程工具名称仅用于演示；要求必须继续使用完整的原装ID。
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
  'notes.delete': '删除笔记',
  'tasks.read': '读取任务',
  'tasks.write': '修改任务',
  'attachments.read': '读取附件',
  'skills.write': '创建或更新自定义 Skill',
  'plugins.write': '创建声明式 Plugin',
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
  if (!status) return t('未知状态', 'Unknown status')
  return appLocale.value === 'en' ? runStatusLabelsEn[status] : runStatusLabels[status]
}

export function eventLabel(event: AgentEventType): string {
  return appLocale.value === 'en' ? eventLabelsEn[event] : eventLabels[event]
}

export function toolLabel(name: string): string {
  const remote = mcpName(name)
  if (remote) return appLocale.value === 'en' ? `MCP Tool · ${remote}` : (mcpTools[remote]?.label ?? `MCP 工具 · ${remote}`)
  if (appLocale.value === 'en') return name.split('.').map(part => part[0]?.toUpperCase() + part.slice(1)).join(' ')
  return toolLabels[name] ?? name
}

export function toolDescription(name: string, fallback: string): string {
  const remote = mcpName(name)
  if (remote) {
    if (appLocale.value === 'en') return fallback && !/\p{Script=Han}/u.test(fallback) ? fallback : `MCP tool ${remote}. See the original service description for full parameters.`
    if (/\p{Script=Han}/u.test(fallback)) return fallback
    return mcpTools[remote]?.description ?? '暂无中文说明，请展开查看服务原文。'
  }
  if (appLocale.value === 'en') return fallback && !/\p{Script=Han}/u.test(fallback) ? fallback : `Built-in tool: ${name}`
  return toolDescriptions[name] ?? fallback
}

export function permissionLabel(permission: string): string {
  if (appLocale.value === 'en') return permission.split('.').map(part => part[0]?.toUpperCase() + part.slice(1)).join(' ')
  return permissionLabels[permission] ?? permission
}

function localizeValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(localizeValue)
  if (value && typeof value === 'object') return localizeDetails(value as Record<string, unknown>)
  if (value === true) return t('是', 'Yes')
  if (value === false) return t('否', 'No')
  if (typeof value === 'string' && value in runStatusLabels) {
    return runStatusLabel(value as AgentRunStatus)
  }
  return value
}

export function localizeDetails(data: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(data).map(([key, value]) => [appLocale.value === 'en' ? key.replaceAll('_', ' ') : (detailLabels[key] ?? key), localizeValue(value)])
  )
}
