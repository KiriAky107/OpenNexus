"""Stable branch windows: walk IDs/parents in SQL, decode only requested bodies."""
from contextlib import closing
import base64
import hashlib
import json
import os

from app.contracts import Message
from app.database.db import connect_knowledge, transaction
from app.errors import ApiError


def _scope(conn) -> str:
    path = conn.execute('PRAGMA database_list').fetchone()['file']
    return hashlib.sha256(os.path.normcase(os.path.realpath(path)).encode()).hexdigest()


def _conversation(conn, conversation_id):
    row = conn.execute('SELECT active_leaf FROM chat_conversations WHERE conversation_id=?', (conversation_id,)).fetchone()
    if row is None:
        raise ApiError(404, 'CONVERSATION_NOT_FOUND', '当前知识库中找不到该会话。')
    return row['active_leaf']


def _path(conn, conversation_id, leaf):
    # UNION without a depth column terminates cycles; no JSON/body columns enter
    # this relation. Ordering uses the saved parent chain, not all conversation rows.
    conn.execute('''CREATE TEMP TABLE chat_window_path AS
        WITH RECURSIVE ancestors(message_id,parent_message_id,sequence,role) AS (
          SELECT message_id,parent_message_id,sequence,role FROM chat_messages INDEXED BY chat_parent_metadata
          WHERE conversation_id=? AND message_id=?
          UNION
          SELECT m.message_id,m.parent_message_id,m.sequence,m.role FROM ancestors a
          CROSS JOIN chat_messages m INDEXED BY chat_parent_metadata
          WHERE m.conversation_id=? AND m.message_id=a.parent_message_id
        ) SELECT *,row_number() OVER (ORDER BY sequence)-1 AS position FROM ancestors''',
        (conversation_id, leaf, conversation_id))
    total = conn.execute('SELECT COUNT(*) FROM chat_window_path').fetchone()[0]
    conn.execute('CREATE UNIQUE INDEX chat_window_position ON chat_window_path(position)')
    conn.execute('CREATE UNIQUE INDEX chat_window_identity ON chat_window_path(message_id)')
    if leaf is not None and total == 0:
        raise ApiError(409, 'CHAT_BRANCH_CHANGED', '会话分支已变化，请重新加载。')
    if total and not conn.execute('''SELECT 1 FROM chat_window_path p WHERE p.parent_message_id IS NULL
                                    OR NOT EXISTS (SELECT 1 FROM chat_window_path a WHERE a.message_id=p.parent_message_id) LIMIT 1''').fetchone():
        raise ApiError(409, 'CHAT_BRANCH_CORRUPT', '会话父链无法定位。')
    return total


def _items(conn, conversation_id, start, limit):
    from app.services.chat_history import _message
    rows = conn.execute('''SELECT m.* FROM chat_window_path p JOIN chat_messages m USING(message_id)
                           WHERE p.position>=? ORDER BY p.position LIMIT ?''', (start, limit)).fetchall()
    # CROSS JOIN fixes the small window as the outer loop. Without it SQLite can
    # scan every conversation message once per visible parent despite the index.
    siblings = conn.execute('''SELECT DISTINCT m.message_id,m.parent_message_id,m.role,m.sequence
        FROM chat_window_path p CROSS JOIN chat_messages m INDEXED BY chat_versions_metadata
        WHERE p.position>=? AND p.position<? AND m.conversation_id=?
        AND p.parent_message_id IS m.parent_message_id AND p.role=m.role
        ORDER BY m.sequence''', (start, start+limit, conversation_id)).fetchall()
    versions = {}
    for sibling in siblings:
        versions.setdefault((sibling['parent_message_id'], sibling['role']), []).append(sibling['message_id'])
    result = []
    for row in rows:
        message = _message(row)
        message.versions = versions[(row['parent_message_id'], row['role'])]
        result.append(message)
    return result


def _cursor(scope, conversation, leaf, anchor, direction):
    data = {'v': 1, 'scope': scope, 'conversation': conversation, 'leaf': leaf, 'anchor': anchor, 'direction': direction}
    return base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':')).encode()).decode().rstrip('=')


def _decode(cursor, scope, conversation):
    try:
        if len(cursor) > 2048: raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
        if not isinstance(data, dict) or set(data) != {'v', 'scope', 'conversation', 'leaf', 'anchor', 'direction'}: raise ValueError()
        if type(data['v']) is not int or data['v'] != 1 or data['direction'] not in {'before', 'after'}: raise ValueError()
        if any(not isinstance(data[key], str) or not data[key] for key in ['scope', 'conversation', 'leaf', 'anchor']): raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise ApiError(400, 'CHAT_CURSOR_INVALID', '消息位置无效，请重新加载。') from None
    if data['scope'] != scope or data['conversation'] != conversation:
        raise ApiError(409, 'CHAT_CURSOR_SCOPE_CONFLICT', '消息位置属于其他知识库或会话。')
    return data


def window(conversation_id: str, *, limit: int = 60, cursor: str | None = None,
           around: str | None = None, branch_leaf: str | None = None) -> dict:
    if not 2 <= limit <= 120 or (cursor and (around or branch_leaf)):
        raise ApiError(400, 'CHAT_WINDOW_INVALID', '消息窗口参数无效。')
    with closing(connect_knowledge()) as conn, transaction(conn):
        active = _conversation(conn, conversation_id)
        scope = _scope(conn)
        bound = _decode(cursor, scope, conversation_id) if cursor else None
        leaf = bound['leaf'] if bound else branch_leaf or active
        total = _path(conn, conversation_id, leaf)
        anchor = bound['anchor'] if bound else around
        if anchor:
            row = conn.execute('SELECT position FROM chat_window_path WHERE message_id=?', (anchor,)).fetchone()
            if row is None:
                raise ApiError(404, 'CHAT_MESSAGE_OUTSIDE_BRANCH', '该消息属于其他回答分支。')
            position = row['position']
            start = position - limit//2 + (1 if bound and bound['direction'] == 'after' else 0)
            start = max(0, min(max(0, total-limit), start))
        else:
            start = max(0, total-limit)
        items = _items(conn, conversation_id, start, limit)
        end = start+len(items)
        return {'items': items, 'total': total, 'start': start, 'branch_leaf': leaf, 'active_leaf': active,
                'before': _cursor(scope, conversation_id, leaf, items[0].message_id, 'before') if items and start else None,
                'after': _cursor(scope, conversation_id, leaf, items[-1].message_id, 'after') if items and end < total else None}


def offset_messages(conversation_id, limit, offset):
    with closing(connect_knowledge()) as conn, transaction(conn):
        leaf = _conversation(conn, conversation_id)
        total = _path(conn, conversation_id, leaf)
        return _items(conn, conversation_id, offset, limit), total


def generation_messages(conversation_id, expected_leaf):
    """Called on send only. A reading window never trims the model's saved context."""
    with closing(connect_knowledge()) as conn, transaction(conn):
        leaf = _conversation(conn, conversation_id)
        if leaf != expected_leaf:
            raise ApiError(409, 'CHAT_BRANCH_CHANGED', '会话分支已变化，请重新加载后发送。')
        _path(conn, conversation_id, leaf)
        rows = conn.execute('''SELECT m.role,m.content,m.thinking FROM chat_window_path p
                              JOIN chat_messages m USING(message_id) ORDER BY p.position''').fetchall()
        return [Message(role=row['role'], content=row['content'],
                        reasoning_content=row['thinking'] if row['role'] == 'assistant' else None) for row in rows]


def prepare_request(request, user_message_id, assistant_message_id):
    from app.services import chat_history as history
    conversation = request.conversation_id
    if request.use_saved_history and not conversation:
        raise ApiError(400, 'CHAT_CONVERSATION_REQUIRED', '请先创建会话。')
    policy = 'full'
    regenerate = False
    if request.retry_message_id:
        if not conversation:
            raise ApiError(400, 'CHAT_CONVERSATION_REQUIRED', 'Retry requires a saved conversation')
        policy = history.retry_write_policy(conversation, request.retry_message_id)
        target = history.prepare_retry(conversation, request.retry_message_id)
        regenerate = target['role'] == 'assistant'
        if regenerate:
            user_message_id = target['parent_message_id']
    if conversation:
        user = next((message for message in reversed(request.messages)
                     if message.role.value == 'user' and message.content.strip()), None)
        if request.use_saved_history and not regenerate and user is None:
            raise ApiError(400, 'CHAT_USER_REQUIRED', '请提供用户消息。')
        if user is not None and not (request.use_saved_history and regenerate):
            guard = {'expected_leaf': request.expected_branch_leaf} if request.use_saved_history and not request.retry_message_id else {}
            history.append_message(conversation, message_id=user_message_id, role='user', content=user.content,
                                   title=request.conversation_title or user.content[:30],
                                   workspace_context=request.workspace_context.model_dump() if request.workspace_context else None,
                                   attachments=request.attachments, **guard)
        if request.use_saved_history:
            messages = generation_messages(conversation, user_message_id)
            if not messages or messages[-1].role.value != 'user':
                raise ApiError(400, 'CHAT_USER_REQUIRED', '请提供用户消息。')
            request = request.model_copy(update={'messages': messages})
        history.reserve_response(conversation, assistant_message_id,
                                 **({'expected_leaf': user_message_id} if request.use_saved_history else {}))
    return request.model_copy(update={
        'user_message_id': user_message_id, 'assistant_message_id': assistant_message_id,
        'metadata': {**request.metadata, 'retry_write_policy': policy},
    })
