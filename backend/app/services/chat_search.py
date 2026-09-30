"""Incremental, vault-scoped index of visible text and recorded operations."""
from contextlib import closing
import json

from app.database.db import connect_knowledge, transaction
from app.errors import ApiError

TOOL_TITLES = {
    'agent.define': '创建智能体配置 Create Agent definition', 'agent.start': '启动智能体任务 Start Agent task',
    'agent.create': '创建并启动临时任务 Create and start task', 'agent.collaborate': '规划协作分工 Plan collaboration',
    'agent.search_tools': '查找可用工具 Discover available tools', 'agent.list': '查找智能体 Find Agents',
    'agent.inspect': '读取智能体配置 Read Agent definition', 'agent.status': '查询任务状态 Read task status',
    'agent.propose_update': '提出配置变更 Propose configuration change', 'agent.propose_delete': '提出删除请求 Propose deletion',
    'rag.search': '检索知识库 Search knowledge base',
}
STATUS = {'completed': '已完成 Completed', 'running': '运行中 Running', 'pending': '待执行 Pending',
          'error': '异常 Error', 'failed': '失败 Failed', 'cancelled': '已取消 Cancelled'}


def segments(message: dict) -> list[dict]:
    activity = json.loads(message['activity_json'])
    calls = json.loads(message['tool_calls_json'])
    calls_by_id = {c['tool_call_id']: c for c in calls if c.get('tool_call_id')}
    entries, used = [], set()
    def tool(c):
        try:
            result = json.loads(c.get('result') or '{}')
        except (ValueError, TypeError):
            result = {}
        if not isinstance(result, dict): result = {}
        config = result.get('config') if isinstance(result.get('config'), dict) else {}
        name = result.get('name') or result.get('title') or config.get('name')
        text = ' '.join(str(v) for v in [TOOL_TITLES.get(c.get('name'), c.get('name', '')),
                                        STATUS.get(c.get('status'), c.get('status', '')),
                                        name if isinstance(name, str) else '', c.get('error_message') or ''] if v)
        entries.append({'kind': 'tool', 'text': text, 'tool_call_id': c['tool_call_id']})
        used.add(c['tool_call_id'])
    if message['role'] == 'user':
        return [{'kind': 'text', 'text': message['content']}]
    for item in activity:
        if item.get('type') == 'text' and isinstance(item.get('text'), str):
            entries.append({'kind': 'text', 'text': item['text']})
        elif item.get('type') == 'tool' and item.get('tool_call_id') in calls_by_id and item['tool_call_id'] not in used:
            tool(calls_by_id[item['tool_call_id']])
    for c in calls:
        if c.get('tool_call_id') and c['tool_call_id'] not in used: tool(c)
    if not any(item.get('type') == 'text' for item in activity) and message['content']:
        entries.append({'kind': 'text', 'text': message['content']})
    return entries


def _index(conn, conversation_id):
    state = conn.execute('SELECT last_sequence FROM chat_search_state WHERE conversation_id=?', (conversation_id,)).fetchone()
    rows = conn.execute('SELECT * FROM chat_messages WHERE conversation_id=? AND sequence>? ORDER BY sequence',
                        (conversation_id, state[0] if state else -1)).fetchall()
    if not rows and state: return
    with transaction(conn, immediate=True):
        for row in rows:
            for index, segment in enumerate(segments(dict(row))):
                conn.execute('INSERT OR IGNORE INTO chat_search_entries(message_id,conversation_id,entry_index,kind,tool_call_id,text) VALUES(?,?,?,?,?,?)',
                             (row['message_id'], conversation_id, index, segment['kind'], segment.get('tool_call_id'), segment['text']))
        if rows:
            conn.execute('INSERT INTO chat_search_state VALUES(?,?) ON CONFLICT(conversation_id) DO UPDATE SET last_sequence=MAX(last_sequence,excluded.last_sequence)', (conversation_id, rows[-1]['sequence']))


def search(conversation_id: str, query: str, *, limit: int = 40, offset: int = 0) -> dict:
    query = query.strip()
    with closing(connect_knowledge()) as conn:
        if not conn.execute('SELECT 1 FROM chat_conversations WHERE conversation_id=?', (conversation_id,)).fetchone():
            raise ApiError(404, 'CONVERSATION_NOT_FOUND', '当前知识库中找不到该会话。')
        if not query: return {'items': [], 'has_more': False}
        _index(conn, conversation_id)
        # Trigram FTS handles long substrings. One/two-character searches use a
        # scoped paginated scan of already projected text, never raw JSON or reasoning.
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        if len(query) >= 3:
            join = 'JOIN chat_search_fts f ON f.rowid=e.id'
            where = 'f.text MATCH ?'
            term = '"' + query.replace('"', '""') + '"'
        else:
            join = ''
            where = 'e.text LIKE ? ESCAPE \'\\\''
            term = '%' + escaped + '%'
        rows = conn.execute(f'''SELECT e.*,m.sequence,m.role,m.created_at FROM chat_search_entries e
            JOIN chat_messages m ON m.message_id=e.message_id {join}
            WHERE e.conversation_id=? AND {where} ORDER BY m.sequence,e.entry_index LIMIT ? OFFSET ?''',
            (conversation_id, term, limit + 1, offset)).fetchall()
        items = []
        for row in rows[:limit]:
            text = row['text']
            start = text.lower().find(query.lower())
            left, right = max(0, start - 45), min(len(text), start + len(query) + 75)
            items.append({'message_id': row['message_id'], 'position': row['sequence'] + 1,
                          'entry_index': row['entry_index'], 'kind': row['kind'], 'role': row['role'],
                          'tool_call_id': row['tool_call_id'], 'snippet': text[left:right],
                          'created_at': row['created_at']})
        return {'items': items, 'has_more': len(rows) > limit}
