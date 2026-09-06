import asyncio
import hashlib
from typing import get_args
import pytest
from app.agent.markdown_tools import ComposeArguments, Format, PatchArguments, compose, patch, register
from app.agent.tools import ToolRegistry
from app.services import note_service


@pytest.mark.parametrize('kind', get_args(Format))
def test_all_registered_formats_compose(kind):
    result = compose(ComposeArguments(format=kind, text='Example', items=['one', 'two'], rows=[['A', 'B'], ['C', 'D']], url='https://example.com', title='Title', tags=['tag']), None)
    assert result['markdown']
    assert result['persisted'] is False


def test_fences_tables_and_permissions():
    assert compose(ComposeArguments(format='code-block', text='```'), None)['markdown'].startswith('````\n')
    with pytest.raises(ValueError): compose(ComposeArguments(format='table', rows=[['a'], ['b', 'c']]), None)
    registry = ToolRegistry()
    register(registry)
    assert registry.get('notes.patch_markdown').definition.permission == 'notes.write'
    assert registry.get('markdown.compose').definition.permission is None


def test_patch_preserves_unrelated_content_and_rejects_stale_version():
    async def run():
        note = await note_service.create_note(title='Patch test', markdown='before\n\nold\n\nafter', folder=None, tags=[])
        args = PatchArguments(note_id=note.note_id, expected_content_hash=hashlib.sha256(note.markdown.encode()).hexdigest(), old_text='old', new_text='> [!NOTE]\n> new')
        await patch(args, None)
        updated = await note_service.get_note(note.note_id)
        assert updated.markdown == 'before\n\n> [!NOTE]\n> new\n\nafter'
        with pytest.raises(ValueError): await patch(args, None)
    asyncio.run(run())


def test_metadata_patch_updates_index_tags():
    async def run():
        markdown = '---\ntitle: Old\ntags: [old]\n---\nBody'
        note = await note_service.create_note(title='Old', markdown=markdown, folder=None, tags=[])
        await patch(PatchArguments(note_id=note.note_id, expected_content_hash=hashlib.sha256(markdown.encode()).hexdigest(), old_text='tags: [old]', new_text='tags: [new]'), None)
        updated = await note_service.get_note(note.note_id)
        assert updated.tags == ['new']
        assert updated.markdown.endswith('Body')
    asyncio.run(run())
