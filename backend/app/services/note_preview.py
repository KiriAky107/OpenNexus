"""Read-only previews built from the same arguments and metadata rules as writes."""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
from contextvars import ContextVar

from app.config import get_settings
from app.errors import ApiError
from app.services import note_service

WRITE_TOOLS = {'notes.create', 'notes.update', 'notes.patch_markdown'}
expected_write_state: ContextVar[dict | None] = ContextVar('expected_note_write_state', default=None)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def snapshot(note) -> dict | None:
    if note is None:
        return None
    return {'note_id': note.note_id, 'file_path': note.file_path,
            'content_hash': hashlib.sha256(note.markdown.encode()).hexdigest(),
            'title': getattr(note, 'title', ''), 'tags': getattr(note, 'tags', [])}


def check_write_state(note) -> None:
    expected = expected_write_state.get()
    if expected is not None and expected != snapshot(note):
        raise ApiError(409, 'NOTE_CONTENT_CONFLICT', '笔记在写入前已修改或移动，请重新预览。')


def content_diff(before: str, after: str) -> dict:
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    # Avoid quadratic work for huge/repetitive documents. The fallback is an
    # explicit whole-document replacement, with complete character counts.
    ops = [] if before == after else (difflib.SequenceMatcher(None, a, b, autojunk=True).get_opcodes()
           if len(a) * len(b) <= 2_000_000 else [('replace', 0, len(a), 0, len(b))])
    rows, added, removed, clipped = [], 0, 0, False
    for kind, i, j, k, l in ops:
        if kind == 'equal':
            continue
        removed += sum(len(line) for line in a[i:j])
        added += sum(len(line) for line in b[k:l])
        for sign, lines, start in [('-', a[i:j], i), ('+', b[k:l], k)]:
            for index, line in enumerate(lines):
                if len(rows) < 400:
                    rows.append({'kind': sign, 'line': start + index + 1, 'text': line[:2000]})
                else:
                    clipped = True
                if len(line) > 2000:
                    clipped = True
    return {'lines': rows, 'added_chars': added, 'removed_chars': removed,
            'before_chars': len(before), 'after_chars': len(after), 'truncated': clipped}


async def preview_write(call) -> dict:
    from app.agent.builtin_tools import NoteCreateArguments, NoteUpdateArguments
    from app.agent.markdown_tools import PatchArguments, prepare_patch
    from app.services.desktop_notes import metadata, call as host_call
    models = {'notes.create': NoteCreateArguments, 'notes.update': NoteUpdateArguments,
              'notes.patch_markdown': PatchArguments}
    from pydantic import ValidationError
    try:
        args = models[call.name].model_validate(call.arguments)
    except ValidationError as exc:
        raise ApiError(422, 'TOOL_ARGUMENT_INVALID', '操作参数无效，请让智能体重新提出修改。') from exc
    desktop = get_settings().environment == 'desktop'
    note = None
    if call.name == 'notes.create':
        path, _ = note_service._rel_path(args.folder, args.title)
        if desktop:
            offset = 0
            while True:
                page = await asyncio.to_thread(host_call, 'list', offset=offset, limit=1000)
                if any(entry['path'] == path for entry in page['items']):
                    raise ApiError(409, 'RESOURCE_CONFLICT', '目标路径已有文件，请重新选择目标。')
                offset += len(page['items'])
                if offset >= page['total'] or not page['items']:
                    break
        else:
            from app.services.vault_paths import resolve_in_vault
            if resolve_in_vault(path).exists():
                raise ApiError(409, 'RESOURCE_CONFLICT', '目标路径已有文件，请重新选择目标。')
        after = metadata(args.markdown, args.title, args.tags or None) if desktop else args.markdown
        proposed = {'title': args.title, 'tags': args.tags}
    else:
        note = await note_service.get_note(args.note_id)
        if note is None:
            raise ApiError(404, 'RESOURCE_NOT_FOUND', '笔记不存在。')
        path = note.file_path
        if args.expected_content_hash and args.expected_content_hash != snapshot(note)['content_hash']:
            raise ApiError(409, 'NOTE_CONTENT_CONFLICT', '笔记已变化，请让智能体重新读取并提出修改。')
        if call.name == 'notes.patch_markdown':
            try:
                after, tags = prepare_patch(args, note)
            except ValueError as exc:
                raise ApiError(409, 'NOTE_PATCH_INVALID', str(exc)) from exc
            title = None
        else:
            after = note.markdown if args.markdown is None else args.markdown
            title, tags = args.title, args.tags
        if desktop:
            if tags is None and (call.name == 'notes.patch_markdown' or args.markdown is not None):
                tags = note.tags
            after = metadata(after, title, tags)
        proposed = {'title': title, 'tags': tags}
    before = note.markdown if note else ''
    binding = {'tool': call.name, 'arguments': call.arguments, 'before': snapshot(note),
               'path': path, 'after_hash': hashlib.sha256(after.encode()).hexdigest()}
    return {'token': digest(binding), 'binding': binding, 'file_path': path,
            'note_id': note.note_id if note else None,
            'operation': 'create' if note is None else 'append' if after != before and after.startswith(before) else 'replace',
            'metadata': proposed, 'diff': content_diff(before, after)}


async def validate_preview(call, preview: dict) -> None:
    current = await preview_write(call)
    if current['token'] != preview['token']:
        raise ApiError(409, 'NOTE_PREVIEW_STALE', '笔记或操作内容已变化，请重新预览后确认。')
