import sqlite3
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.database import migrations
from app.database.db import _load_extension
from app.errors import ApiError
from app.knowledge.parser import parse_note


def parsed(value):
    return parse_note(markdown='---\nembedding_local_only: '+value+'\n---\nbody', file_path='note.md', folder='',
                      created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))


@pytest.mark.parametrize('value,expected', [('true', True), ('true # keep local', True), ('TRUE # comment', True), ('false # explicit', False)])
def test_policy_parses_yaml_boolean_with_comments(value, expected):
    assert parsed(value).embedding_local_only is expected


@pytest.mark.parametrize('value', ['truth', '1', '', 'null', '"true"', '[true]', '{broken', 'true\nembedding_local_only: false'])
def test_invalid_policy_never_silently_enables_remote(value):
    with pytest.raises(ApiError) as error:
        parsed(value)
    assert error.value.code == 'INVALID_EMBEDDING_POLICY'


def connection(path, factory=sqlite3.Connection):
    conn = sqlite3.connect(path, isolation_level=None, factory=factory)
    conn.row_factory = sqlite3.Row
    _load_extension(conn)
    return conn


def seed_v5(path, monkeypatch):
    conn = connection(path)
    with monkeypatch.context() as patch:
        patch.setattr(migrations, 'MIGRATIONS', migrations.MIGRATIONS[:5])
        migrations.migrate(conn)
    conn.execute("INSERT INTO search_history(query) VALUES ('retained')")
    conn.close()


@pytest.mark.parametrize('failure', [sqlite3.OperationalError, KeyboardInterrupt])
def test_migration_and_version_write_rollback_together(tmp_path, monkeypatch, failure):
    path = tmp_path / 'migration.db'
    seed_v5(path, monkeypatch)
    class Interrupted(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith('INSERT INTO schema_migrations') and parameters[0] == 6:
                raise failure('interrupted')
            return super().execute(sql, parameters)
    conn = connection(path, Interrupted)
    try:
        with pytest.raises(failure):
            migrations.migrate(conn)
        assert not conn.in_transaction
        assert not any(r['name'] == 'embedding_local_only' for r in conn.execute('pragma table_info(blocks)'))
    finally:
        conn.close()
    conn = connection(path)
    try:
        migrations.migrate(conn)
        assert conn.execute('select count(*) from schema_migrations where version=6').fetchone()[0] == 1
        assert conn.execute('select query from search_history').fetchone()[0] == 'retained'
    finally:
        conn.close()


def test_old_partial_v6_recovers_without_duplicate_column(tmp_path, monkeypatch):
    path = tmp_path / 'partial.db'
    seed_v5(path, monkeypatch)
    conn = connection(path)
    try:
        conn.executescript(migrations.MIGRATIONS[5])
        migrations.migrate(conn)
        migrations.migrate(conn)
        assert conn.execute('select count(*) from schema_migrations where version=6').fetchone()[0] == 1
        assert conn.execute('select query from search_history').fetchone()[0] == 'retained'
    finally:
        conn.close()


def test_concurrent_connections_can_upgrade(tmp_path, monkeypatch):
    path = tmp_path / 'concurrent.db'
    seed_v5(path, monkeypatch)
    def upgrade(_):
        conn = connection(path)
        try:
            migrations.migrate(conn)
            return conn.execute('select count(*) from schema_migrations where version=6').fetchone()[0]
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(upgrade, range(2))) == [1, 1]


@pytest.mark.parametrize('header', ['"embedding_local_only": true # comment', '  embedding_local_only: true', 'embedding_local_only:\n  true', 'local: &local true\nembedding_local_only: *local'])
def test_policy_supports_yaml_key_and_scalar_forms(header):
    note = parse_note(markdown='---\n'+header+'\n---\nbody',file_path='note.md',folder='',created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc))
    assert note.embedding_local_only


def test_merge_policy_is_rejected_instead_of_ignored():
    with pytest.raises(ApiError):
        parsed('true\n<<: {embedding_local_only: false}')
    with pytest.raises(ApiError):
        parsed('!!bool invalid')


@pytest.mark.parametrize('bom', ['', '\ufeff'])
@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
@pytest.mark.parametrize('closing', ['---', '...'])
def test_frontmatter_boundaries_preserve_policy_and_utf16_offsets(bom, newline, closing):
    markdown = bom + newline.join(['---  ', 'title: Sample', 'embedding_local_only: true # local', closing+'  ', '# Heading', '', 'private \U0001f600'])
    note = parse_note(markdown=markdown, file_path='note.md', folder='', created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    assert note.embedding_local_only and note.title == 'Sample'
    assert all('embedding_local_only' not in block.content for block in note.blocks)
    block = next(block for block in note.blocks if block.content == 'private \U0001f600')
    original = markdown.encode('utf-16-le')[block.start_offset*2:block.end_offset*2].decode('utf-16-le')
    assert original == block.content


@pytest.mark.parametrize('ending', ['', '\n---not-a-delimiter', '\n----'])
def test_unclosed_frontmatter_is_rejected_even_with_bom(ending):
    for bom in ['', '\ufeff']:
        markdown = bom+'---\nembedding_local_only: true'+ending
        with pytest.raises(ApiError) as error:
            parse_note(markdown=markdown, file_path='note.md', folder='', created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        assert error.value.code == 'INVALID_EMBEDDING_POLICY'


def test_boundary_matching_does_not_truncate_yaml_keys():
    markdown = '---\n---metadata: value\nembedding_local_only: true\n---\nbody'
    note = parse_note(markdown=markdown,file_path='note.md',folder='',created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc))
    assert note.embedding_local_only


def test_bom_save_and_invalid_update_never_use_remote(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.local_models.runtime import LocalEmbedding
    from app.retrieval import routed_vectors
    from app.services import note_service, index_service
    from app.contracts import IndexRebuildRequest
    from app.config import get_settings
    calls=[]
    class Routing:
        async def embed(self, texts, *, local_only=False):
            calls.append(local_only)
            assert local_only
            return SimpleNamespace(source='local', model_id='local-test', dimensions=2, vectors=[[1.0,0.0] for _ in texts], fallback_reason=None)
    monkeypatch.setattr(routed_vectors, 'get_model_routing', lambda: Routing())
    monkeypatch.setattr(note_service, 'embedding', LocalEmbedding())
    async def scenario():
        markdown='\ufeff---\nembedding_local_only: true\n---\nprivate text'
        note=await note_service.create_note(title='Private',markdown=markdown,folder=None,tags=[])
        await index_service.rebuild(IndexRebuildRequest())
        count=len(calls)
        with pytest.raises(ApiError):
            await note_service.update_note(note.note_id,markdown='\ufeff---\nembedding_local_only: true\nprivate text')
        assert len(calls)==count
        assert (get_settings().vault_path/note.file_path).read_text(encoding='utf-8')==markdown
        assert (await note_service.get_note(note.note_id)).markdown==markdown
    asyncio.run(scenario())


@pytest.mark.parametrize('markdown', ['---', '---\n\n# Title\n\nNormal body', '---\n\nNormal body\n\n---\n\nLast paragraph', '---\n\n```python\nprint(1)\n```\n---'])
def test_thematic_breaks_are_not_frontmatter(markdown):
    note = parse_note(markdown=markdown,file_path='ordinary.md',folder='',created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc))
    assert not note.embedding_local_only
    assert note.blocks[0].content == '---'
    assert any(block.content == markdown.split('\n\n')[-1] for block in note.blocks) or '```' in markdown


@pytest.mark.parametrize('header', ['title: Sample\nembedding_local_only: true', '"embedding_local_only": true', 'title: [broken\nembedding_local_only: true', '{embedding_local_only: true'])
def test_unclosed_metadata_still_fails_closed(header):
    with pytest.raises(ApiError) as error:
        parse_note(markdown='---\n'+header,file_path='private.md',folder='',created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc))
    assert error.value.code == 'INVALID_EMBEDDING_POLICY'


def test_thematic_break_note_can_save_and_rebuild():
    import asyncio
    from app.services import note_service, index_service
    from app.contracts import IndexRebuildRequest
    async def scenario():
        markdown='---\n\n# Title\n\nNormal body'
        note=await note_service.create_note(title='Divider',markdown=markdown,folder=None,tags=[])
        assert note.blocks[0].content == '---'
        assert (await index_service.rebuild(IndexRebuildRequest())).status == 'completed'
        loaded=await note_service.get_note(note.note_id)
        assert loaded.markdown == markdown
        assert [b.content for b in loaded.blocks] == [b.content for b in note.blocks]
    asyncio.run(scenario())


def test_thematic_break_with_policy_example_is_ordinary_markdown():
    markdown='---\n\n```yaml\nembedding_local_only: true\n```\n\n---\n\nExplanation'
    note=parse_note(markdown=markdown,file_path='example.md',folder='',created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc))
    assert not note.embedding_local_only
    assert any('embedding_local_only: true' in block.content for block in note.blocks)
    assert note.blocks[0].content=='---'
