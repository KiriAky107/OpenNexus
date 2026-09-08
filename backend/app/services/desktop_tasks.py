"""Desktop Task records are committed by Host before returning to Core callers."""
from __future__ import annotations
from datetime import datetime, timezone
import re
from uuid import uuid4, uuid5, NAMESPACE_URL
from app import host_bridge
from app.contracts import Task, TaskStatus
from app.database.db import connect_knowledge, transaction
from app.errors import ApiError
from app.services import desktop_notes


def _call(method, **params): return desktop_notes.call('records.' + method, **params)
def _ms(value): return None if value is None else int(value.timestamp() * 1000)
def _datetime(value): return None if value is None else datetime.fromtimestamp(value / 1000, timezone.utc)
def _record(task):
    return {'schema': 1, 'kind': 'task', 'id': task.task_id, 'data': {
        'title': task.title, 'description': task.description, 'status': task.status.value,
        'note_id': task.note_id, 'due_at_ms': _ms(task.due_at),
        'created_at_ms': _ms(task.created_at), 'updated_at_ms': _ms(task.updated_at)}}
def _task(record):
    data = record['data']
    return Task(task_id=record['id'], title=data['title'], description=data['description'], status=data['status'],
                note_id=data['note_id'], due_at=_datetime(data['due_at_ms']), created_at=_datetime(data['created_at_ms']), updated_at=_datetime(data['updated_at_ms']))
def _operation(): return host_bridge.operation_id.get() or str(uuid4())
def _replay(operation, task_id=None, values=None, deleted=False):
    previous = _call('operation', operation_id=operation)
    if previous is None: return None
    if previous.get('state') != 'committed' or previous.get('deleted') != deleted:
        raise ApiError(409, 'OPERATION_PAYLOAD_CONFLICT', '该操作标识已用于其他修改。')
    task = _task(previous['record'])
    if task_id is not None and task.task_id != task_id:
        raise ApiError(409, 'OPERATION_PAYLOAD_CONFLICT', '该操作标识已用于其他任务。')
    for name, value in (values or {}).items():
        actual = getattr(task, name)
        if isinstance(actual, datetime) and isinstance(value, datetime):
            actual, value = _ms(actual), _ms(value)
        if actual != value: raise ApiError(409, 'OPERATION_PAYLOAD_CONFLICT', '该操作标识的字段不一致。')
    return task

def _migrate():
    # Only the already scoped Vault database is eligible; unassigned legacy global data stays untouched.
    conn = connect_knowledge()
    try:
        if conn.execute("SELECT value FROM index_meta WHERE key='tasks_host_owned_v1'").fetchone(): return
        from app.services.task_service import _task_from_row
        for row in conn.execute('SELECT * FROM tasks ORDER BY task_id').fetchall():
            task = _task_from_row(row)
            if _call('get', id=task.task_id) is None:
                operation = str(uuid5(NAMESPACE_URL, 'opennexus-task-migration:' + host_bridge.vault_id.get() + ':' + task.task_id))
                _call('write', record=_record(task), expected='', operation_id=operation)
        with transaction(conn):
            conn.execute("INSERT OR REPLACE INTO index_meta VALUES ('tasks_host_owned_v1','1')")
    finally: conn.close()

def _link(note_id):
    if not note_id: return None
    try: return desktop_notes.call('read', file_id=note_id)['file_id']
    except ApiError as error:
        if error.code == 'FILE_NOT_FOUND': raise ApiError(404, 'RESOURCE_NOT_FOUND', 'note not found', {'note_id': note_id}) from None
        raise

def create(*, title, description='', note_id=None, due_at=None):
    _migrate(); operation = _operation()
    values = {'title': title, 'description': description, 'note_id': note_id, 'due_at': due_at}
    replay = _replay(operation, values=values)
    if replay is not None: return replay
    now = datetime.now(timezone.utc)
    task_id = 'task_' + uuid5(NAMESPACE_URL, 'opennexus-task:' + operation).hex
    task = Task(task_id=task_id, title=title, description=description, note_id=_link(note_id), due_at=due_at, created_at=now, updated_at=now)
    receipt = _call('write', record=_record(task), expected='', operation_id=operation)
    return _task(receipt['record'])
def get(task_id):
    if re.fullmatch(r'task_[0-9a-f]{32}', task_id) is None: return None
    _migrate(); value = _call('get', id=task_id)
    return _task(value['record']) if value is not None else None
def list_tasks(*, limit, offset):
    _migrate(); result = _call('list', limit=1000, offset=0); records = list(result['items'])
    while len(records) < result['total']:
        page = _call('list', limit=1000, offset=len(records))
        if not page['items']: break
        records.extend(page['items'])
    tasks = sorted((_task(value['record']) for value in records), key=lambda value: (value.updated_at, value.task_id), reverse=True)
    return tasks[offset:offset+limit], len(tasks)
def update(task_id, values):
    _migrate(); operation = _operation(); values = dict(values)
    for key in ['title', 'description', 'status']:
        if values.get(key) is None: values.pop(key, None)
    if not set(values) <= {'title','description','status','note_id','due_at'}: raise ApiError(422, 'INVALID_ARGUMENT', '未知任务字段。')
    replay = _replay(operation, task_id, values)
    if replay is not None: return replay
    current = _call('get', id=task_id)
    if current is None: raise ApiError(404, 'RESOURCE_NOT_FOUND', 'task not found', {'task_id': task_id})
    if 'note_id' in values: values['note_id'] = _link(values['note_id'])
    task = _task(current['record']).model_copy(update={**values, 'updated_at': datetime.now(timezone.utc)})
    if isinstance(task.status, str): task.status = TaskStatus(task.status)
    receipt = _call('write', record=_record(task), expected=current['hash'], operation_id=operation)
    return _task(receipt['record'])
def delete(task_id):
    if re.fullmatch(r'task_[0-9a-f]{32}', task_id) is None: return False
    _migrate(); operation = _operation()
    if _replay(operation, task_id, deleted=True) is not None: return True
    current = _call('get', id=task_id)
    if current is None: return False
    _call('delete', id=task_id, expected=current['hash'], operation_id=operation)
    return True
