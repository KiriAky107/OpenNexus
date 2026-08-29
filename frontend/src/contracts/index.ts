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
  | 'TextDelta'
  | 'ThinkingDelta'
  | 'ToolCallStart'
  | 'ToolCallDelta'
  | 'ToolCallEnd'
  | 'Usage'
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
  source?: 'builtin' | 'plugin'
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
  input_tokens: number
  output_tokens: number
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
  contributions: PluginContribution[]
  backend_type?: 'mcp' | 'internal'
  transport?: 'stdio' | 'websocket'
  last_error?: string
  dependent_skills?: string[]
}

// ============ Provider ============

export type ProviderType = 'openai' | 'anthropic' | 'ollama' | 'openai-compatible' | 'mock'

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

export interface ProviderConfig {
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

// ============ Tasks ============

export type TaskStatus = 'todo' | 'in_progress' | 'done' | 'cancelled'
export type TaskPriority = 'low' | 'medium' | 'high'
export type TaskSource = 'user' | 'note' | 'agent'

export interface TaskItem {
  task_id: string
  title: string
  description?: string
  status: TaskStatus
  priority: TaskPriority
  due_date?: string
  note_id?: string
  note_title?: string
  source: TaskSource
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
}

// ============ Index ============

export interface IndexStatus {
  status: 'idle' | 'indexing' | 'error'
  pending_jobs: number
  total_notes: number
  total_blocks: number
  fts_enabled: boolean
  vector_enabled: boolean
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
  name: string
  version: string
  environment: 'development' | 'production' | 'test'
  ai_core_available: boolean
}

export type SaveStatus =
  | 'idle'
  | 'dirty'
  | 'saving'
  | 'saved'
  | 'save_failed'
  | 'external_changed'
  | 'conflict'

export type AiCoreStatus = 'starting' | 'running' | 'stopped' | 'error'
