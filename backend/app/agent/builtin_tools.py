from pydantic import BaseModel, ConfigDict, Field

from app.agent.tools import ToolExecutionContext, ToolRegistry
from app.contracts import SearchMode, SearchRequest, ToolDefinition
from app.retrieval.engine import engine
from app.services import note_service


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
