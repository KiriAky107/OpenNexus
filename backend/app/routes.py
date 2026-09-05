import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse

from app.agent import AgentCapacityError, AgentRunNotFoundError
from app.container import container
from app.contracts import (
    AgentRun,
    AgentRunCreateRequest,
    AgentRunListResponse,
    AgentTraceResponse,
    ChatRequest,
    ChatMessageListResponse,
    Conversation,
    ConversationCreateRequest,
    ConversationListResponse,
    BenchmarkDatasetListResponse,
    BenchmarkEventType,
    BenchmarkKind,
    BenchmarkReport,
    BenchmarkRun,
    BenchmarkRunListResponse,
    BenchmarkStatus,
    RAGRunRequest,
    CredentialStatus,
    CredentialWriteRequest,
    ExtensionInstallRequest,
    FolderCreateRequest,
    FolderDeleteRequest,
    FolderRenameRequest,
    IndexJob,
    IndexRebuildRequest,
    IndexStatus,
    McpServer,
    McpServerCreateRequest,
    McpServerListResponse,
    McpServerSecretStatus,
    McpServerSecretWriteRequest,
    McpServerTrustRequest,
    McpServerUpdateRequest,
    McpToolSummaryListResponse,
    ModelEvent,
    ModelEventType,
    EmbeddingRequest,
    EmbeddingResult,
    ModelRoutingConfig,
    ModelRoutingResponse,
    SpeakerMatchRequest,
    SpeakerMatchResult,
    Note,
    NoteCreateRequest,
    NoteListResponse,
    NoteMoveRequest,
    NoteRenameRequest,
    NoteUpdateRequest,
    OperationResponse,
    PageMeta,
    PermissionDecisionRequest,
    Plugin,
    PluginCommandExecuteRequest,
    PluginCommandListResponse,
    PluginCommandLocation,
    PluginCommandResult,
    PluginHostStatus,
    PluginListResponse,
    PluginPermissionGrantRequest,
    PluginSecretStatus,
    PluginSecretWriteRequest,
    PluginSettingsSchema,
    PluginSettingsUpdateRequest,
    ProviderConfig,
    ProviderCreateRequest,
    ProviderListResponse,
    ProviderModelsResponse,
    ProviderPresetListResponse,
    ProviderTestRequest,
    ProviderTestResponse,
    ProviderUpdateRequest,
    SearchRequest,
    SearchResponse,
    Skill,
    SkillListResponse,
    Task,
    TaskCreateRequest,
    TaskListResponse,
    TaskUpdateRequest,
    ToolListResponse,
    TranscriptionJob,
    TranscriptionRequest,
    WorkspaceEntry,
    WorkspaceInfo,
    WorkspaceOpenRequest,
    WorkspaceSnapshot,
)
from app.agent import AgentCapacityError, AgentRunNotFoundError
from app.benchmarks import datasets as benchmark_datasets
from app.benchmarks import service as benchmark_service
from app.container import container
from app.errors import ApiError
from app.extensions import ExtensionError
from app.extensions.mcp_registry import McpRegistryError
from app.providers.base import ProviderError
from app.providers.credentials import (
    CredentialStoreError,
    validate_provider_credential_id,
)
from app.providers.factory import UnsupportedProviderError
from app.providers.registry import ProviderNotFoundError
from app.retrieval.engine import engine
from app.services import (
    index_service,
    note_service,
    task_service,
    transcription_service,
    workspace_service,
)
from app.services.attachment_service import attachment_path

router = APIRouter(prefix="/api")


@router.get("/permissions/policy", tags=["Permissions"])
async def get_permission_policy() -> dict[str, str]:
    from app.agent.permissions import KNOWN_PERMISSIONS
    return {permission: container.permissions.policy.mode_for(permission).value
            for permission in sorted(KNOWN_PERMISSIONS)}


async def mcp_call_async(operation):
    """Even registry reads can wait on lifecycle locks; keep all MCP work off the event loop."""
    try:
        return await asyncio.to_thread(operation)
    except McpRegistryError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_public_credential_id(credential_id: str | None) -> None:
    try:
        validate_provider_credential_id(credential_id)
    except CredentialStoreError as exc:
        raise ApiError(422, "CREDENTIAL_NAMESPACE_RESERVED", str(exc)) from exc


def as_sse(event: str, payload: str, *, event_id: int | None = None) -> str:
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{id_line}event: {event}\ndata: {payload}\n\n"


def provider_or_404(provider_id: str):
    try:
        return container.providers.get(provider_id)
    except ProviderNotFoundError as exc:
        raise ApiError(
            404,
            "PROVIDER_NOT_FOUND",
            f"Provider is not registered or enabled: {provider_id}",
            {"provider_id": provider_id},
        ) from exc


def agent_run_or_404(run_id: str) -> AgentRun:
    try:
        return container.agent.get_run(run_id)
    except AgentRunNotFoundError as exc:
        raise ApiError(
            404,
            "AGENT_RUN_NOT_FOUND",
            f"Agent run does not exist: {run_id}",
            {"run_id": run_id},
        ) from exc


def configurable_provider_or_404(provider_id: str):
    try:
        return container.providers.get_any(provider_id)
    except ProviderNotFoundError as exc:
        raise ApiError(
            404,
            "PROVIDER_NOT_FOUND",
            f"Provider is not registered: {provider_id}",
            {"provider_id": provider_id},
        ) from exc


def extension_call(operation):
    try:
        return operation()
    except ExtensionError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message, exc.details) from exc


async def extension_call_async(operation):
    """进程启动/关闭可能等待 stdio Host，移出 FastAPI 事件循环。"""

    try:
        return await asyncio.to_thread(operation)
    except ExtensionError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message, exc.details) from exc


# Workspace (single configured Vault in Web development mode)
@router.get("/workspace", response_model=WorkspaceInfo, tags=["Workspace"])
async def get_workspace() -> WorkspaceInfo:
    return workspace_service.get_workspace_info()


@router.post("/workspace/open", response_model=WorkspaceSnapshot, tags=["Workspace"])
async def open_workspace(request: WorkspaceOpenRequest) -> WorkspaceSnapshot:
    return await workspace_service.open_workspace(request.path)


@router.get("/workspace/tree", response_model=list[WorkspaceEntry], tags=["Workspace"])
async def get_workspace_tree() -> list[WorkspaceEntry]:
    return workspace_service.get_workspace_tree()


@router.post("/workspace/folders", response_model=WorkspaceEntry, tags=["Workspace"])
async def create_workspace_folder(request: FolderCreateRequest) -> WorkspaceEntry:
    return await workspace_service.create_folder(request.parent, request.name)


@router.post(
    "/workspace/folders/rename", response_model=WorkspaceEntry, tags=["Workspace"]
)
async def rename_workspace_folder(request: FolderRenameRequest) -> WorkspaceEntry:
    return await workspace_service.rename_folder(request.path, request.new_name)


@router.post(
    "/workspace/folders/delete", response_model=OperationResponse, tags=["Workspace"]
)
async def delete_workspace_folder(request: FolderDeleteRequest) -> OperationResponse:
    return await workspace_service.delete_folder(request.path)


# Notes
@router.get("/notes", response_model=NoteListResponse, tags=["Notes"])
async def list_notes(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    folder: str | None = None,
    tag: str | None = None,
) -> NoteListResponse:
    items, total = note_service.list_notes(
        limit=limit, offset=offset, folder=folder, tag=tag
    )
    return NoteListResponse(
        items=items, page=PageMeta(total=total, limit=limit, offset=offset)
    )


@router.post("/notes", response_model=Note, tags=["Notes"])
async def create_note(request: NoteCreateRequest) -> Note:
    return await note_service.create_note(
        title=request.title,
        markdown=request.markdown,
        folder=request.folder,
        tags=request.tags,
    )


@router.get("/notes/{note_id}", response_model=Note, tags=["Notes"])
async def get_note(note_id: str) -> Note:
    note = await note_service.get_note(note_id)
    if note is None:
        raise ApiError(
            404, "RESOURCE_NOT_FOUND", "note not found", {"note_id": note_id}
        )
    return note


@router.patch("/notes/{note_id}", response_model=Note, tags=["Notes"])
async def update_note(note_id: str, request: NoteUpdateRequest) -> Note:
    return await note_service.update_note(
        note_id, title=request.title, markdown=request.markdown, tags=request.tags
    )


@router.delete("/notes/{note_id}", response_model=OperationResponse, tags=["Notes"])
async def delete_note(note_id: str) -> OperationResponse:
    if not await note_service.delete_note(note_id):
        raise ApiError(
            404, "RESOURCE_NOT_FOUND", "note not found", {"note_id": note_id}
        )
    return OperationResponse(status="completed", resource_id=note_id, message="deleted")


@router.post("/notes/{note_id}/move", response_model=Note, tags=["Notes"])
async def move_note(note_id: str, request: NoteMoveRequest) -> Note:
    return await note_service.move_note(note_id, folder=request.folder)


@router.post("/notes/{note_id}/rename", response_model=Note, tags=["Notes"])
async def rename_note(note_id: str, request: NoteRenameRequest) -> Note:
    return await note_service.rename_note(note_id, file_name=request.file_name)


# Retrieval and chat
@router.post("/search", response_model=SearchResponse, tags=["Search"])
async def search_notes(request: SearchRequest) -> SearchResponse:
    from app.services import search_history
    search_history.record(request.query)
    return await engine.search(request)


@router.get("/search/history", tags=["Search"])
async def get_search_history() -> dict[str, list[str]]:
    from app.services import search_history
    return {"queries": search_history.list_queries()}


@router.delete("/search/history", tags=["Search"])
async def clear_search_history() -> dict[str, list[str]]:
    from app.services import search_history
    search_history.clear()
    return {"queries": []}


@router.get("/chat/conversations", response_model=ConversationListResponse, tags=["Chat"])
async def list_chat_conversations(
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> ConversationListResponse:
    from app.services import chat_history
    items, total = chat_history.list_conversations(limit, offset)
    return ConversationListResponse(items=items, page=PageMeta(total=total, limit=limit, offset=offset))


@router.post("/chat/conversations", response_model=Conversation, status_code=201, tags=["Chat"])
async def create_chat_conversation(request: ConversationCreateRequest) -> Conversation:
    from app.services import chat_history
    return chat_history.create(request.title, request.conversation_id)


@router.get("/chat/conversations/{conversation_id}/messages", response_model=ChatMessageListResponse, tags=["Chat"])
async def list_chat_messages(
    conversation_id: str,
    limit: int = Query(default=500, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> ChatMessageListResponse:
    from app.services import chat_history
    items, total = chat_history.list_messages(conversation_id, limit, offset)
    return ChatMessageListResponse(items=items, page=PageMeta(total=total, limit=limit, offset=offset))


@router.delete("/chat/conversations/{conversation_id}", response_model=OperationResponse, tags=["Chat"])
async def delete_chat_conversation(conversation_id: str) -> OperationResponse:
    from app.services import chat_history
    if not chat_history.delete(conversation_id):
        raise ApiError(404, "CONVERSATION_NOT_FOUND", "conversation not found", {"conversation_id": conversation_id})
    return OperationResponse(status="completed", resource_id=conversation_id, message="deleted")


@router.post(
    "/chat",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "ModelEvent Server-Sent Events stream",
            "content": {"text/event-stream": {}},
        }
    },
    tags=["Chat"],
)
async def chat(request: ChatRequest) -> StreamingResponse:
    from app.services import chat_history

    conversation_id = request.conversation_id
    assistant_message_id = request.assistant_message_id or f"message_{uuid4().hex}"
    if conversation_id:
        user_message = next(
            (message for message in reversed(request.messages) if message.role.value == "user" and message.content.strip()),
            None,
        )
        if user_message is not None:
            chat_history.append_message(
                conversation_id,
                message_id=request.user_message_id or f"message_{uuid4().hex}",
                role="user",
                content=user_message.content,
                title=request.conversation_title or user_message.content[:30],
            )
    provider = provider_or_404(request.provider_id)

    async def stream() -> AsyncIterator[str]:
        sequence = 0
        assistant_content = ""
        assistant_thinking = ""
        citations: list[dict] = []
        tool_calls: list[dict] = []
        argument_buffers: dict[str, str] = {}
        usage: dict | None = None
        try:
            from app.services.chat_context import prepare
            grounded_request, grounded_citations = await prepare(request)
            for citation in grounded_citations:
                citations.append(citation)
                event = ModelEvent(event=ModelEventType.citation, sequence=sequence,
                                   data=citation, timestamp=utc_now())
                sequence += 1
                yield as_sse(event.event.value, event.model_dump_json())
            async with aclosing(provider.adapter.stream(grounded_request)) as events:
                async for event in events:
                    event = event.model_copy(update={"sequence": sequence})
                    sequence += 1
                    if event.event == ModelEventType.text_delta:
                        assistant_content += str(event.data.get("text", ""))
                    elif event.event == ModelEventType.thinking_delta:
                        assistant_thinking += str(event.data.get("text", ""))
                    elif event.event == ModelEventType.tool_call_start:
                        tool_calls.append({
                            "tool_call_id": str(event.data.get("tool_call_id", "")),
                            "name": str(event.data.get("name", "unknown")),
                            "parameters": event.data.get("arguments") if isinstance(event.data.get("arguments"), dict) else {},
                            "status": "running",
                        })
                    elif event.event == ModelEventType.tool_call_delta:
                        call_id = str(event.data.get("tool_call_id", ""))
                        call = next((item for item in tool_calls if item["tool_call_id"] == call_id), None)
                        if call is not None:
                            delta = event.data.get("arguments_delta")
                            if isinstance(delta, str):
                                argument_buffers[call_id] = argument_buffers.get(call_id, "") + delta
                                try:
                                    parsed_arguments = json.loads(argument_buffers[call_id])
                                    if isinstance(parsed_arguments, dict):
                                        call["parameters"] = parsed_arguments
                                except ValueError:
                                    pass
                            arguments = event.data.get("arguments")
                            if isinstance(arguments, dict):
                                call["parameters"].update(arguments)
                    elif event.event == ModelEventType.tool_call_end:
                        call_id = str(event.data.get("tool_call_id", ""))
                        call = next((item for item in tool_calls if item["tool_call_id"] == call_id), None)
                        if call is not None:
                            call["status"] = "completed"
                    elif event.event == ModelEventType.usage:
                        input_tokens = int(event.data.get("input_tokens", 0))
                        output_tokens = int(event.data.get("output_tokens", 0))
                        usage = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                                 "total_tokens": input_tokens + output_tokens}
                    elif event.event == ModelEventType.error:
                        if assistant_content:
                            assistant_content += "\n\n"
                        assistant_content += str(event.data.get("message", "Model generation failed."))
                    yield as_sse(event.event.value, event.model_dump_json())
        except Exception as exc:
            failure_message = exc.message if isinstance(exc, ApiError) else "知识库检索或模型生成失败，请检查服务状态。"
            if assistant_content:
                assistant_content += "\n\n"
            assistant_content += failure_message
            error = ModelEvent(
                event=ModelEventType.error,
                sequence=sequence,
                data={"code": exc.code if isinstance(exc, ApiError) else "CHAT_FAILED",
                      "message": failure_message},
                timestamp=utc_now(),
            )
            done = ModelEvent(
                event=ModelEventType.done, sequence=sequence + 1,
                data={"status": "failed"}, timestamp=utc_now()
            )
            yield as_sse(error.event.value, error.model_dump_json())
            yield as_sse(done.event.value, done.model_dump_json())
        finally:
            if conversation_id and (assistant_content or assistant_thinking or citations or tool_calls):
                chat_history.append_message(
                    conversation_id,
                    message_id=assistant_message_id,
                    role="assistant",
                    content=assistant_content,
                    thinking=assistant_thinking or None,
                    citations=citations,
                    tool_calls=tool_calls,
                    usage=usage,
                )

    return StreamingResponse(stream(), media_type="text/event-stream")


# Agent
@router.get("/agent/runs", response_model=AgentRunListResponse, tags=["Agent"])
async def list_agent_runs(
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> AgentRunListResponse:
    items, total = container.agent.list_runs(limit=limit, offset=offset)
    return AgentRunListResponse(
        items=items,
        page=PageMeta(total=total, limit=limit, offset=offset),
    )


@router.post(
    "/agent/runs",
    response_model=AgentRun,
    status_code=202,
    tags=["Agent"],
)
async def create_agent_run(request: AgentRunCreateRequest) -> AgentRun:
    provider_or_404(request.provider_id)
    try:
        return await container.agent.create_run(request)
    except ExtensionError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message, exc.details) from exc
    except AgentCapacityError as exc:
        raise ApiError(429, "AGENT_CAPACITY_EXCEEDED", str(exc)) from exc


@router.get(
    "/agent/runs/{run_id}",
    response_model=AgentRun,
    tags=["Agent"],
)
async def get_agent_run(run_id: str) -> AgentRun:
    return agent_run_or_404(run_id)


@router.post(
    "/agent/runs/{run_id}/cancel",
    response_model=OperationResponse,
    tags=["Agent"],
)
async def cancel_agent_run(run_id: str) -> OperationResponse:
    agent_run_or_404(run_id)
    run = await container.agent.cancel(run_id)
    return OperationResponse(
        status="completed",
        resource_id=run.run_id,
        message=f"Agent run status: {run.status.value}",
    )


@router.get(
    "/agent/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "AgentEvent Server-Sent Events stream",
            "content": {"text/event-stream": {}},
        }
    },
    tags=["Agent"],
)
async def agent_events(
    run_id: str,
    after_sequence: int | None = Query(default=None, ge=-1),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    agent_run_or_404(run_id)
    cursor = after_sequence
    if cursor is None and last_event_id is not None:
        try:
            cursor = int(last_event_id)
        except ValueError as exc:
            raise ApiError(
                400,
                "TRACE_CURSOR_INVALID",
                "Last-Event-ID must be an integer sequence.",
                {"last_event_id": last_event_id},
            ) from exc
        if cursor < -1:
            raise ApiError(
                400,
                "TRACE_CURSOR_INVALID",
                "Last-Event-ID must be greater than or equal to -1.",
            )
    cursor = cursor if cursor is not None else -1

    async def stream() -> AsyncIterator[str]:
        async for event in container.agent.events(run_id, after_sequence=cursor):
            yield as_sse(
                event.event.value,
                event.model_dump_json(),
                event_id=event.sequence,
            )

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get(
    "/agent/runs/{run_id}/trace",
    response_model=AgentTraceResponse,
    tags=["Agent"],
)
async def get_agent_trace(
    run_id: str,
    after_sequence: int = Query(default=-1, ge=-1),
    limit: int = Query(default=200, ge=1, le=500),
) -> AgentTraceResponse:
    try:
        return container.agent.get_trace(
            run_id, after_sequence=after_sequence, limit=limit
        )
    except AgentRunNotFoundError as exc:
        raise ApiError(
            404,
            "AGENT_RUN_NOT_FOUND",
            f"Agent run does not exist: {run_id}",
            {"run_id": run_id},
        ) from exc


@router.post(
    "/agent/runs/{run_id}/permissions/{request_id}",
    response_model=OperationResponse,
    tags=["Agent"],
)
async def decide_agent_permission(
    run_id: str, request_id: str, request: PermissionDecisionRequest
) -> OperationResponse:
    agent_run_or_404(run_id)
    if not container.agent.resolve_permission(run_id, request_id, request.decision):
        raise ApiError(
            404,
            "PERMISSION_REQUEST_NOT_FOUND",
            "Permission request does not exist or has already been resolved.",
            {"run_id": run_id, "request_id": request_id},
        )
    return OperationResponse(
        status="completed", resource_id=request_id, message=request.decision
    )


@router.get("/tools", response_model=ToolListResponse, tags=["Agent"])
async def list_tools() -> ToolListResponse:
    return ToolListResponse(items=container.tools.definitions())


# Skills
@router.get("/skills", response_model=SkillListResponse, tags=["Skills"])
async def list_skills() -> SkillListResponse:
    return SkillListResponse(items=container.skills.list())


@router.get("/skills/{skill_id}", response_model=Skill, tags=["Skills"])
async def get_skill(skill_id: str) -> Skill:
    return extension_call(lambda: container.skills.get(skill_id))


@router.post(
    "/skills/install",
    response_model=Skill,
    status_code=202,
    tags=["Skills"],
)
async def install_skill(request: ExtensionInstallRequest) -> Skill:
    return extension_call(lambda: container.skills.install(request.package_path))


@router.post(
    "/skills/{skill_id}/enable",
    response_model=Skill,
    tags=["Skills"],
)
async def enable_skill(skill_id: str) -> Skill:
    return extension_call(lambda: container.skills.enable(skill_id))


@router.post(
    "/skills/{skill_id}/disable",
    response_model=Skill,
    tags=["Skills"],
)
async def disable_skill(skill_id: str) -> Skill:
    return extension_call(lambda: container.skills.disable(skill_id))


@router.delete(
    "/skills/{skill_id}",
    response_model=OperationResponse,
    tags=["Skills"],
)
async def uninstall_skill(skill_id: str) -> OperationResponse:
    extension_call(lambda: container.skills.uninstall(skill_id))
    return OperationResponse(
        status="completed", resource_id=skill_id, message="uninstalled"
    )


# Independent MCP Server Registry
@router.get("/mcp/servers", response_model=McpServerListResponse, tags=["MCP Servers"])
async def list_mcp_servers() -> McpServerListResponse:
    return McpServerListResponse(items=await mcp_call_async(container.mcp_servers.list))


@router.post(
    "/mcp/servers", response_model=McpServer, status_code=201, tags=["MCP Servers"]
)
async def create_mcp_server(request: McpServerCreateRequest) -> McpServer:
    return await mcp_call_async(lambda: container.mcp_servers.create(request))


@router.get("/mcp/servers/{server_id}", response_model=McpServer, tags=["MCP Servers"])
async def get_mcp_server(server_id: str) -> McpServer:
    return await mcp_call_async(lambda: container.mcp_servers.get(server_id))


@router.get(
    "/mcp/servers/{server_id}/tools",
    response_model=McpToolSummaryListResponse,
    tags=["MCP Servers"],
)
async def list_mcp_server_tools(server_id: str) -> McpToolSummaryListResponse:
    return McpToolSummaryListResponse(
        items=await mcp_call_async(lambda: container.mcp_servers.list_tools(server_id))
    )


@router.put("/mcp/servers/{server_id}", response_model=McpServer, tags=["MCP Servers"])
async def update_mcp_server(
    server_id: str, request: McpServerUpdateRequest
) -> McpServer:
    return await mcp_call_async(
        lambda: container.mcp_servers.update(server_id, request)
    )


@router.delete(
    "/mcp/servers/{server_id}", response_model=OperationResponse, tags=["MCP Servers"]
)
async def delete_mcp_server(server_id: str) -> OperationResponse:
    await mcp_call_async(lambda: container.mcp_servers.delete(server_id))
    return OperationResponse(
        status="completed", resource_id=server_id, message="deleted"
    )


@router.post(
    "/mcp/servers/{server_id}/trust", response_model=McpServer, tags=["MCP Servers"]
)
async def trust_mcp_server(server_id: str, request: McpServerTrustRequest) -> McpServer:
    return await mcp_call_async(
        lambda: container.mcp_servers.trust(server_id, request.command_digest)
    )


@router.post(
    "/mcp/servers/{server_id}/test", response_model=McpServer, tags=["MCP Servers"]
)
async def test_mcp_server(server_id: str) -> McpServer:
    return await mcp_call_async(lambda: container.mcp_servers.test(server_id))


@router.post(
    "/mcp/servers/{server_id}/enable", response_model=McpServer, tags=["MCP Servers"]
)
async def enable_mcp_server(server_id: str) -> McpServer:
    return await mcp_call_async(lambda: container.mcp_servers.enable(server_id))


@router.post(
    "/mcp/servers/{server_id}/disable", response_model=McpServer, tags=["MCP Servers"]
)
async def disable_mcp_server(server_id: str) -> McpServer:
    return await mcp_call_async(lambda: container.mcp_servers.disable(server_id))


@router.put(
    "/mcp/servers/{server_id}/secrets/{key}",
    response_model=McpServerSecretStatus,
    tags=["MCP Servers"],
)
async def put_mcp_server_secret(
    server_id: str,
    key: str,
    request: McpServerSecretWriteRequest,
    kind: str = Query(default="environment", pattern="^(environment|header)$"),
) -> McpServerSecretStatus:
    return await mcp_call_async(
        lambda: container.mcp_servers.put_secret(
            server_id, key, request.secret.get_secret_value(), kind=kind
        )
    )


@router.delete(
    "/mcp/servers/{server_id}/secrets/{key}",
    response_model=McpServerSecretStatus,
    tags=["MCP Servers"],
)
async def delete_mcp_server_secret(
    server_id: str,
    key: str,
    kind: str = Query(default="environment", pattern="^(environment|header)$"),
) -> McpServerSecretStatus:
    return await mcp_call_async(
        lambda: container.mcp_servers.delete_secret(server_id, key, kind=kind)
    )


# Plugins
@router.get("/plugins", response_model=PluginListResponse, tags=["Plugins"])
async def list_plugins() -> PluginListResponse:
    return PluginListResponse(items=container.plugins.list())


@router.get(
    "/plugins/{plugin_id}",
    response_model=Plugin,
    tags=["Plugins"],
)
async def get_plugin(plugin_id: str) -> Plugin:
    return extension_call(lambda: container.plugins.get(plugin_id))


@router.post(
    "/plugins/install",
    response_model=Plugin,
    status_code=202,
    tags=["Plugins"],
)
async def install_plugin(request: ExtensionInstallRequest) -> Plugin:
    return extension_call(lambda: container.plugins.install(request.package_path))


@router.post(
    "/plugins/{plugin_id}/enable",
    response_model=Plugin,
    tags=["Plugins"],
)
async def enable_plugin(plugin_id: str) -> Plugin:
    return await extension_call_async(lambda: container.plugins.enable(plugin_id))


@router.post(
    "/plugins/{plugin_id}/disable",
    response_model=Plugin,
    tags=["Plugins"],
)
async def disable_plugin(plugin_id: str) -> Plugin:
    return await extension_call_async(lambda: container.plugins.disable(plugin_id))


@router.put(
    "/plugins/{plugin_id}/permissions",
    response_model=Plugin,
    tags=["Plugins"],
)
async def set_plugin_permissions(
    plugin_id: str, request: PluginPermissionGrantRequest
) -> Plugin:
    return await extension_call_async(
        lambda: container.plugins.set_permissions(plugin_id, request.permissions)
    )


@router.get(
    "/plugins/{plugin_id}/host",
    response_model=PluginHostStatus,
    tags=["Plugins"],
)
async def get_plugin_host_status(plugin_id: str) -> PluginHostStatus:
    return extension_call(lambda: container.plugins.get_host_status(plugin_id))


@router.post(
    "/plugins/{plugin_id}/host/restart",
    response_model=OperationResponse,
    status_code=202,
    tags=["Plugins"],
)
async def restart_plugin_host(plugin_id: str) -> OperationResponse:
    status = await extension_call_async(
        lambda: container.plugins.restart_host(plugin_id)
    )
    return OperationResponse(
        status="accepted",
        resource_id=plugin_id,
        message=f"Plugin Host status: {status.status.value}",
    )


@router.delete(
    "/plugins/{plugin_id}",
    response_model=OperationResponse,
    tags=["Plugins"],
)
async def uninstall_plugin(plugin_id: str) -> OperationResponse:
    plugin = extension_call(lambda: container.plugins.get(plugin_id))
    dependent_skills = container.skills.depending_on_tools(
        plugin.manifest.contributes.tools
    )
    await extension_call_async(
        lambda: container.plugins.uninstall(plugin_id, dependent_skills)
    )
    return OperationResponse(
        status="completed", resource_id=plugin_id, message="uninstalled"
    )


# Plugin Command / Settings Contributions
@router.get(
    "/plugin-contributions/commands",
    response_model=PluginCommandListResponse,
    tags=["Plugins"],
)
async def list_plugin_commands(
    location: PluginCommandLocation | None = Query(default=None),
) -> PluginCommandListResponse:
    return PluginCommandListResponse(items=container.plugins.list_commands(location))


@router.post(
    "/plugin-contributions/commands/{command_id}/execute",
    response_model=PluginCommandResult,
    tags=["Plugins"],
)
async def execute_plugin_command(
    command_id: str, request: PluginCommandExecuteRequest
) -> PluginCommandResult:
    try:
        return await container.plugins.execute_command(
            command_id, request.arguments, request.context
        )
    except ExtensionError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message, exc.details) from exc


@router.get(
    "/plugins/{plugin_id}/settings",
    response_model=PluginSettingsSchema,
    tags=["Plugins"],
)
async def get_plugin_settings(plugin_id: str) -> PluginSettingsSchema:
    return extension_call(lambda: container.plugins.get_settings(plugin_id))


@router.put(
    "/plugins/{plugin_id}/settings",
    response_model=PluginSettingsSchema,
    tags=["Plugins"],
)
async def update_plugin_settings(
    plugin_id: str, request: PluginSettingsUpdateRequest
) -> PluginSettingsSchema:
    return extension_call(
        lambda: container.plugins.update_settings(
            plugin_id, request.schema_version, request.values
        )
    )


@router.put(
    "/plugins/{plugin_id}/settings/{key}/secret",
    response_model=PluginSecretStatus,
    tags=["Plugins"],
)
async def put_plugin_setting_secret(
    plugin_id: str, key: str, request: PluginSecretWriteRequest
) -> PluginSecretStatus:
    return extension_call(
        lambda: container.plugins.put_setting_secret(
            plugin_id, key, request.secret.get_secret_value()
        )
    )


@router.delete(
    "/plugins/{plugin_id}/settings/{key}/secret",
    response_model=PluginSecretStatus,
    tags=["Plugins"],
)
async def delete_plugin_setting_secret(plugin_id: str, key: str) -> PluginSecretStatus:
    return extension_call(
        lambda: container.plugins.delete_setting_secret(plugin_id, key)
    )


# Providers
@router.get(
    "/credentials/{credential_id}",
    response_model=CredentialStatus,
    tags=["Providers"],
)
async def get_credential_status(credential_id: str) -> CredentialStatus:
    validate_public_credential_id(credential_id)
    try:
        configured = container.credentials.has(credential_id)
    except CredentialStoreError as exc:
        raise ApiError(422, "CREDENTIAL_INVALID", str(exc)) from exc
    return CredentialStatus(credential_id=credential_id, configured=configured)


@router.put(
    "/credentials/{credential_id}",
    response_model=CredentialStatus,
    tags=["Providers"],
)
async def put_credential(
    credential_id: str, request: CredentialWriteRequest
) -> CredentialStatus:
    validate_public_credential_id(credential_id)
    try:
        container.credentials.put(credential_id, request.api_key.get_secret_value())
    except CredentialStoreError as exc:
        raise ApiError(422, "CREDENTIAL_STORE_ERROR", str(exc)) from exc
    return CredentialStatus(credential_id=credential_id, configured=True)


@router.delete(
    "/credentials/{credential_id}",
    response_model=CredentialStatus,
    tags=["Providers"],
)
async def delete_credential(credential_id: str) -> CredentialStatus:
    validate_public_credential_id(credential_id)
    try:
        container.credentials.delete(credential_id)
    except CredentialStoreError as exc:
        raise ApiError(422, "CREDENTIAL_STORE_ERROR", str(exc)) from exc
    return CredentialStatus(credential_id=credential_id, configured=False)


@router.get("/providers", response_model=ProviderListResponse, tags=["Providers"])
async def list_providers() -> ProviderListResponse:
    return ProviderListResponse(items=container.providers.list_configs())


@router.get(
    "/providers/presets",
    response_model=ProviderPresetListResponse,
    tags=["Providers"],
)
async def list_provider_presets() -> ProviderPresetListResponse:
    return ProviderPresetListResponse(items=container.provider_factory.presets())


@router.get(
    "/providers/{provider_id}",
    response_model=ProviderConfig,
    tags=["Providers"],
)
async def get_provider(provider_id: str) -> ProviderConfig:
    return configurable_provider_or_404(provider_id).config.model_copy(deep=True)


@router.post(
    "/providers",
    response_model=ProviderConfig,
    tags=["Providers"],
)
async def create_provider(request: ProviderCreateRequest) -> ProviderConfig:
    validate_public_credential_id(request.credential_id)
    config = ProviderConfig(
        provider_id=f"provider_{uuid4().hex}",
        provider_type=request.provider_type,
        name=request.name,
        base_url=request.base_url,
        default_model=request.default_model,
        credential_id=request.credential_id,
        enabled=request.enabled,
        request_overrides=request.request_overrides,
        capabilities=container.provider_factory.capabilities(request.provider_type),
    )
    try:
        adapter = container.provider_factory.build(config)
    except UnsupportedProviderError as exc:
        raise ApiError(
            422,
            "PROVIDER_TYPE_UNSUPPORTED",
            f"Provider adapter is not implemented: {request.provider_type.value}",
        ) from exc
    container.providers.register(config, adapter)
    return config


@router.patch(
    "/providers/{provider_id}",
    response_model=ProviderConfig,
    tags=["Providers"],
)
async def update_provider(
    provider_id: str, request: ProviderUpdateRequest
) -> ProviderConfig:
    current = configurable_provider_or_404(provider_id).config
    if provider_id == "mock":
        raise ApiError(
            409, "BUILTIN_PROVIDER_IMMUTABLE", "Mock provider cannot be modified."
        )
    fields = request.model_fields_set
    if request.version is not None and request.version != current.version:
        raise ApiError(409, "PROVIDER_VERSION_CONFLICT", "提供商配置已变更，请重新加载后保存。")
    if ("provider_type" in fields and request.provider_type is None) or ("name" in fields and request.name is None) or (
        "enabled" in fields and request.enabled is None
    ) or (
        "request_overrides" in fields and request.request_overrides is None
    ):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "provider_type, name and enabled cannot be null when explicitly provided.",
        )
    updates = {name: getattr(request, name) for name in fields}
    updates["version"] = current.version + 1
    if "credential_id" in fields:
        validate_public_credential_id(request.credential_id)
    config = ProviderConfig.model_validate(
        {**current.model_dump(mode="python"), **updates}
    )
    config.capabilities = container.provider_factory.capabilities(config.provider_type)
    try:
        adapter = container.provider_factory.build(config)
    except UnsupportedProviderError as exc:
        raise ApiError(422, "PROVIDER_TYPE_UNSUPPORTED", "Provider adapter is not supported.") from exc
    container.providers.replace(config, adapter)
    return config


@router.delete(
    "/providers/{provider_id}",
    response_model=OperationResponse,
    tags=["Providers"],
)
async def delete_provider(provider_id: str) -> OperationResponse:
    configurable_provider_or_404(provider_id)
    if provider_id == "mock":
        raise ApiError(
            409, "BUILTIN_PROVIDER_IMMUTABLE", "Mock provider cannot be deleted."
        )
    if container.model_routing.uses_provider(provider_id):
        raise ApiError(409, "PROVIDER_IN_USE", "请先在索引与模型中解除该提供商的模型绑定。")
    container.providers.unregister(provider_id)
    return OperationResponse(status="completed", resource_id=provider_id)


@router.get(
    "/providers/{provider_id}/models",
    response_model=ProviderModelsResponse,
    tags=["Providers"],
)
async def list_provider_models(provider_id: str) -> ProviderModelsResponse:
    provider_or_404(provider_id)
    try:
        models = await container.providers.list_models(provider_id)
    except ProviderError as exc:
        status_code = {
            "PROVIDER_CREDENTIAL_MISSING": 422,
            "PROVIDER_CREDENTIAL_UNAVAILABLE": 500,
            "PROVIDER_AUTH_FAILED": 401,
            "MODEL_NOT_FOUND": 404,
            "PROVIDER_RATE_LIMITED": 429,
            "PROVIDER_TIMEOUT": 504,
        }.get(exc.code, 502)
        raise ApiError(
            status_code,
            exc.code,
            exc.message,
            {"provider_id": provider_id},
        ) from exc
    return ProviderModelsResponse(
        provider_id=provider_id,
        items=models,
    )


@router.post(
    "/providers/test",
    response_model=ProviderTestResponse,
    tags=["Providers"],
)
async def test_provider(request: ProviderTestRequest) -> ProviderTestResponse:
    registered = configurable_provider_or_404(request.provider_id)
    if request.credential_context_id:
        validate_public_credential_id(request.credential_context_id)
        temporary_config = registered.config.model_copy(
            update={"credential_id": request.credential_context_id, "enabled": True}
        )
        adapter = container.provider_factory.build(temporary_config)
        success, message = await adapter.test_connection(request.model)
        return ProviderTestResponse(
            provider_id=request.provider_id,
            success=success,
            message=message,
        )
    provider_or_404(request.provider_id)
    return await container.providers.test(request.provider_id, request.model)


# Tasks
@router.get("/tasks", response_model=TaskListResponse, tags=["Tasks"])
async def list_tasks(
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> TaskListResponse:
    items, total = task_service.list_tasks(limit=limit, offset=offset)
    return TaskListResponse(
        items=items, page=PageMeta(total=total, limit=limit, offset=offset)
    )


@router.post("/tasks", response_model=Task, tags=["Tasks"])
async def create_task(request: TaskCreateRequest) -> Task:
    return task_service.create_task(**request.model_dump())


@router.get("/tasks/{task_id}", response_model=Task, tags=["Tasks"])
async def get_task(task_id: str) -> Task:
    task = task_service.get_task(task_id)
    if task is None:
        raise ApiError(
            404, "RESOURCE_NOT_FOUND", "task not found", {"task_id": task_id}
        )
    return task


@router.patch("/tasks/{task_id}", response_model=Task, tags=["Tasks"])
async def update_task(task_id: str, request: TaskUpdateRequest) -> Task:
    return task_service.update_task(task_id, request.model_dump(exclude_unset=True))


@router.delete(
    "/tasks/{task_id}",
    response_model=OperationResponse,
    tags=["Tasks"],
)
async def delete_task(task_id: str) -> OperationResponse:
    if not task_service.delete_task(task_id):
        raise ApiError(
            404, "RESOURCE_NOT_FOUND", "task not found", {"task_id": task_id}
        )
    return OperationResponse(status="completed", resource_id=task_id, message="deleted")


# Media and index
@router.get("/model-routing", response_model=ModelRoutingResponse, tags=["Providers"])
async def get_model_routing() -> ModelRoutingResponse:
    return container.model_routing.describe()


@router.put("/model-routing", response_model=ModelRoutingResponse, tags=["Providers"])
async def update_model_routing(request: ModelRoutingConfig) -> ModelRoutingResponse:
    return container.model_routing.update(request)


@router.post("/models/embeddings", response_model=EmbeddingResult, tags=["Providers"])
async def create_embeddings(request: EmbeddingRequest) -> EmbeddingResult:
    return await container.model_routing.embed(request.texts)


@router.post("/media/speaker-matches", response_model=SpeakerMatchResult, tags=["Media"])
async def match_speakers(request: SpeakerMatchRequest) -> SpeakerMatchResult:
    return await container.model_routing.match_speakers(
        attachment_path(request.attachment_id), attachment_path(request.reference_attachment_id),
        local_only=request.local_only,
    )


@router.post(
    "/media/transcriptions",
    response_model=TranscriptionJob,
    status_code=202,
    tags=["Media"],
)
async def create_transcription(request: TranscriptionRequest) -> TranscriptionJob:
    return await transcription_service.create_transcription(
        **request.model_dump(), wait=False
    )


@router.get(
    "/media/transcriptions/{job_id}",
    response_model=TranscriptionJob,
    tags=["Media"],
)
async def get_transcription(job_id: str) -> TranscriptionJob:
    job = transcription_service.get_transcription(job_id)
    if job is None:
        raise ApiError(
            404, "RESOURCE_NOT_FOUND", "transcription job not found", {"job_id": job_id}
        )
    return job


@router.get("/index/status", response_model=IndexStatus, tags=["Index"])
async def get_index_status() -> IndexStatus:
    return index_service.get_status()


@router.post(
    "/index/rebuild",
    response_model=IndexJob,
    status_code=202,
    tags=["Index"],
)
async def rebuild_index(request: IndexRebuildRequest) -> IndexJob:
    return await index_service.rebuild(request)


@router.get("/index/jobs/{job_id}", response_model=IndexJob, tags=["Index"])
async def get_index_job(job_id: str) -> IndexJob:
    job = index_service.get_job(job_id)
    if job is None:
        raise ApiError(
            404, "RESOURCE_NOT_FOUND", "index job not found", {"job_id": job_id}
        )
    return job


# Benchmark
@router.get(
    "/benchmarks/datasets",
    response_model=BenchmarkDatasetListResponse,
    tags=["Benchmark"],
)
async def list_benchmark_datasets(
    kind: BenchmarkKind = Query(default=BenchmarkKind.rag),
) -> BenchmarkDatasetListResponse:
    return BenchmarkDatasetListResponse(items=benchmark_datasets.list_datasets(kind))


@router.post(
    "/benchmarks/rag/runs",
    response_model=BenchmarkRun,
    status_code=202,
    tags=["Benchmark"],
)
async def create_rag_benchmark(request: RAGRunRequest) -> BenchmarkRun:
    return await benchmark_service.create_rag_run(request)


@router.get(
    "/benchmarks/runs",
    response_model=BenchmarkRunListResponse,
    tags=["Benchmark"],
)
async def list_benchmark_runs(
    kind: BenchmarkKind | None = Query(default=None),
    status: BenchmarkStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> BenchmarkRunListResponse:
    items, total = benchmark_service.list_runs(
        kind=kind, status=status, limit=limit, offset=offset
    )
    return BenchmarkRunListResponse(
        items=items, page=PageMeta(total=total, limit=limit, offset=offset)
    )


@router.get(
    "/benchmarks/runs/{run_id}",
    response_model=BenchmarkRun,
    tags=["Benchmark"],
)
async def get_benchmark_run(run_id: str) -> BenchmarkRun:
    run = benchmark_service.get_run(run_id)
    if run is None:
        raise ApiError(
            404, "BENCHMARK_RUN_NOT_FOUND", "benchmark run not found", {"run_id": run_id}
        )
    return run


@router.post(
    "/benchmarks/runs/{run_id}/cancel",
    response_model=OperationResponse,
    tags=["Benchmark"],
)
async def cancel_benchmark_run(run_id: str) -> OperationResponse:
    run = benchmark_service.cancel_run(run_id)
    if run is None:
        raise ApiError(
            404, "BENCHMARK_RUN_NOT_FOUND", "benchmark run not found", {"run_id": run_id}
        )
    return OperationResponse(
        status="accepted",
        resource_id=run_id,
        message=f"Benchmark run status: {run.status.value}",
    )


@router.get(
    "/benchmarks/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "BenchmarkEvent Server-Sent Events stream",
            "content": {"text/event-stream": {}},
        }
    },
    tags=["Benchmark"],
)
async def benchmark_events(
    run_id: str,
    after_sequence: int = Query(default=-1, ge=-1),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    if benchmark_service.get_run(run_id) is None:
        raise ApiError(
            404, "BENCHMARK_RUN_NOT_FOUND", "benchmark run not found", {"run_id": run_id}
        )

    # SSE 断线重连：Last-Event-ID 优先于 after_sequence，用于从上次收到的事件继续
    cursor = after_sequence
    if last_event_id is not None:
        try:
            cursor = int(last_event_id)
        except ValueError as exc:
            raise ApiError(
                400,
                "BENCHMARK_EVENT_CURSOR_INVALID",
                "Last-Event-ID must be an integer sequence.",
                {"last_event_id": last_event_id},
            ) from exc
        if cursor < -1:
            raise ApiError(
                400,
                "BENCHMARK_EVENT_CURSOR_INVALID",
                "Last-Event-ID must be greater than or equal to -1.",
            )

    async def stream() -> AsyncIterator[str]:
        # 先订阅（保证订阅之后产生的事件也能收到），再回放历史事件，最后实时输出新事件
        terminal = (
            BenchmarkEventType.run_completed,
            BenchmarkEventType.run_failed,
            BenchmarkEventType.run_cancelled,
        )
        queue = benchmark_service.subscribe(run_id)
        try:
            last_sequence = cursor
            # 回放按订阅时刻的快照长度遍历，避免列表在回放期间被追加；终止事件同样要结束流，
            # 防止回放完成后进入实时队列却因序号去重跳过同一终止事件而永久等待。
            history = benchmark_service.get_events(run_id)
            for index in range(len(history)):
                event = history[index]
                if event.sequence <= cursor:
                    continue
                yield as_sse(event.event.value, event.model_dump_json(), event_id=event.sequence)
                last_sequence = event.sequence
                if event.event in terminal:
                    return
            if queue is None:
                return
            while True:
                event = await queue.get()
                if event.sequence <= last_sequence:
                    continue
                yield as_sse(event.event.value, event.model_dump_json(), event_id=event.sequence)
                last_sequence = event.sequence
                if event.event in terminal:
                    return
        finally:
            if queue is not None:
                benchmark_service.unsubscribe(run_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get(
    "/benchmarks/runs/{run_id}/report",
    response_model=BenchmarkReport,
    tags=["Benchmark"],
)
async def get_benchmark_report(run_id: str) -> BenchmarkReport:
    report = benchmark_service.get_report(run_id)
    if report is None:
        raise ApiError(
            404, "BENCHMARK_RUN_NOT_FOUND", "benchmark report not found", {"run_id": run_id}
        )
    return report
