import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse

from app.contracts import (
    AgentRun,
    AgentRunCreateRequest,
    AgentRunListResponse,
    AgentTraceResponse,
    ChatRequest,
    BenchmarkDatasetListResponse,
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
    ModelEvent,
    ModelEventType,
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
    PluginHostStatus,
    PluginListResponse,
    PluginPermissionGrantRequest,
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
from app.errors import ApiError, not_implemented
from app.extensions import ExtensionError
from app.providers.registry import ProviderNotFoundError
from app.providers.factory import UnsupportedProviderError
from app.providers.base import ProviderError
from app.providers.credentials import CredentialStoreError
from app.retrieval.engine import engine
from app.services import (
    index_service,
    note_service,
    task_service,
    transcription_service,
    workspace_service,
)

router = APIRouter(prefix="/api")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    items, total = note_service.list_notes(limit=limit, offset=offset, folder=folder, tag=tag)
    return NoteListResponse(items=items, page=PageMeta(total=total, limit=limit, offset=offset))


@router.post("/notes", response_model=Note, tags=["Notes"])
async def create_note(request: NoteCreateRequest) -> Note:
    return await note_service.create_note(
        title=request.title, markdown=request.markdown, folder=request.folder, tags=request.tags
    )


@router.get("/notes/{note_id}", response_model=Note, tags=["Notes"])
async def get_note(note_id: str) -> Note:
    note = await note_service.get_note(note_id)
    if note is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "note not found", {"note_id": note_id})
    return note


@router.patch("/notes/{note_id}", response_model=Note, tags=["Notes"])
async def update_note(note_id: str, request: NoteUpdateRequest) -> Note:
    return await note_service.update_note(
        note_id, title=request.title, markdown=request.markdown, tags=request.tags
    )


@router.delete("/notes/{note_id}", response_model=OperationResponse, tags=["Notes"])
async def delete_note(note_id: str) -> OperationResponse:
    if not await note_service.delete_note(note_id):
        raise ApiError(404, "RESOURCE_NOT_FOUND", "note not found", {"note_id": note_id})
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
    return await engine.search(request)


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
    provider = provider_or_404(request.provider_id)

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in provider.adapter.stream(request):
                yield as_sse(event.event.value, event.model_dump_json())
        except Exception as exc:
            error = ModelEvent(
                event=ModelEventType.error,
                data={"code": "PROVIDER_ERROR", "message": str(exc)},
                timestamp=utc_now(),
            )
            done = ModelEvent(event=ModelEventType.done, sequence=1, timestamp=utc_now())
            yield as_sse(error.event.value, error.model_dump_json())
            yield as_sse(done.event.value, done.model_dump_json())

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


@router.get(
    "/skills/{skill_id}", response_model=Skill, tags=["Skills"]
)
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
    return OperationResponse(status="completed", resource_id=skill_id, message="uninstalled")


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
    dependent_skills = container.skills.depending_on_tools(plugin.manifest.contributes.tools)
    await extension_call_async(
        lambda: container.plugins.uninstall(plugin_id, dependent_skills)
    )
    return OperationResponse(status="completed", resource_id=plugin_id, message="uninstalled")


# Providers
@router.get(
    "/credentials/{credential_id}",
    response_model=CredentialStatus,
    tags=["Providers"],
)
async def get_credential_status(credential_id: str) -> CredentialStatus:
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
    config = ProviderConfig(
        provider_id=f"provider_{uuid4().hex}",
        provider_type=request.provider_type,
        name=request.name,
        base_url=request.base_url,
        default_model=request.default_model,
        credential_id=request.credential_id,
        enabled=request.enabled,
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
        raise ApiError(409, "BUILTIN_PROVIDER_IMMUTABLE", "Mock provider cannot be modified.")
    fields = request.model_fields_set
    if ("name" in fields and request.name is None) or (
        "enabled" in fields and request.enabled is None
    ):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "name and enabled cannot be null when explicitly provided.",
        )
    updates = {name: getattr(request, name) for name in fields}
    config = ProviderConfig.model_validate(
        {**current.model_dump(mode="python"), **updates}
    )
    adapter = container.provider_factory.build(config)
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
        raise ApiError(409, "BUILTIN_PROVIDER_IMMUTABLE", "Mock provider cannot be deleted.")
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
        raise ApiError(404, "RESOURCE_NOT_FOUND", "task not found", {"task_id": task_id})
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
        raise ApiError(404, "RESOURCE_NOT_FOUND", "task not found", {"task_id": task_id})
    return OperationResponse(status="completed", resource_id=task_id, message="deleted")


# Media and index
@router.post(
    "/media/transcriptions",
    response_model=TranscriptionJob,
    status_code=202,
    tags=["Media"],
)
async def create_transcription(request: TranscriptionRequest) -> TranscriptionJob:
    return transcription_service.create_transcription(
        request.attachment_id, request.language
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
        raise ApiError(404, "RESOURCE_NOT_FOUND", "index job not found", {"job_id": job_id})
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


@router.post(
    "/benchmarks/agent/runs",
    response_model=BenchmarkRun,
    status_code=202,
    tags=["Benchmark"],
)
async def create_agent_benchmark() -> BenchmarkRun:
    # Agent Benchmark 基础设施在 RAG Benchmark 之后单独交付，先占位契约
    not_implemented("Agent Benchmark")
    raise AssertionError("unreachable")


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
        status="completed",
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
) -> StreamingResponse:
    if benchmark_service.get_run(run_id) is None:
        raise ApiError(
            404, "BENCHMARK_RUN_NOT_FOUND", "benchmark run not found", {"run_id": run_id}
        )

    async def stream() -> AsyncIterator[str]:
        for event in benchmark_service.get_events(run_id):
            if event.sequence <= after_sequence:
                continue
            yield as_sse(event.event.value, event.model_dump_json(), event_id=event.sequence)

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
