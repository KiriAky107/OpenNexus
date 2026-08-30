from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.contracts import (
    AgentRun,
    AgentRunCreateRequest,
    AgentRunListResponse,
    ChatRequest,
    ExtensionInstallRequest,
    IndexJob,
    IndexRebuildRequest,
    IndexStatus,
    ModelEvent,
    ModelEventType,
    Note,
    NoteCreateRequest,
    NoteListResponse,
    NoteMoveRequest,
    NoteUpdateRequest,
    OperationResponse,
    PageMeta,
    PermissionDecisionRequest,
    Plugin,
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
)
from app.agent import AgentCapacityError, AgentRunNotFoundError
from app.container import container
from app.errors import ApiError
from app.extensions import ExtensionError
from app.providers.registry import ProviderNotFoundError
from app.providers.factory import UnsupportedProviderError
from app.providers.base import ProviderError
from app.retrieval.engine import engine
from app.services import index_service, note_service, task_service, transcription_service

router = APIRouter(prefix="/api")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_sse(event: str, payload: str) -> str:
    return f"event: {event}\ndata: {payload}\n\n"


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
async def agent_events(run_id: str) -> StreamingResponse:
    agent_run_or_404(run_id)

    async def stream() -> AsyncIterator[str]:
        async for event in container.agent.events(run_id):
            yield as_sse(event.event.value, event.model_dump_json())

    return StreamingResponse(stream(), media_type="text/event-stream")


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
    return extension_call(lambda: container.plugins.enable(plugin_id))


@router.post(
    "/plugins/{plugin_id}/disable",
    response_model=Plugin,
    tags=["Plugins"],
)
async def disable_plugin(plugin_id: str) -> Plugin:
    return extension_call(lambda: container.plugins.disable(plugin_id))


@router.put(
    "/plugins/{plugin_id}/permissions",
    response_model=Plugin,
    tags=["Plugins"],
)
async def set_plugin_permissions(
    plugin_id: str, request: PluginPermissionGrantRequest
) -> Plugin:
    return extension_call(
        lambda: container.plugins.set_permissions(plugin_id, request.permissions)
    )


@router.delete(
    "/plugins/{plugin_id}",
    response_model=OperationResponse,
    tags=["Plugins"],
)
async def uninstall_plugin(plugin_id: str) -> OperationResponse:
    plugin = extension_call(lambda: container.plugins.get(plugin_id))
    dependent_skills = container.skills.depending_on_tools(plugin.manifest.contributes.tools)
    extension_call(lambda: container.plugins.uninstall(plugin_id, dependent_skills))
    return OperationResponse(status="completed", resource_id=plugin_id, message="uninstalled")


# Providers
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
