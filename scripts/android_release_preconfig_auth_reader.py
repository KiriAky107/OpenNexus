"""Closed SQL projection of an already-owned private auth working copy.

This opens no path and admits no copy, Stop, command completion or runtime.
The owning preconfig factory must establish those sources before calling it.
Returned metadata is not an admission token or a Native acknowledgement.
"""
import re
import sqlite3


_ERROR = 'SIGNED_PRECONFIG_AUTH_INDEX_REFUSED'
_INTENTS = ('operation_id', 'endpoint', 'account', 'device_name', 'kind',
            'phase', 'device_id', 'generation', 'server_logout_confirmed',
            'previous_operation')
_SELECTION = ('revision', 'endpoint', 'account', 'operation_id', 'generation')
# Exact current native schema; unrelated metadata/credential tables are not read.
_DDL = {
    'auth_intents': """CREATE TABLE auth_intents(operation_id TEXT PRIMARY KEY,
        endpoint TEXT NOT NULL,account TEXT NOT NULL,device_name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('login','refresh','logout','local_disconnect')),
        phase TEXT NOT NULL CHECK(phase IN ('login_pending','store_pending','active',
        'refresh_pending','logout_pending','erase_pending','disconnected','unknown')),
        device_id TEXT,generation INTEGER NOT NULL CHECK(generation>0),
        server_logout_confirmed INTEGER NOT NULL DEFAULT 0
        CHECK(server_logout_confirmed IN (0,1)),previous_operation TEXT)""",
    'auth_selection': """CREATE TABLE auth_selection(singleton INTEGER PRIMARY KEY
        CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>0),
        endpoint TEXT NOT NULL,account TEXT NOT NULL,operation_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0))""",
}
_TOKEN = re.compile(r"\s+|'[^']*'|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[(),=><]")
_READS = {
    'sqlite_master': frozenset(('name', 'type', 'sql')),
    'auth_intents': frozenset((*_INTENTS, 'ROWID')),
    'auth_selection': frozenset(_SELECTION),
}


def _need(value):
    if not value:
        raise ValueError(_ERROR)


def _tokens(value):
    _need(type(value) is str and 0 < len(value) <= 4096)
    result, position = [], 0
    for match in _TOKEN.finditer(value):
        _need(match.start() == position)
        token = match.group()
        position = match.end()
        if not token.isspace():
            result.append(token if token.startswith("'") else token.lower())
    _need(position == len(value))
    return tuple(result)


def _authorizer(action, table, column, database, origin):
    # Origin is non-None for a view/trigger expansion; neither is permitted.
    if database not in (None, 'main') or origin is not None:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return (sqlite3.SQLITE_OK if table in _READS and column in _READS[table]
                else sqlite3.SQLITE_DENY)
    if action == sqlite3.SQLITE_PRAGMA and table == 'query_only' and column in (None, 'ON'):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _rows(db, statement, maximum):
    cursor = db.execute(statement)
    try:
        rows = cursor.fetchmany(maximum + 1)
        _need(len(rows) <= maximum)
        return rows
    finally:
        cursor.close()


def _scalars(rows, width):
    for row in rows:
        _need(type(row) is tuple and len(row) == width)
        for value in row:
            if value is None:
                continue
            if type(value) is int:
                _need(-(2**63) <= value < 2**63)
            else:
                _need(type(value) is str and len(value) <= 2048)
                _need(len(value.encode('utf-8', 'strict')) <= 2048)


def read_preconfig_index(db):
    """Select bounded nonsecret rows; retain restrictive connection guards.

    The caller retains and closes its working connection, including on failure.
    No original file is opened, no private exception is formatted, and no claim
    about caller/copy provenance follows from the connection's exact type.
    """
    try:
        _need(type(db) is sqlite3.Connection)
        _need(db.row_factory is None and db.text_factory is str)
        remaining = 128

        def budget():
            nonlocal remaining
            remaining -= 1
            return int(remaining <= 0)

        db.set_authorizer(_authorizer)
        db.set_progress_handler(budget, 1000)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4096)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 2048)
        db.execute('PRAGMA query_only=ON').close()
        _need(_rows(db, 'PRAGMA query_only', 1) == [(1,)])
        schema = _rows(db, "SELECT name,type,sql FROM sqlite_master "
                       "WHERE name IN ('auth_intents','auth_selection') ORDER BY name LIMIT 3", 2)
        _need(len(schema) == 2)
        for row, name in zip(schema, ('auth_intents', 'auth_selection')):
            _need(type(row) is tuple and len(row) == 3)
            _need(type(row[0]) is str and row[0] == name and type(row[1]) is str and row[1] == 'table')
            _need(_tokens(row[2]) == _tokens(_DDL[name]))
        intents = _rows(db, 'SELECT ' + ','.join(_INTENTS)
                        + ' FROM auth_intents ORDER BY rowid LIMIT 17', 16)
        selected = _rows(db, 'SELECT ' + ','.join(_SELECTION)
                         + ' FROM auth_selection LIMIT 2', 1)
        _scalars(intents, len(_INTENTS)); _scalars(selected, len(_SELECTION))
        return dict(intents=[dict(zip(_INTENTS, row)) for row in intents],
                    selection=dict(zip(_SELECTION, selected[0])) if selected else None)
    except BaseException:
        raise ValueError(_ERROR) from None
