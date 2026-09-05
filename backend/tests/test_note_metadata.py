import asyncio
from datetime import datetime, timezone

import pytest

from app.contracts import IndexRebuildRequest
from app.knowledge.parser import parse_note
from app.services import index_service, note_service


@pytest.mark.parametrize(('header', 'expected'), [
    ('tags:\n- python\n- rust', ['python', 'rust']),
    ('tags:\n  - python\n  - rust', ['python', 'rust']),
    ('"tags": ["a,b", "quote\\\"tag", "path\\\\tag"] # comment', ['a,b', 'quote"tag', 'path\\tag']),
    ('tags: [on, yes, "true", "001"]', ['on', 'yes', 'true', '001']),
    ('tags: python, rust', ['python', 'rust']),
    ('tags: []', []),
    ('tags: null', []),
])
def test_yaml_tags_are_parsed_as_complete_values(header, expected):
    now = datetime.now(timezone.utc)
    note = parse_note(
        markdown=f'---\ntitle: "Demo: YAML"\n{header}\n---\n# Body',
        file_path='demo.md', folder='', created_at=now, updated_at=now,
    )
    assert note.tags == expected
    assert note.title == 'Demo: YAML'


def test_saved_metadata_survives_full_index_rebuild():
    async def scenario():
        note = await note_service.create_note(title='Demo', markdown='# Body', folder=None, tags=['old'])
        for tags, yaml_tags in [
            (['python', 'a,b', 'on'], '\n  - python\n  - a,b\n  - on'),
            ([], ' []'),
        ]:
            markdown = f'---\ntitle: "Demo: updated"\ntags:{yaml_tags}\n---\n# Body\n'
            saved = await note_service.update_note(note.note_id, markdown=markdown, tags=tags)
            assert saved.tags == tags
            job = await index_service.rebuild(IndexRebuildRequest())
            assert job.status == 'completed'
            restored = await note_service.get_note(note.note_id)
            assert restored.tags == tags
            assert restored.title == 'Demo: updated'
            assert restored.markdown == markdown
    asyncio.run(scenario())
