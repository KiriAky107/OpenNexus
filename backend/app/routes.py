from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.contracts import (
    AgentEvent,
    AgentEventType,
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
from app.errors import not_implemented

router = APIRouter(prefix="/api")
not_implemented_response = {501: {"model": ErrorResponse, "description": "业务服务尚未实现"}}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_sse(event: str, payload: str) -> str:
    return f"event: {event}\ndata: {payload}\n\n"


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
async def chat(_: ChatRequest) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        error = ModelEvent(
            event=ModelEventType.error,
            data={"code": "NOT_IMPLEMENTED", "message": "Chat runtime is not implemented."},
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
    return AgentRunListResponse(page=PageMeta(limit=limit, offset=offset))


@router.post(
    "/agent/runs",
    response_model=AgentRun,
    status_code=202,
    responses=not_implemented_response,
    tags=["Agent"],
)
async def create_agent_run(_: AgentRunCreateRequest) -> AgentRun:
    not_implemented("agent.runs.create")


@router.get(
    "/agent/runs/{run_id}",
    response_model=AgentRun,
    responses=not_implemented_response,
    tags=["Agent"],
)
async def get_agent_run(run_id: str) -> AgentRun:
    not_implemented(f"agent.runs.read:{run_id}")


@router.post(
    "/agent/runs/{run_id}/cancel",
    response_model=OperationResponse,
    responses=not_implemented_response,
    tags=["Agent"],
)
async def cancel_agent_run(run_id: str) -> OperationResponse:
    not_implemented(f"agent.runs.cancel:{run_id}")


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
    async def stream() -> AsyncIterator[str]:
        event = AgentEvent(
            event=AgentEventType.run_failed,
            run_id=run_id,
            sequence=0,
            data={"code": "NOT_IMPLEMENTED", "message": "Agent runtime is not implemented."},
            timestamp=utc_now(),
        )
        yield as_sse(event.event.value, event.model_dump_json())

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post(
    "/agent/runs/{run_id}/permissions/{request_id}",
    response_model=OperationResponse,
    responses=not_implemented_response,
    tags=["Agent"],
)
async def decide_agent_permission(
    run_id: str, request_id: str, _: PermissionDecisionRequest
) -> OperationResponse:
    not_implemented(f"agent.permissions:{run_id}:{request_id}")


@router.get("/tools", response_model=ToolListResponse, tags=["Agent"])
async def list_tools() -> ToolListResponse:
    return ToolListResponse()


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
    return ProviderListResponse()


@router.get(
    "/providers/{provider_id}",
    response_model=ProviderConfig,
    responses=not_implemented_response,
    tags=["Providers"],
)
async def get_provider(provider_id: str) -> ProviderConfig:
    not_implemented(f"providers.read:{provider_id}")


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
    not_implemented(f"providers.models:{provider_id}")


@router.post(
    "/providers/test",
    response_model=ProviderTestResponse,
    responses=not_implemented_response,
    tags=["Providers"],
)
async def test_provider(_: ProviderTestRequest) -> ProviderTestResponse:
    not_implemented("providers.test")


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
