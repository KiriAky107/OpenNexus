from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageMeta(Contract):
    total: int = 0
    limit: int = 50
    offset: int = 0


class ErrorDetail(Contract):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(Contract):
    error: ErrorDetail


class OperationResponse(Contract):
    status: Literal["accepted", "completed"]
    resource_id: str | None = None
    message: str | None = None


# Notes and retrieval
class NoteBlock(Contract):
    block_id: str
    note_id: str
    heading_path: list[str] = Field(default_factory=list)
    start_offset: int
    end_offset: int
    content: str
    content_hash: str
    token_count: int


class NoteSummary(Contract):
    note_id: str
    title: str
    file_path: str
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class Note(NoteSummary):
    markdown: str
    blocks: list[NoteBlock] = Field(default_factory=list)


class NoteListResponse(Contract):
    items: list[NoteSummary] = Field(default_factory=list)
    page: PageMeta = Field(default_factory=PageMeta)


class NoteCreateRequest(Contract):
    title: str = Field(min_length=1)
    markdown: str = ""
    folder: str | None = None
    tags: list[str] = Field(default_factory=list)


class NoteUpdateRequest(Contract):
    title: str | None = None
    markdown: str | None = None
    tags: list[str] | None = None


class NoteMoveRequest(Contract):
    folder: str


class SearchMode(str, Enum):
    fts = "fts"
    vector = "vector"
    hybrid = "hybrid"


class SearchRequest(Contract):
    query: str = Field(min_length=1)
    mode: SearchMode = SearchMode.hybrid
    folders: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_from: datetime | None = None
    created_to: datetime | None = None
    updated_from: datetime | None = None
    updated_to: datetime | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    include_snippet: bool = True


class Citation(Contract):
    citation_id: str
    note_id: str
    block_id: str
    file_path: str
    heading_path: list[str] = Field(default_factory=list)
    start_offset: int | None = None
    end_offset: int | None = None
    source_audio: str | None = None
    start_time: float | None = None
    end_time: float | None = None
    speaker: str | None = None


class SearchResult(Contract):
    note_id: str
    block_id: str
    title: str
    file_path: str
    heading_path: list[str] = Field(default_factory=list)
    snippet: str | None = None
    score: float
    citation: Citation


class SearchResponse(Contract):
    query: str
    mode: SearchMode
    items: list[SearchResult] = Field(default_factory=list)
    page: PageMeta = Field(default_factory=PageMeta)


# Model, chat and tools
class MessageRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class Message(Contract):
    role: MessageRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class ToolDefinition(Contract):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    permission: str | None = None
    source: Literal["builtin", "plugin"] = "builtin"


class ToolListResponse(Contract):
    items: list[ToolDefinition] = Field(default_factory=list)


class ModelCapability(str, Enum):
    chat = "chat"
    vision = "vision"
    tool_calling = "tool_calling"
    reasoning = "reasoning"
    streaming = "streaming"
    structured_output = "structured_output"
    embedding = "embedding"


class ModelRequest(Contract):
    provider_id: str
    model: str
    system: str | None = None
    messages: list[Message]
    tools: list[ToolDefinition] = Field(default_factory=list)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    response_format: dict[str, Any] | None = None
    attachments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(ModelRequest):
    conversation_id: str | None = None
    use_rag: bool = True
    retrieval: SearchRequest | None = None


class ModelEventType(str, Enum):
    text_delta = "TextDelta"
    thinking_delta = "ThinkingDelta"
    tool_call_start = "ToolCallStart"
    tool_call_delta = "ToolCallDelta"
    tool_call_end = "ToolCallEnd"
    usage = "Usage"
    error = "Error"
    done = "Done"


class ModelEvent(Contract):
    event: ModelEventType
    sequence: int = 0
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


# Agent
class AgentRunStatus(str, Enum):
    queued = "queued"
    running = "running"
    waiting_permission = "waiting_permission"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AgentRunCreateRequest(Contract):
    input: str = Field(min_length=1)
    provider_id: str
    model: str
    skill_id: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    max_steps: int = Field(default=10, ge=1, le=100)
    tool_timeout_seconds: int = Field(default=30, ge=1)
    run_timeout_seconds: int = Field(default=300, ge=1)
    token_budget: int | None = Field(default=None, ge=1)
    max_concurrent_tools: int = Field(default=1, ge=1)
    allow_network: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRun(Contract):
    run_id: str
    status: AgentRunStatus
    input: str
    provider_id: str
    model: str
    skill_id: str | None = None
    current_step: int = 0
    max_steps: int
    token_budget: int | None = None
    cancelled: bool = False
    citations: list[Citation] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AgentRunListResponse(Contract):
    items: list[AgentRun] = Field(default_factory=list)
    page: PageMeta = Field(default_factory=PageMeta)


class AgentEventType(str, Enum):
    run_started = "RunStarted"
    text_delta = "TextDelta"
    thinking_delta = "ThinkingDelta"
    tool_call = "ToolCall"
    tool_result = "ToolResult"
    permission_required = "PermissionRequired"
    usage = "Usage"
    citation = "Citation"
    run_completed = "RunCompleted"
    run_failed = "RunFailed"
    run_cancelled = "RunCancelled"


class AgentEvent(Contract):
    event: AgentEventType
    run_id: str
    sequence: int
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class PermissionDecisionRequest(Contract):
    decision: Literal["allow_once", "allow_session", "deny"]


# Skills and plugins
class RetrievalConfig(Contract):
    top_k: int = Field(default=10, ge=1, le=100)
    rerank: bool = True
    citation: bool = True


class SkillModelConfig(Contract):
    required_capabilities: list[ModelCapability] = Field(default_factory=list)


class SkillManifest(Contract):
    skill_id: str
    name: str
    version: str
    description: str = ""
    permissions: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    model: SkillModelConfig = Field(default_factory=SkillModelConfig)


class SkillStatus(str, Enum):
    installed = "installed"
    disabled = "disabled"
    ready = "ready"
    dependency_missing = "dependency_missing"
    permission_required = "permission_required"
    error = "error"


class Skill(Contract):
    manifest: SkillManifest
    status: SkillStatus
    enabled: bool = False
    missing_dependencies: list[str] = Field(default_factory=list)


class SkillListResponse(Contract):
    items: list[Skill] = Field(default_factory=list)


class ExtensionInstallRequest(Contract):
    package_path: str


class PluginBackend(Contract):
    type: Literal["mcp", "internal_rpc", "none"] = "none"
    transport: Literal["stdio", "http", "none"] = "none"


class PluginContribution(Contract):
    tools: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    importers: list[str] = Field(default_factory=list)
    exporters: list[str] = Field(default_factory=list)
    panels: list[str] = Field(default_factory=list)
    settings_sections: list[str] = Field(default_factory=list)


class PluginManifest(Contract):
    plugin_id: str
    name: str
    version: str
    description: str = ""
    permissions: list[str] = Field(default_factory=list)
    contributes: PluginContribution = Field(default_factory=PluginContribution)
    backend: PluginBackend = Field(default_factory=PluginBackend)


class PluginStatus(str, Enum):
    installed = "installed"
    disabled = "disabled"
    starting = "starting"
    ready = "ready"
    error = "error"
    dependency_missing = "dependency_missing"
    permission_required = "permission_required"


class Plugin(Contract):
    manifest: PluginManifest
    status: PluginStatus
    enabled: bool = False
    error_message: str | None = None


class PluginListResponse(Contract):
    items: list[Plugin] = Field(default_factory=list)


# Providers
class ProviderType(str, Enum):
    openai_responses = "openai_responses"
    openai_chat = "openai_chat"
    openai_compatible = "openai_compatible"
    anthropic_messages = "anthropic_messages"
    ollama = "ollama"


class ProviderConfig(Contract):
    provider_id: str
    provider_type: ProviderType
    name: str
    base_url: str | None = None
    default_model: str | None = None
    credential_id: str | None = None
    enabled: bool = True
    capabilities: list[ModelCapability] = Field(default_factory=list)


class ProviderCreateRequest(Contract):
    provider_type: ProviderType
    name: str
    base_url: str | None = None
    default_model: str | None = None
    credential_id: str | None = None
    enabled: bool = True


class ProviderUpdateRequest(Contract):
    name: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    credential_id: str | None = None
    enabled: bool | None = None


class ProviderListResponse(Contract):
    items: list[ProviderConfig] = Field(default_factory=list)


class ModelInfo(Contract):
    model: str
    display_name: str
    capabilities: list[ModelCapability] = Field(default_factory=list)


class ProviderModelsResponse(Contract):
    provider_id: str
    items: list[ModelInfo] = Field(default_factory=list)


class ProviderTestRequest(Contract):
    provider_id: str
    model: str | None = None
    credential_context_id: str | None = None


class ProviderTestResponse(Contract):
    provider_id: str
    success: bool
    latency_ms: int | None = None
    message: str


# Tasks, media and index
class TaskStatus(str, Enum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"
    cancelled = "cancelled"


class Task(Contract):
    task_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.todo
    note_id: str | None = None
    due_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskCreateRequest(Contract):
    title: str = Field(min_length=1)
    description: str = ""
    note_id: str | None = None
    due_at: datetime | None = None


class TaskUpdateRequest(Contract):
    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    note_id: str | None = None
    due_at: datetime | None = None


class TaskListResponse(Contract):
    items: list[Task] = Field(default_factory=list)
    page: PageMeta = Field(default_factory=PageMeta)


class TranscriptionRequest(Contract):
    attachment_id: str
    language: str | None = None
    diarization: bool = False


class TranscriptionJob(Contract):
    job_id: str
    attachment_id: str
    status: Literal["queued", "processing", "completed", "failed"]
    created_at: datetime


class IndexStatus(Contract):
    status: Literal["idle", "queued", "running", "failed"] = "idle"
    pending_jobs: int = 0
    active_job_id: str | None = None
    last_completed_at: datetime | None = None
    error_message: str | None = None


class IndexRebuildRequest(Contract):
    scope: Literal["all", "notes", "vectors"] = "all"
    note_ids: list[str] = Field(default_factory=list)
    force: bool = False


class IndexJob(Contract):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    scope: Literal["all", "notes", "vectors"]
    created_at: datetime
