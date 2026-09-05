// ============ Notes & Blocks ============

export interface Note {
  note_id: string
  title: string
  file_path: string
  folder_path: string
  created_at: string
  updated_at: string
  tags: string[]
  word_count: number
}

export interface NoteBlock {
  block_id: string
  note_id: string
  heading_path: string
  start_offset: number
  end_offset: number
  content: string
  content_hash: string
  token_count: number
}

export interface FileNode {
  id: string
  note_id?: string
  name: string
  path: string
  type: 'file' | 'folder'
  children?: FileNode[]
  is_open?: boolean
  is_dirty?: boolean
  is_external_changed?: boolean
}

// ============ Search ============

export interface SearchRequest {
  query: string
  mode?: 'fts' | 'vector' | 'hybrid'
  folder?: string
  note_id?: string
  tag?: string
  limit?: number
  offset?: number
}

export interface SearchResult {
  block_id: string
  note_id: string
  note_title: string
  file_path: string
  heading_path: string
  snippet: string
  score: number
  match_type: 'fts' | 'vector' | 'hybrid'
  tags?: string[]
}

// ============ Chat ============

export interface Conversation {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface ChatMessage {
  message_id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
  citations?: Citation[]
  tool_calls?: ToolCall[]
  thinking?: string
  usage?: TokenUsage
}

export interface Citation {
  note_id: string
  block_id: string
  file_path: string
  heading_path: string
  content: string
  source_audio?: {
    start_time: number
    end_time: number
    speaker?: string
  }
}

// ============ Model Events (SSE) ============

export type ModelEventType =
  | 'ContextStatus'
  | 'TextDelta'
  | 'ThinkingDelta'
  | 'ToolCallStart'
  | 'ToolCallDelta'
  | 'ToolCallEnd'
  | 'Usage'
  | 'Citation'
  | 'Error'
  | 'Done'

export interface ModelEvent {
  event: ModelEventType
  sequence: number
  data: Record<string, unknown>
  timestamp: string
}

// ============ Agent ============

export type AgentRunStatus =
  | 'queued'
  | 'running'
  | 'waiting_permission'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface AgentRun {
  run_id: string
  status: AgentRunStatus
  current_step: number
  max_steps: number
  token_usage?: TokenUsage
  started_at?: string
  completed_at?: string
  error?: string
}

export type AgentEventType =
  | 'RunStarted'
  | 'TextDelta'
  | 'ThinkingDelta'
  | 'ToolCall'
  | 'ToolResult'
  | 'PermissionRequired'
  | 'Usage'
  | 'Citation'
  | 'ModelCallStarted'
  | 'ModelCallCompleted'
  | 'ModelCallFailed'
  | 'PermissionResolved'
  | 'RunCompleted'
  | 'RunFailed'
  | 'RunCancelled'

export interface AgentEvent {
  event: AgentEventType
  sequence: number
  run_id: string
  data: Record<string, unknown>
  timestamp: string
}

export interface AgentTraceSummary {
  model_calls: number
  tool_calls: number
  duration_ms: number
  token_usage: number
  errors: number
}

export interface AgentTraceResponse {
  run_id: string
  status: AgentRunStatus
  items: AgentEvent[]
  next_sequence: number
  has_more: boolean
  summary: AgentTraceSummary
  config_snapshot: Record<string, unknown>
}

export interface ToolCall {
  tool_call_id: string
  name: string
  parameters: Record<string, unknown>
  status: 'pending' | 'running' | 'completed' | 'error'
  result?: string
  started_at?: string
  completed_at?: string
  error_code?: string
  error_message?: string
}

export interface ToolDefinition {
  name: string
  description: string
  parameters: Record<string, unknown>
  source?: 'builtin' | 'plugin' | 'mcp_server'
  plugin_id?: string
}

export interface PermissionRequest {
  request_id: string
  run_id: string
  tool_name: string
  permission: string
  parameters: Record<string, unknown>
  impact: string
}

export interface TokenUsage {
  input_tokens?: number
  output_tokens?: number
  total_tokens: number
}

// ============ Skill ============

export type SkillStatus =
  | 'installed'
  | 'disabled'
  | 'ready'
  | 'dependency_missing'
  | 'permission_required'
  | 'error'

export interface Skill {
  skill_id: string
  name: string
  version: string
  description: string
  icon?: string
  author?: string
  permissions: string[]
  tools: string[]
  retrieval_config?: {
    top_k: number
    rerank: boolean
    citation: boolean
  }
  model_requirements?: {
    capabilities: string[]
  }
  status: SkillStatus
  missing_dependencies?: string[]
  enabled: boolean
}

// ============ Plugin ============

export type PluginStatus =
  | 'installed'
  | 'disabled'
  | 'starting'
  | 'ready'
  | 'error'
  | 'dependency_missing'
  | 'permission_required'

export type PluginHostState = 'stopped' | 'starting' | 'ready' | 'unhealthy' | 'error'

export interface PluginHostStatus {
  plugin_id: string
  backend_type: 'mcp' | 'internal_rpc' | 'none'
  transport: 'stdio' | 'http' | 'none'
  status: PluginHostState
  tools_count: number
  started_at?: string | null
  last_seen_at?: string | null
  protocol_version?: string | null
  server_name?: string | null
  server_version?: string | null
  error?: string | null
}

export type PluginCommandLocation = 'command_palette' | 'context_menu' | 'toolbar'

export interface PluginCommand {
  command_id: string
  plugin_id: string
  title: string
  description: string
  icon?: string | null
  locations: PluginCommandLocation[]
  when: string[]
  parameters: Record<string, unknown>
  enabled: boolean
}

export interface PluginCommandContext {
  vault_id?: string | null
  note_id?: string | null
  file_path?: string | null
  selection?: string | null
}

export type PluginCommandEffect =
  | { type: 'none'; payload: Record<string, never> }
  | {
      type: 'notification'
      payload: { level: 'info' | 'success' | 'warning' | 'error'; message: string }
    }
  | {
      type: 'navigate'
      payload: {
        route:
          | 'vault-entry'
          | 'workspace'
          | 'search'
          | 'chat'
          | 'agent'
          | 'tasks'
          | 'skills'
          | 'plugins'
          | 'themes'
          | 'settings'
      }
    }
  | { type: 'refresh'; payload: { scope: 'workspace' | 'commands' | 'settings' | 'plugins' } }
  | { type: 'job'; payload: { job_id: string } }

export interface PluginCommandResult {
  command_id: string
  status: 'completed'
  effect: PluginCommandEffect
}

export type PluginSettingType = 'string' | 'number' | 'boolean' | 'select' | 'secret'

export interface PluginSettingField {
  key: string
  label: string
  description: string
  type: PluginSettingType
  required: boolean
  default?: unknown
  minimum?: number | null
  maximum?: number | null
  options: string[]
}

export interface PluginSettingsSchema {
  plugin_id: string
  schema_version: number
  fields: PluginSettingField[]
  values: Record<string, unknown>
  secrets: Record<string, { configured: boolean }>
}

export interface PluginSecretStatus {
  plugin_id: string
  key: string
  configured: boolean
}

export interface PluginContribution {
  type: 'tool' | 'command' | 'importer' | 'exporter' | 'sidebar_panel' | 'settings_section'
  id: string
  name: string
  description?: string
}

export interface Plugin {
  plugin_id: string
  name: string
  version: string
  description: string
  icon?: string
  author?: string
  status: PluginStatus
  enabled: boolean
  permissions: string[]
  granted_permissions?: string[]
  contributions: PluginContribution[]
  backend_type?: 'mcp' | 'internal_rpc' | 'none'
  transport?: 'stdio' | 'http' | 'none'
  last_error?: string
  dependent_skills?: string[]
}

// ============ Provider ============

export type ProviderType = ApiProviderType

export interface ModelCapability {
  chat: boolean
  vision: boolean
  tool_calling: boolean
  reasoning: boolean
  streaming: boolean
  structured_output: boolean
  embedding: boolean
}

export interface ModelInfo {
  model_id: string
  name: string
  capabilities: Partial<ModelCapability>
  context_window?: number
}

export interface RequestOverride {
  capability: 'chat' | 'embedding' | 'transcription' | 'speaker_matching'
  model?: string | null
  stream?: boolean | null
  body: Record<string, unknown>
}

export interface ModelContextPolicy {
  model: string
  context_window: number
  output_reserve: number
  threshold: number
  mode: 'detect' | 'compress'
  prompt: string
}

export interface ProviderConfig {
  version?: number
  context_policies?: ModelContextPolicy[]
  request_overrides?: RequestOverride[]
  provider_id: string
  provider_type: ProviderType
  name: string
  base_url?: string
  default_model: string
  enabled: boolean
  capabilities: Partial<ModelCapability>
  credential_id?: string
  has_credential: boolean
}

export interface ProviderPreset {
  preset_id: string
  name: string
  provider_type: ProviderType
  base_url: string
  default_credential_id?: string | null
  requires_credential: boolean
  logo_id?: string
  description?: string
  capabilities?: string[]
}

export type ProviderUpdateRequest = Partial<Omit<ProviderConfig, 'provider_id' | 'credential_id' | 'base_url'>> & {
  credential_id?: string | null
  base_url?: string | null
}

export type RoutingCapability = 'embedding' | 'transcription' | 'speaker_matching'

export interface ModelBinding {
  provider_id: string
  model: string
  endpoint: string
  dimensions?: number | null
}

export interface ModelRoutingConfig {
  version: number
  embedding: ModelBinding | null
  transcription: ModelBinding | null
  speaker_matching: ModelBinding | null
}

export interface ModelRoutingResponse {
  config: ModelRoutingConfig
  local_backends: Array<{
    capability: RoutingCapability
    status: 'placeholder' | 'not_installed' | 'ready'
    message: string
  }>
}

// ============ Tasks ============

export type TaskStatus = 'todo' | 'in_progress' | 'done' | 'cancelled'
export type TaskPriority = 'low' | 'medium' | 'high'
export type TaskSource = 'user' | 'note' | 'agent'

export interface TaskItem {
  task_id: string
  title: string
  description?: string
  status: TaskStatus
  priority?: TaskPriority
  due_date?: string
  note_id?: string
  note_title?: string
  source?: TaskSource
  created_at: string
  updated_at: string
}

// ============ Theme ============

export interface ThemeConfig {
  theme_id: string
  name: string
  version: string
  description: string
  is_dark: boolean
  author?: string
  builtin: boolean
  code_theme?: 'github-light' | 'github-dark'
}

// ============ Index ============

export interface IndexStatus {
  vector_refresh_required?: boolean
  status: 'unknown' | 'idle' | 'indexing' | 'error'
  pending_jobs: number
  total_notes: number | null
  total_blocks: number | null
  fts_enabled?: boolean
  vector_enabled?: boolean
  embedding_model?: string
  reranker_model?: string
  last_indexed_at?: string
  error?: string
}

// ============ System ============

export interface ApiError {
  code: string
  message: string
  details?: Record<string, unknown>
}

export interface ErrorResponse {
  error: ApiError
}

export interface SystemStatus {
  status: 'ok'
  name: string
  version: string
  environment: string
}

export type SaveStatus =
  | 'idle'
  | 'dirty'
  | 'saving'
  | 'saved'
  | 'save_failed'
  | 'external_changed'
  | 'conflict'

export type AiCoreStatus = 'unknown' | 'starting' | 'running' | 'stopped' | 'error'

// ============ FastAPI wire contracts ============
// UI view models above may contain presentation-only fields. Services must use
// these DTOs at the HTTP boundary and explicitly map them to view models.

export interface PageMeta {
  total: number
  limit: number
  offset: number
}

export interface ApiWorkspaceInfo {
  vault_id: string
  name: string
  path: string
  file_count: number
  indexed_note_count: number
  requires_refresh: boolean
}

export interface ApiWorkspaceEntry {
  entry_id: string
  name: string
  path: string
  type: 'file' | 'folder'
  note_id?: string | null
  children: ApiWorkspaceEntry[]
}

export interface ApiWorkspaceSnapshot {
  workspace: ApiWorkspaceInfo
  items: ApiWorkspaceEntry[]
}

export interface OperationResponse {
  status: 'accepted' | 'completed'
  resource_id?: string | null
  message?: string | null
}

export type McpServerTransport = 'stdio' | 'streamable_http' | 'sse'
export type McpServerState = 'stopped' | 'starting' | 'ready' | 'unhealthy' | 'error'

export interface McpServerInput {
  version?: number
  name: string
  transport: McpServerTransport
  command?: string | null
  args: string[]
  url?: string | null
  headers: Record<string, string>
  environment: Record<string, string>
  secret_environment_keys: string[]
  secret_header_keys: string[]
  permissions: string[]
  startup_timeout_seconds: number
  tool_timeout_seconds: number
}

export interface McpServer extends Omit<McpServerInput, 'secret_environment_keys' | 'secret_header_keys'> {
  server_id: string
  version: number
  secret_environment: Record<string, boolean>
  secret_headers: Record<string, boolean>
  enabled: boolean
  trusted: boolean
  command_digest: string
  command_summary: string
  status: McpServerState
  tools_count: number
  protocol_version?: string | null
  remote_server_name?: string | null
  remote_server_version?: string | null
  error?: string | null
  last_tested_at?: string | null
  last_test_succeeded?: boolean | null
}

export interface McpToolSummary {
  name: string
  remote_name: string
  description: string
  permission?: string | null
}

export interface ApiNoteBlock {
  block_id: string
  note_id: string
  heading_path: string[]
  start_offset: number
  end_offset: number
  content: string
  content_hash: string
  token_count: number
}

export interface ApiNoteSummary {
  note_id: string
  title: string
  file_path: string
  tags: string[]
  created_at: string
  updated_at: string
}

export interface ApiNote extends ApiNoteSummary {
  markdown: string
  blocks: ApiNoteBlock[]
}

export interface ApiSearchResult {
  note_id: string
  block_id: string
  title: string
  file_path: string
  heading_path: string[]
  snippet?: string | null
  score: number
  citation: ApiCitation
}

export interface ApiCitation {
  citation_id: string
  note_id: string
  block_id: string
  file_path: string
  heading_path: string[]
  start_offset?: number | null
  end_offset?: number | null
  source_audio?: string | null
  start_time?: number | null
  end_time?: number | null
  speaker?: string | null
}

export interface ApiAgentRun {
  run_id: string
  status: AgentRunStatus
  input: string
  provider_id: string
  model: string
  skill_id?: string | null
  current_step: number
  max_steps: number
  token_budget?: number | null
  cancelled: boolean
  output?: string | null
  error_code?: string | null
  error_message?: string | null
  token_usage: number
  created_at: string
  updated_at: string
}

export interface ApiSkill {
  manifest: {
    skill_id: string
    name: string
    version: string
    description: string
    permissions: string[]
    tools: string[]
    retrieval: { top_k: number; rerank: boolean; citation: boolean }
    model: { required_capabilities: string[] }
  }
  status: SkillStatus
  enabled: boolean
  missing_dependencies: string[]
}

export interface ApiPlugin {
  manifest: {
    plugin_id: string
    name: string
    version: string
    description: string
    permissions: string[]
    contributes: {
      tools: string[]
      commands: string[]
      importers: string[]
      exporters: string[]
      panels: string[]
      settings_sections: string[]
    }
    backend: {
      type: 'mcp' | 'internal_rpc' | 'none'
      transport: 'stdio' | 'http' | 'none'
      command?: string | null
      args?: string[]
      startup_timeout_seconds?: number
      tool_timeout_seconds?: number
    }
  }
  status: PluginStatus
  enabled: boolean
  granted_permissions: string[]
  error_message?: string | null
}

export type ApiProviderType =
  | 'mock'
  | 'openai_responses'
  | 'openai_chat'
  | 'openai_compatible'
  | 'anthropic_messages'
  | 'ollama'

export interface ApiProviderConfig {
  version?: number
  context_policies?: ModelContextPolicy[]
  request_overrides?: RequestOverride[]
  provider_id: string
  provider_type: ApiProviderType
  name: string
  base_url?: string | null
  default_model?: string | null
  credential_id?: string | null
  enabled: boolean
  capabilities: string[]
}

export interface ApiProviderPreset {
  preset_id: string
  name: string
  provider_type: ApiProviderType
  base_url: string
  default_credential_id?: string | null
  requires_credential: boolean
  logo_id?: string
  description?: string
  capabilities?: string[]
}

export interface ApiModelInfo {
  model: string
  display_name: string
  capabilities: string[]
}

export interface ApiTask {
  task_id: string
  title: string
  description: string
  status: TaskStatus
  note_id?: string | null
  due_at?: string | null
  created_at: string
  updated_at: string
}

export interface ApiIndexStatus {
  vector_refresh_required?: boolean
  total_notes: number
  total_blocks: number
  status: 'idle' | 'queued' | 'running' | 'failed'
  pending_jobs: number
  active_job_id?: string | null
  last_completed_at?: string | null
  error_message?: string | null
}

export interface ApiIndexJob {
  job_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  scope: 'all' | 'notes' | 'vectors'
  created_at: string
}

// ============ Theme Package (Phase 2) ============

export interface ThemeManifest {
  theme_id: string
  name: string
  version: string
  author: string
  description?: string
  min_app_version: string
  is_dark: boolean
  css_entry: string
  preview?: string
  tags?: string[]
  homepage?: string
  license?: string
}

export interface InstalledTheme {
  theme_id: string
  name: string
  version: string
  author: string
  description?: string
  is_dark: boolean
  builtin: boolean
  enabled: boolean
  installed_at?: string
  manifest: ThemeManifest
  code_theme?: 'github-light' | 'github-dark'
}

export interface ThemePackageInspection {
  package_id: string
  manifest: ThemeManifest
  preview_url: string
  warnings: string[]
  compatible: boolean
  error_code?: string
  /** 包内实际的主题 CSS。安装时必须用这份内容，不能另行生成。 */
  css: string
}

export type ThemeErrorCode =
  | 'THEME_PACKAGE_NOT_FOUND'
  | 'THEME_MANIFEST_INVALID'
  | 'THEME_PACKAGE_INCOMPATIBLE'
  | 'THEME_PACKAGE_UNSUPPORTED_FORMAT'
  | 'THEME_PACKAGE_INVALID'
  | 'THEME_CSS_INVALID'
  | 'THEME_SECURITY_VIOLATION'
  | 'THEME_INSTALL_FAILED'
  | 'THEME_UNINSTALL_FAILED'

// ============ Mermaid Renderer (Phase 2) ============

export interface MermaidRenderResult {
  svg: string
  width: number
  height: number
  warnings: string[]
}

export interface MermaidParseError {
  message: string
  line?: number
  column?: number
}

// ============ Agent Trace Node (Phase 2 visualization) ============

export type TraceNodeType =
  | 'run'
  | 'model_call'
  | 'tool_call'
  | 'tool_result'
  | 'text'
  | 'thinking'
  | 'citation'
  | 'usage'
  | 'permission'
  | 'error'
  | 'complete'

export interface TraceNode {
  id: string
  sequence: number
  type: TraceNodeType
  title: string
  subtitle?: string
  status: 'pending' | 'running' | 'completed' | 'error' | 'cancelled'
  duration_ms?: number
  children: TraceNode[]
  data: Record<string, unknown>
  timestamp: string
  parent_id?: string
}

export interface TraceTimelineGroup {
  group_id: string
  label: string
  start_sequence: number
  end_sequence: number
  duration_ms?: number
  nodes: TraceNode[]
}
