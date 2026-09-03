from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.agent.tools import ToolExecutionContext, ToolRegistry
from app.contracts import SearchMode, SearchRequest, TaskStatus, ToolDefinition
from app.retrieval.engine import engine
from app.services import note_service
from app.services import attachment_service, task_service, transcription_service


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EchoArguments(ToolArguments):
    text: str


class AddArguments(ToolArguments):
    left: float
    right: float


class NoteSearchArguments(ToolArguments):
    query: str = Field(min_length=1)
    mode: SearchMode = SearchMode.hybrid
    folders: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class NoteReadArguments(ToolArguments):
    note_id: str = Field(min_length=1)


class NoteCreateArguments(ToolArguments):
    title: str = Field(min_length=1)
    markdown: str = ""
    folder: str | None = None
    tags: list[str] = Field(default_factory=list)


class NoteUpdateArguments(ToolArguments):
    note_id: str = Field(min_length=1)
    title: str | None = None
    markdown: str | None = None
    tags: list[str] | None = None


class NoteListArguments(ToolArguments):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    folder: str | None = None
    tag: str | None = None


class NoteMoveArguments(ToolArguments):
    note_id: str = Field(min_length=1)
    folder: str


class TaskCreateArguments(ToolArguments):
    title: str = Field(min_length=1)
    description: str = ""
    note_id: str | None = None
    due_at: datetime | None = None


class TaskUpdateArguments(ToolArguments):
    task_id: str = Field(min_length=1)
    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    note_id: str | None = None
    due_at: datetime | None = None


class TaskListArguments(ToolArguments):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class AttachmentReadArguments(ToolArguments):
    attachment_id: str = Field(min_length=1)
    max_chars: int = Field(default=100_000, ge=1, le=1_000_000)


class AudioTranscribeArguments(ToolArguments):
    attachment_id: str = Field(min_length=1)
    language: str | None = None


async def echo(arguments: EchoArguments, _: ToolExecutionContext) -> dict[str, str]:
    return {"text": arguments.text}


async def add(arguments: AddArguments, _: ToolExecutionContext) -> dict[str, float]:
    return {"value": arguments.left + arguments.right}


async def search_notes(arguments: NoteSearchArguments, _: ToolExecutionContext) -> dict:
    request = SearchRequest(**arguments.model_dump(), include_snippet=True)
    return (await engine.search(request)).model_dump(mode="json")


async def read_note(arguments: NoteReadArguments, _: ToolExecutionContext) -> dict:
    note = await note_service.get_note(arguments.note_id)
    if note is None:
        raise LookupError(f"Note does not exist: {arguments.note_id}")
    return note.model_dump(mode="json")


async def create_note(arguments: NoteCreateArguments, _: ToolExecutionContext) -> dict:
    note = await note_service.create_note(**arguments.model_dump())
    return note.model_dump(mode="json")


async def update_note(arguments: NoteUpdateArguments, _: ToolExecutionContext) -> dict:
    values = arguments.model_dump()
    note_id = values.pop("note_id")
    note = await note_service.update_note(note_id, **values)
    return note.model_dump(mode="json")


def list_notes(arguments: NoteListArguments, _: ToolExecutionContext) -> dict:
    items, total = note_service.list_notes(**arguments.model_dump())
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "page": {"total": total, "limit": arguments.limit, "offset": arguments.offset},
    }


async def move_note(arguments: NoteMoveArguments, _: ToolExecutionContext) -> dict:
    note = await note_service.move_note(arguments.note_id, folder=arguments.folder)
    return note.model_dump(mode="json")


def create_task(arguments: TaskCreateArguments, _: ToolExecutionContext) -> dict:
    return task_service.create_task(**arguments.model_dump()).model_dump(mode="json")


def update_task(arguments: TaskUpdateArguments, _: ToolExecutionContext) -> dict:
    values = arguments.model_dump(exclude_unset=True)
    task_id = values.pop("task_id")
    return task_service.update_task(task_id, values).model_dump(mode="json")


def list_tasks(arguments: TaskListArguments, _: ToolExecutionContext) -> dict:
    items, total = task_service.list_tasks(**arguments.model_dump())
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "page": {"total": total, "limit": arguments.limit, "offset": arguments.offset},
    }


def read_attachment(arguments: AttachmentReadArguments, _: ToolExecutionContext) -> dict:
    return attachment_service.read_attachment(**arguments.model_dump())


async def transcribe_audio(arguments: AudioTranscribeArguments, _: ToolExecutionContext) -> dict:
    job = await transcription_service.create_transcription(
        arguments.attachment_id, arguments.language
    )
    return job.model_dump(mode="json")


def _register(
    registry: ToolRegistry,
    *,
    name: str,
    description: str,
    arguments_model: type[BaseModel],
    executor,
    permission: str | None = None,
) -> None:
    registry.register(
        ToolDefinition(
            name=name,
            description=description,
            parameters=arguments_model.model_json_schema(),
            permission=permission,
        ),
        arguments_model,
        executor,
    )


def register_builtin_tools(registry: ToolRegistry) -> None:
    _register(
        registry,
        name="system.echo",
        description="Echo text for local Agent integration testing.",
        arguments_model=EchoArguments,
        executor=echo,
    )
    _register(
        registry,
        name="math.add",
        description="Add two numbers without external side effects.",
        arguments_model=AddArguments,
        executor=add,
    )
    _register(
        registry,
        name="notes.search",
        description="Search indexed notes and return snippets with citations.",
        arguments_model=NoteSearchArguments,
        executor=search_notes,
        permission="notes.search",
    )
    _register(
        registry,
        name="rag.search",
        description="Retrieve relevant note blocks for Agent context with citations.",
        arguments_model=NoteSearchArguments,
        executor=search_notes,
        permission="notes.search",
    )
    _register(
        registry,
        name="notes.read",
        description="Read a note and its parsed blocks by note_id.",
        arguments_model=NoteReadArguments,
        executor=read_note,
        permission="notes.read",
    )
    _register(
        registry,
        name="notes.create",
        description="Create a Markdown note in the current Vault.",
        arguments_model=NoteCreateArguments,
        executor=create_note,
        permission="notes.write",
    )
    _register(
        registry,
        name="notes.update",
        description="Update an existing Markdown note.",
        arguments_model=NoteUpdateArguments,
        executor=update_note,
        permission="notes.write",
    )
    _register(
        registry,
        name="notes.list",
        description="List note summaries with folder and tag filters.",
        arguments_model=NoteListArguments,
        executor=list_notes,
        permission="notes.read",
    )
    _register(
        registry,
        name="notes.move",
        description="Move a note to another folder while preserving note_id.",
        arguments_model=NoteMoveArguments,
        executor=move_note,
        permission="notes.write",
    )
    _register(
        registry,
        name="tasks.create",
        description="Create a persistent task.",
        arguments_model=TaskCreateArguments,
        executor=create_task,
        permission="tasks.write",
    )
    _register(
        registry,
        name="tasks.update",
        description="Update a persistent task.",
        arguments_model=TaskUpdateArguments,
        executor=update_task,
        permission="tasks.write",
    )
    _register(
        registry,
        name="tasks.list",
        description="List persistent tasks.",
        arguments_model=TaskListArguments,
        executor=list_tasks,
        permission="tasks.read",
    )
    _register(
        registry,
        name="attachments.read",
        description="Read a UTF-8 attachment from host-managed attachment storage.",
        arguments_model=AttachmentReadArguments,
        executor=read_attachment,
        permission="attachments.read",
    )
    _register(
        registry,
        name="audio.transcribe",
        description="Read a host-generated transcript for an audio attachment.",
        arguments_model=AudioTranscribeArguments,
        executor=transcribe_audio,
        permission="attachments.read",
    )
