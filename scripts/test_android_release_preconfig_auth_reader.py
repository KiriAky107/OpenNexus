"""Real SQLite RAM checks for the preconfig closed-column projection only."""
import builtins
from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
import unittest
from unittest.mock import patch

import android_release_preconfig_auth_reader as reader


@contextmanager
def database(*, intent_schema=None, selection_schema=None):
    db = sqlite3.connect(':memory:')
    try:
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute(intent_schema or reader._DDL['auth_intents'])
        db.execute(selection_schema or reader._DDL['auth_selection'])
        db.execute('CREATE TABLE credentials(payload BLOB)')
        db.execute("INSERT INTO credentials VALUES (x'01020304')")
        yield db
    finally:
        db.close()


def login(db, index=1, *, endpoint='https://owned.invalid', account='owned-account'):
    operation = '00000000-0000-4000-8000-' + str(index).zfill(12)
    db.execute('INSERT INTO auth_intents VALUES (?,?,?,?,?,?,?,?,?,?)',
               (operation, endpoint, account, 'owned-phone', 'login', 'active',
                'owned-device', 1, 0, None))
    return operation


class PreconfigAuthReader(unittest.TestCase):
    def refused(self, db):
        with self.assertRaises(ValueError) as raised:
            reader.read_preconfig_index(db)
        self.assertEqual(raised.exception.args, ('SIGNED_PRECONFIG_AUTH_INDEX_REFUSED',))
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)

    def test_native_schema_rows_and_connection_guards(self):
        with database() as db:
            operation = login(db)
            db.execute('INSERT INTO auth_selection VALUES (1,1,?,?,?,1)',
                       ('https://owned.invalid', 'owned-account', operation))
            value = reader.read_preconfig_index(db)
            self.assertEqual(value['intents'][0]['operation_id'], operation)
            self.assertEqual(value['selection']['operation_id'], operation)
            self.assertEqual(set(value), {'intents', 'selection'})
            self.assertEqual(db.execute('PRAGMA query_only').fetchone(), (1,))
            for sql in ['SELECT payload FROM credentials', 'DELETE FROM auth_intents',
                        'PRAGMA query_only=OFF', 'ATTACH DATABASE ":memory:" AS extra',
                        'SELECT length(account) FROM auth_intents']:
                with self.assertRaises(sqlite3.Error):
                    db.execute(sql)

    def test_exact_if_not_exists_native_creation_and_empty_selection(self):
        with database(intent_schema=reader._DDL['auth_intents'].replace(
                'CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ')) as db:
            self.assertEqual(reader.read_preconfig_index(db), dict(intents=[], selection=None))

    def test_actual_native_source_ddl_matches_the_closed_reader(self):
        source = Path(__file__).resolve().parents[1] / 'frontend/src-tauri/src/mobile_sync_session.rs'
        raw = source.read_bytes()
        self.assertLessEqual(len(raw), 256 * 1024)
        ddl = {}
        for table in ('auth_intents', 'auth_selection'):
            matches = re.findall(r'CREATE TABLE IF NOT EXISTS ' + table + r'\([^;]+\);',
                                 raw.decode('utf-8'), re.DOTALL)
            self.assertEqual(len(matches), 1)
            ddl[table] = matches[0][:-1]
        with database(intent_schema=ddl['auth_intents'], selection_schema=ddl['auth_selection']) as db:
            operation = login(db)
            self.assertEqual(reader.read_preconfig_index(db)['intents'][0]['operation_id'], operation)

    def test_view_refused_before_private_function_or_rows(self):
        with database(intent_schema='CREATE TABLE placeholder(value)') as db:
            calls = []
            db.create_function('private_payload', 0, lambda: calls.append(1) or 'private')
            db.execute('CREATE VIEW auth_intents AS SELECT private_payload() AS operation_id')
            self.refused(db)
            self.assertEqual(calls, [])
            with self.assertRaises(sqlite3.Error):
                db.execute('SELECT payload FROM credentials')

    def test_extra_column_and_changed_literal_refused(self):
        changed = [reader._DDL['auth_intents'][:-1] + ',payload BLOB)',
                   reader._DDL['auth_intents'].replace("'active'", "'act ive'")]
        for schema in changed:
            with self.subTest(schema_kind=changed.index(schema)), database(intent_schema=schema) as db:
                self.refused(db)

    def test_missing_table_and_non_native_selection_refused(self):
        for schema in ['CREATE TABLE placeholder(value)',
                       'CREATE TABLE auth_selection(revision,endpoint,account,operation_id,generation)']:
            with self.subTest(schema_kind=schema.split('(')[0]), database(selection_schema=schema) as db:
                self.refused(db)

    def test_sixteen_intents_allowed_seventeen_refused(self):
        for count in [16, 17]:
            with self.subTest(count=count), database() as db:
                for index in range(count):
                    login(db, index)
                if count == 16:
                    self.assertEqual(len(reader.read_preconfig_index(db)['intents']), 16)
                else:
                    self.refused(db)

    def test_blob_and_utf8_cell_bounds_refused_without_coercion(self):
        for value in [sqlite3.Binary(b'private'), 'x' * 2049, '\u4e2d' * 683]:
            with self.subTest(value_kind=type(value).__name__), database() as db:
                login(db, account=value)
                self.refused(db)

    def test_row_and_text_factories_refused_before_query(self):
        with database() as db:
            statements = []
            db.set_trace_callback(statements.append)
            db.row_factory = sqlite3.Row
            self.refused(db)
            self.assertEqual(statements, [])
            db.row_factory = None
            db.text_factory = bytes
            self.refused(db)
            self.assertEqual(statements, [])

    def test_nonconnection_and_closed_connection_static_error(self):
        class Private:
            def __str__(self):
                raise AssertionError('private coercion')
        self.refused(Private())
        db = sqlite3.connect(':memory:')
        db.close()
        self.refused(db)

    def test_no_file_or_external_sql_connection(self):
        with database() as db:
            login(db)
            with patch.object(builtins, 'open', side_effect=AssertionError('file IO')), \
                 patch.object(sqlite3, 'connect', side_effect=AssertionError('new DB')):
                self.assertEqual(len(reader.read_preconfig_index(db)['intents']), 1)

    def test_progress_handler_has_finite_sqlite_budget(self):
        with database() as db:
            for index in range(16):
                login(db, index)
            reader.read_preconfig_index(db)
            with self.assertRaises(sqlite3.OperationalError) as raised:
                db.execute('SELECT a.operation_id,b.operation_id,c.operation_id,d.operation_id,e.operation_id FROM auth_intents a,auth_intents b,'
                           'auth_intents c,auth_intents d,auth_intents e').fetchall()
            self.assertEqual(str(raised.exception), 'interrupted')


if __name__ == '__main__':
    unittest.main()
