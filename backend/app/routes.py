from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.contracts import (
    AgentRun,
    AgentRunCreateRequest,
    AgentRunListResponse,
    ChatRequest,
    ErrorResponse,
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
    ProviderConfig,
    ProviderCreateRequest,
    ProviderListResponse,
    ProviderModelsResponse,
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
from app.agent import AgentRunNotFoundError
from app.container import container
from app.errors import ApiError, not_implemented
from app.providers.registry import ProviderNotFoundError

router = APIRouter(prefix="/api")
not_implemented_response = {501: {"model": ErrorResponse, "description": "业务服务尚未实现"}}


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


# Notes
@router.get("/notes", response_model=NoteListResponse, tags=["Notes"])
async def list_notes(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    folder: str | None = None,
    tag: str | None = None,
) -> NoteListResponse:
    return NoteListResponse(page=PageMeta(limit=limit, offset=offset))


@router.post(
    "/notes", response_model=Note, responses=not_implemented_response, tags=["Notes"]
)
async def create_note(_: NoteCreateRequest) -> Note:
    not_implemented("notes.create")


@router.get(
    "/notes/{note_id}", response_model=Note, responses=not_implemented_response, tags=["Notes"]
)
async def get_note(note_id: str) -> Note:
    not_implemented(f"notes.read:{note_id}")


@router.patch(
    "/notes/{note_id}", response_model=Note, responses=not_implemented_response, tags=["Notes"]
)
async def update_note(note_id: str, _: NoteUpdateRequest) -> Note:
    not_implemented(f"notes.update:{note_id}")


@router.delete(
    "/notes/{note_id}",
    response_model=OperationResponse,
    responses=not_implemented_response,
    tags=["Notes"],
)
async def delete_note(note_id: str) -> OperationResponse:
    not_implemented(f"notes.delete:{note_id}")


@router.post(
    "/notes/{note_id}/move", response_model=Note, responses=not_implemented_response, tags=["Notes"]
)
async def move_note(note_id: str, _: NoteMoveRequest) -> Note:
    not_implemented(f"notes.move:{note_id}")


# Retrieval and chat
@router.post("/search", response_model=SearchResponse, tags=["Search"])
async def search_notes(request: SearchRequest) -> SearchResponse:
    return SearchResponse(
        query=request.query,
        mode=request.mode,
        page=PageMeta(limit=request.limit, offset=request.offset),
    )


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
    responses=not_implemented_response,
    tags=["Agent"],
)
async def create_agent_run(request: AgentRunCreateRequest) -> AgentRun:
    provider_or_404(request.provider_id)
    return await container.agent.create_run(request)


@router.get(
    "/agent/runs/{run_id}",
    response_model=AgentRun,
    responses=not_implemented_response,
    tags=["Agent"],
)
async def get_agent_run(run_id: str) -> AgentRun:
    return agent_run_or_404(run_id)


@router.post(
    "/agent/runs/{run_id}/cancel",
    response_model=OperationResponse,
    responses=not_implemented_response,
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
    responses=not_implemented_response,
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
    return SkillListResponse()


@router.get(
    "/skills/{skill_id}", response_model=Skill, responses=not_implemented_response, tags=["Skills"]
)
async def get_skill(skill_id: str) -> Skill:
    not_implemented(f"skills.read:{skill_id}")


@router.post(
    "/skills/install",
    response_model=Skill,
    status_code=202,
    responses=not_implemented_response,
    tags=["Skills"],
)
async def install_skill(_: ExtensionInstallRequest) -> Skill:
    not_implemented("skills.install")


@router.post(
    "/skills/{skill_id}/enable",
    response_model=Skill,
    responses=not_implemented_response,
    tags=["Skills"],
)
async def enable_skill(skill_id: str) -> Skill:
    not_implemented(f"skills.enable:{skill_id}")


@router.post(
    "/skills/{skill_id}/disable",
    response_model=Skill,
    responses=not_implemented_response,
    tags=["Skills"],
)
async def disable_skill(skill_id: str) -> Skill:
    not_implemented(f"skills.disable:{skill_id}")


@router.delete(
    "/skills/{skill_id}",
    response_model=OperationResponse,
    responses=not_implemented_response,
    tags=["Skills"],
)
async def uninstall_skill(skill_id: str) -> OperationResponse:
    not_implemented(f"skills.uninstall:{skill_id}")


# Plugins
@router.get("/plugins", response_model=PluginListResponse, tags=["Plugins"])
async def list_plugins() -> PluginListResponse:
    return PluginListResponse()


@router.get(
    "/plugins/{plugin_id}",
    response_model=Plugin,
    responses=not_implemented_response,
    tags=["Plugins"],
)
async def get_plugin(plugin_id: str) -> Plugin:
    not_implemented(f"plugins.read:{plugin_id}")


@router.post(
    "/plugins/install",
    response_model=Plugin,
    status_code=202,
    responses=not_implemented_response,
    tags=["Plugins"],
)
async def install_plugin(_: ExtensionInstallRequest) -> Plugin:
    not_implemented("plugins.install")


@router.post(
    "/plugins/{plugin_id}/enable",
    response_model=Plugin,
    responses=not_implemented_response,
    tags=["Plugins"],
)
async def enable_plugin(plugin_id: str) -> Plugin:
    not_implemented(f"plugins.enable:{plugin_id}")


@router.post(
    "/plugins/{plugin_id}/disable",
    response_model=Plugin,
    responses=not_implemented_response,
    tags=["Plugins"],
)
async def disable_plugin(plugin_id: str) -> Plugin:
    not_implemented(f"plugins.disable:{plugin_id}")


@router.delete(
    "/plugins/{plugin_id}",
    response_model=OperationResponse,
    responses=not_implemented_response,
    tags=["Plugins"],
)
async def uninstall_plugin(plugin_id: str) -> OperationResponse:
    not_implemented(f"plugins.uninstall:{plugin_id}")


# Providers
@router.get("/providers", response_model=ProviderListResponse, tags=["Providers"])
async def list_providers() -> ProviderListResponse:
    return ProviderListResponse(items=container.providers.list_configs())


@router.get(
    "/providers/{provider_id}",
    response_model=ProviderConfig,
    responses=not_implemented_response,
    tags=["Providers"],
)
async def get_provider(provider_id: str) -> ProviderConfig:
    return provider_or_404(provider_id).config.model_copy(deep=True)


@router.post(
    "/providers",
    response_model=ProviderConfig,
    responses=not_implemented_response,
    tags=["Providers"],
)
async def create_provider(_: ProviderCreateRequest) -> ProviderConfig:
    not_implemented("providers.create")


@router.patch(
    "/providers/{provider_id}",
    response_model=ProviderConfig,
    responses=not_implemented_response,
    tags=["Providers"],
)
async def update_provider(provider_id: str, _: ProviderUpdateRequest) -> ProviderConfig:
    not_implemented(f"providers.update:{provider_id}")


@router.delete(
    "/providers/{provider_id}",
    response_model=OperationResponse,
    responses=not_implemented_response,
    tags=["Providers"],
)
async def delete_provider(provider_id: str) -> OperationResponse:
    not_implemented(f"providers.delete:{provider_id}")


@router.get(
    "/providers/{provider_id}/models",
    response_model=ProviderModelsResponse,
    responses=not_implemented_response,
    tags=["Providers"],
)
async def list_provider_models(provider_id: str) -> ProviderModelsResponse:
    provider_or_404(provider_id)
    return ProviderModelsResponse(
        provider_id=provider_id,
        items=await container.providers.list_models(provider_id),
    )


@router.post(
    "/providers/test",
    response_model=ProviderTestResponse,
    responses=not_implemented_response,
    tags=["Providers"],
)
async def test_provider(request: ProviderTestRequest) -> ProviderTestResponse:
    provider_or_404(request.provider_id)
    return await container.providers.test(request.provider_id, request.model)


# Tasks
@router.get("/tasks", response_model=TaskListResponse, tags=["Tasks"])
async def list_tasks(
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> TaskListResponse:
    return TaskListResponse(page=PageMeta(limit=limit, offset=offset))


@router.post(
    "/tasks", response_model=Task, responses=not_implemented_response, tags=["Tasks"]
)
async def create_task(_: TaskCreateRequest) -> Task:
    not_implemented("tasks.create")


@router.get(
    "/tasks/{task_id}", response_model=Task, responses=not_implemented_response, tags=["Tasks"]
)
async def get_task(task_id: str) -> Task:
    not_implemented(f"tasks.read:{task_id}")


@router.patch(
    "/tasks/{task_id}", response_model=Task, responses=not_implemented_response, tags=["Tasks"]
)
async def update_task(task_id: str, _: TaskUpdateRequest) -> Task:
    not_implemented(f"tasks.update:{task_id}")


@router.delete(
    "/tasks/{task_id}",
    response_model=OperationResponse,
    responses=not_implemented_response,
    tags=["Tasks"],
)
async def delete_task(task_id: str) -> OperationResponse:
    not_implemented(f"tasks.delete:{task_id}")


# Media and index
@router.post(
    "/media/transcriptions",
    response_model=TranscriptionJob,
    status_code=202,
    responses=not_implemented_response,
    tags=["Media"],
)
async def create_transcription(_: TranscriptionRequest) -> TranscriptionJob:
    not_implemented("media.transcriptions.create")


@router.get(
    "/media/transcriptions/{job_id}",
    response_model=TranscriptionJob,
    responses=not_implemented_response,
    tags=["Media"],
)
async def get_transcription(job_id: str) -> TranscriptionJob:
    not_implemented(f"media.transcriptions.read:{job_id}")


@router.get("/index/status", response_model=IndexStatus, tags=["Index"])
async def get_index_status() -> IndexStatus:
    return IndexStatus()


@router.post(
    "/index/rebuild",
    response_model=IndexJob,
    status_code=202,
    responses=not_implemented_response,
    tags=["Index"],
)
async def rebuild_index(_: IndexRebuildRequest) -> IndexJob:
    not_implemented("index.rebuild")


@router.get(
    "/index/jobs/{job_id}",
    response_model=IndexJob,
    responses=not_implemented_response,
    tags=["Index"],
)
async def get_index_job(job_id: str) -> IndexJob:
    not_implemented(f"index.jobs.read:{job_id}")
