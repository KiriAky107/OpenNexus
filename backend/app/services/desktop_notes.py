"""Desktop note adapter: Markdown and stable identities are owned only by Rust.

No fallback to the Core's unbound Vault or its stale SQLite note projection.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import uuid4
import yaml
from app import host_bridge
from app.contracts import Note, NoteSummary
from app.errors import ApiError
from app.knowledge.parser import parse_note, _frontmatter
from app.services.vault_paths import normalize_folder, normalize_entry_name, safe_note_filename


def call(method: str, **params):
    vault = host_bridge.vault_id.get()
    if not vault:
        raise ApiError(409, 'WORKSPACE_NOT_OPEN', '请先打开授权工作区。')
    if host_bridge.active is None:
        raise ApiError(503, 'HOST_UNAVAILABLE', 'Host 不可用。')
    try:
        return host_bridge.active.call('workspace.' + method, vault_id=vault, **params)
    except RuntimeError as exc:
        code = str(exc)
        status = 404 if code in {'FILE_NOT_FOUND', 'OPERATION_NOT_FOUND'} else 409
        if code in {'HOST_UNAVAILABLE', 'HOST_TIMEOUT'}: status = 503
        raise ApiError(status, code, '工作区操作未完成，请检查当前工作区和操作结果。',
                       {'operation_id': params.get('operation_id'), 'vault_id': vault}) from None


def note_from_document(document: dict) -> Note:
    path = PurePosixPath(document['path'])
    parsed = parse_note(markdown=document['content'], file_path=str(path),
                        folder=str(path.parent) if str(path.parent) != '.' else '',
                        note_id=document['file_id'],
                        created_at=datetime.fromtimestamp(document['created_at'], timezone.utc),
                        updated_at=datetime.fromtimestamp(document['updated_at'], timezone.utc))
    return Note(note_id=parsed.note_id, title=parsed.title, file_path=parsed.file_path,
                tags=parsed.tags, created_at=parsed.created_at, updated_at=parsed.updated_at,
                markdown=document['content'], blocks=parsed.blocks)


def metadata(markdown: str, title: str | None, tags: list[str] | None) -> str:
    if title is None and tags is None: return markdown
    header = _frontmatter(markdown)
    try:
        values = yaml.safe_load(header[0]) if header else {}
    except yaml.YAMLError:
        raise ApiError(422, 'INVALID_FRONTMATTER', '元数据格式无效，请先修复原文。') from None
    if values is None: values = {}
    if not isinstance(values, dict): raise ApiError(422, 'INVALID_FRONTMATTER', '元数据必须是字段映射。')
    if title is not None: values['title'] = title
    if tags is not None: values['tags'] = tags
    return '---\n' + yaml.safe_dump(values, allow_unicode=True, sort_keys=False) + '---\n' + (markdown[header[1]:] if header else markdown)


async def get_note(note_id: str) -> Note | None:
    try:
        return note_from_document(await asyncio.to_thread(call, 'read', file_id=note_id))
    except ApiError as exc:
        if exc.code == 'FILE_NOT_FOUND': return None
        raise


async def mutate(name: str, *args, **kwargs):
    operation_id = host_bridge.operation_id.get() or str(uuid4())
    if name == 'create_note':
        folder = normalize_folder(kwargs.get('folder'))
        path = '/'.join(filter(None, [folder, safe_note_filename(kwargs['title'])]))
        content = metadata(kwargs['markdown'], kwargs['title'], kwargs.get('tags') or None)
        receipt = await asyncio.to_thread(call, 'write', path=path, expected='', content=content, operation_id=operation_id)
        return await get_note(receipt['result']['file_id'])
    note_id = args[0] if args else kwargs.pop('note_id')
    document = await asyncio.to_thread(call, 'read', file_id=note_id)
    path = document['path']
    if name == 'update_note':
        expected = kwargs.get('expected_content_hash') or document['hash']
        content = document['content'] if kwargs.get('markdown') is None else kwargs['markdown']
        tags = kwargs.get('tags')
        if tags is None and kwargs.get('markdown') is not None:
            tags = note_from_document(document).tags
        content = metadata(content, kwargs.get('title'), tags)
        await asyncio.to_thread(call, 'write', path=path, expected=expected, content=content, operation_id=operation_id)
        return await get_note(note_id)
    if name in {'move_note', 'rename_note', 'delete_note'}:
        destination = ''
        if name == 'move_note':
            destination = '/'.join(filter(None, [normalize_folder(kwargs['folder']), PurePosixPath(path).name]))
        if name == 'rename_note':
            parent = str(PurePosixPath(path).parent)
            destination = '/'.join(filter(None, ['' if parent == '.' else parent, normalize_entry_name(kwargs['file_name'], markdown=True)]))
        if destination == path: return await get_note(note_id)
        await asyncio.to_thread(call, 'mutate', kind='delete' if name == 'delete_note' else 'rename',
                                path=path, destination=destination, expected=document['hash'], operation_id=operation_id)
        return True if name == 'delete_note' else await get_note(note_id)
    raise ApiError(409, 'WORKSPACE_OPERATION_UNSUPPORTED', '此操作尚未接入 Host。')


def list_notes(*, limit: int, offset: int, folder: str | None, tag: str | None):
    entries, position = [], 0
    while True:
        page = call('list', offset=position, limit=1000)
        entries.extend(page['items'])
        position += len(page['items'])
        if position >= page['total'] or not page['items']: break
    notes = []
    for entry in entries:
        parent = str(PurePosixPath(entry['path']).parent)
        if folder is not None and ('' if parent == '.' else parent) != normalize_folder(folder): continue
        note = note_from_document(call('read', file_id=entry['file_id']))
        if tag is not None and tag not in note.tags: continue
        notes.append(NoteSummary(**note.model_dump(exclude={'markdown', 'blocks'})))
    return notes[offset:offset + limit], len(notes)
