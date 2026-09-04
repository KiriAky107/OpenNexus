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
