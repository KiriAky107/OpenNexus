import asyncio
import json

from app.agent.note_references import NoteReferences
from app.contracts import ChatRequest, Message, SearchRequest
from app.services import note_service
from app.services.chat_context import prepare


def test_nested_references_are_stable_reversible_and_run_local():
    references = NoteReferences()
    raw = {"note_id": "note_abcdef", "content_hash": "a" * 64,
           "items": [{"citation_id": "cit_blk_123", "note_id": "note_abcdef", "block_id": "blk_123"}],
           "markdown": "User-authored hash aabbcc must remain unchanged"}
    public = references.transform(raw)
    assert public["note_id"] == "note:1"
    assert public["items"][0]["note_id"] == "note:1"
    assert "abcdef" not in json.dumps(public)
    assert "a" * 64 not in json.dumps(public)
    assert references.transform(public, restore=True) == raw
    assert references.transform({"expected_content_hash": "revision:1", "note_ids": ["note:1"]}, restore=True) == {
        "expected_content_hash": "a" * 64, "note_ids": ["note_abcdef"]}
    assert NoteReferences().transform({"note_id": "note:1"}, restore=True)["note_id"] == "note:1"


def test_public_tool_schema_accepts_revision_references_without_changing_runtime_schema():
    from app.agent.markdown_tools import PatchArguments
    original = PatchArguments.model_json_schema()
    public = NoteReferences.tool_parameters(original)
    assert "pattern" not in public["properties"]["expected_content_hash"]
    assert "pattern" in original["properties"]["expected_content_hash"]


def test_chat_context_keeps_internal_locators_out_of_model_input():
    async def scenario():
        note = await note_service.create_note(title="Readable orchard", markdown="apple orchard", folder=None, tags=[])
        grounded, sources = await prepare(ChatRequest(provider_id="mock", model="mock-1", use_rag=True,
            messages=[Message(role="user", content="apple")], retrieval=SearchRequest(query="apple", mode="fts")))
        assert sources
        assert note.note_id not in grounded.system
        assert sources[0]["block_id"] not in grounded.system
        assert "Readable orchard" in grounded.system
        assert sources[0]["number"] == 1
    asyncio.run(scenario())


def test_agent_restores_note_and_revision_before_validated_write(monkeypatch):
    """The model sees references; the actual write still checks the stored revision."""
    import hashlib
    from pydantic import BaseModel
    from app.agent.markdown_tools import PatchArguments, patch
    from app.agent.permissions import PermissionMode
    from app.container import build_container
    from app.contracts import AgentRunCreateRequest, AgentRunStatus, ToolDefinition
    from app.providers.base import ProviderToolCall, ProviderTurn

    class LookupArguments(BaseModel):
        pass

    async def scenario():
        note = await note_service.create_note(title="Readable source", markdown="before", folder=None, tags=[])
        revision = hashlib.sha256(note.markdown.encode()).hexdigest()
        container = build_container()

        async def lookup(arguments, context):
            return {"note_id": note.note_id, "title": note.title, "content_hash": revision}

        container.tools.register(ToolDefinition(name="qa.lookup", description="Read source", parameters=LookupArguments.model_json_schema()), LookupArguments, lookup)
        container.tools.register(ToolDefinition(name="qa.patch", description="Update source", parameters=PatchArguments.model_json_schema(), permission="notes.write"), PatchArguments, patch)
        container.permissions.policy.set_rule("notes.write", PermissionMode.allow)
        calls = 0

        async def complete(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ProviderTurn(tool_calls=[ProviderToolCall(tool_call_id="lookup", name="qa.lookup", arguments={})])
            model_context = json.dumps([m.model_dump(mode="json") for m in request.messages])
            assert note.note_id not in model_context
            assert revision not in model_context
            if calls == 2:
                source = json.loads(request.messages[-1].content)["output"]
                assert source["note_id"] == "note:1"
                assert source["content_hash"] == "revision:1"
                return ProviderTurn(tool_calls=[ProviderToolCall(tool_call_id="patch", name="qa.patch", arguments={"note_id": source["note_id"], "expected_content_hash": source["content_hash"], "old_text": "before", "new_text": "after"})])
            assert json.loads(request.messages[-1].content)["success"]
            return ProviderTurn(text="Updated Readable source.")

        monkeypatch.setattr(container.providers.get("mock").adapter, "complete", complete)
        run = await container.agent.create_run(AgentRunCreateRequest(input="Update the source", provider_id="mock", model="mock-1", allowed_tools=["qa.lookup", "qa.patch"]))
        result = await container.agent.wait(run.run_id)
        assert result.status == AgentRunStatus.completed
        assert (await note_service.get_note(note.note_id)).markdown == "after"
        assert result.tool_results[0].output["note_id"] == note.note_id

    asyncio.run(scenario())
