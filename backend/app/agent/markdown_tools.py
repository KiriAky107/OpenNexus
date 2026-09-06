"""Markdown authoring tools. Composition is pure; persistence uses note permissions/CAS."""
import hashlib
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.contracts import ToolDefinition
from app.services import note_service

Format = Literal['heading', 'paragraph', 'bold', 'italic', 'strikethrough', 'inline-code', 'bullet-list', 'ordered-list', 'task-list', 'blockquote', 'callout', 'code-block', 'mermaid', 'function-plot', 'inline-math', 'math-block', 'link', 'image', 'table', 'horizontal-rule', 'hard-break', 'reference-link', 'html', 'metadata']
CALLOUTS = ['note', 'abstract', 'summary', 'tldr', 'info', 'todo', 'tip', 'hint', 'important', 'success', 'check', 'done', 'question', 'help', 'faq', 'warning', 'caution', 'attention', 'failure', 'fail', 'missing', 'danger', 'error', 'bug', 'example', 'quote', 'cite']


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CatalogArguments(Arguments):
    pass


class ComposeArguments(Arguments):
    format: Format
    text: str = Field(default='', max_length=100000)
    level: int = Field(default=2, ge=1, le=6)
    language: str = Field(default='', pattern=r'^[\w+-]{0,40}$')
    url: str = Field(default='', max_length=4000)
    items: list[str] = Field(default_factory=list, max_length=200)
    rows: list[list[str]] = Field(default_factory=list, max_length=200)
    callout: str = 'note'
    collapsed: bool | None = None
    title: str = Field(default='', max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=100)


class PatchArguments(Arguments):
    note_id: str = Field(min_length=1)
    expected_content_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    old_text: str = Field(min_length=1, max_length=200000)
    new_text: str = Field(max_length=200000)


def fenced(text, language=''):
    length = max([2, *(len(m[0]) for m in re.finditer(r'`+', text))]) + 1
    fence = '`' * length
    return f'{fence}{language}\n{text}\n{fence}'


def compose(arguments: ComposeArguments, _):
    a, text = arguments, arguments.text
    kind = a.format
    if kind == 'heading': result = '#' * a.level + ' ' + text.replace('\n', ' ')
    elif kind == 'paragraph': result = text
    elif kind in ('bold', 'italic', 'strikethrough'):
        marker = {'bold': '**', 'italic': '*', 'strikethrough': '~~'}[kind]
        result = marker + text + marker
    elif kind == 'inline-code':
        marker = '`' * (max([0, *(len(m[0]) for m in re.finditer(r'`+', text))]) + 1)
        result = marker + ' ' + text.replace('\n', ' ') + ' ' + marker
    elif kind in ('code-block', 'mermaid', 'function-plot'): result = fenced(text, kind if kind != 'code-block' else a.language)
    elif kind in ('bullet-list', 'ordered-list', 'task-list'):
        result = '\n'.join((f'{i + 1}. ' if kind == 'ordered-list' else '- [ ] ' if kind == 'task-list' else '- ') + item.replace('\n', '\n    ') for i, item in enumerate(a.items))
    elif kind == 'blockquote': result = '\n'.join('> ' + line for line in text.split('\n'))
    elif kind == 'callout':
        if a.callout.lower() not in CALLOUTS: raise ValueError('Unknown callout type')
        fold = '' if a.collapsed is None else '-' if a.collapsed else '+'
        result = f'> [!{a.callout.upper()}]{fold} {a.title.replace(chr(10), " ")}\n' + '\n'.join('> ' + line for line in text.split('\n'))
    elif kind == 'inline-math': result = '$' + text + '$'
    elif kind == 'math-block': result = '$$\n' + text + '\n$$'
    elif kind in ('link', 'image', 'reference-link'):
        if not a.url or re.search(r'[\r\n<>]', a.url): raise ValueError('A single-line URL without angle brackets is required')
        label = text.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
        result = f'[{label}](<{a.url}>)'
        if kind == 'image': result = '!' + result
        if kind == 'reference-link': result = f'[{label}][source]\n\n[source]: <{a.url}>'
    elif kind == 'table':
        if not a.rows or not a.rows[0] or any(len(row) != len(a.rows[0]) for row in a.rows): raise ValueError('Table requires equally sized nonempty rows; first row is the header')
        lines = ['| ' + ' | '.join(cell.replace('\\', '\\\\').replace('|', '\\|').replace('\n', '<br>') for cell in row) + ' |' for row in a.rows]
        lines.insert(1, '| ' + ' | '.join('---' for _ in a.rows[0]) + ' |')
        result = '\n'.join(lines)
    elif kind == 'horizontal-rule': result = '---'
    elif kind == 'hard-break': result = text + '  \n'
    elif kind == 'html': result = text
    else:
        import yaml
        result = '---\n' + yaml.safe_dump({'title': a.title, 'tags': a.tags}, allow_unicode=True, sort_keys=False).rstrip() + '\n---\n' + text
    return {'markdown': result, 'persisted': False}


def catalog(_, __):
    from typing import get_args
    return {'formats': list(get_args(Format)), 'callouts': CALLOUTS,
            'workflow': 'Use markdown.compose, then notes.create or notes.patch_markdown to persist. Read notes.read.content_hash before patching. metadata composition replaces the frontmatter only when you explicitly patch it; do not prepend duplicate frontmatter.',
            'function_plot': 'Use a function-plot fenced block: domain: -4, 4 followed by y = x^2 and y = sin(x). At most 16 expressions per block, 16 plots and 8000 total AST nodes per exported document. No arbitrary code execution.',
            'rendering': 'Function plots, Math, Mermaid, callouts and auto-links depend on editor preferences. HTML is sanitized; scripts are not supported. Heading folding, font size, undo and redo are UI state, not Markdown document syntax. Callout collapsed=null is static, true is folded, false is expanded.'}


async def patch(arguments: PatchArguments, _):
    note = await note_service.get_note(arguments.note_id)
    if note is None: raise LookupError('Note not found')
    if hashlib.sha256(note.markdown.encode()).hexdigest() != arguments.expected_content_hash:
        raise ValueError('Note changed; read it again before editing')
    if note.markdown.count(arguments.old_text) != 1:
        raise ValueError('old_text must match exactly once; provide more surrounding context')
    markdown = note.markdown.replace(arguments.old_text, arguments.new_text, 1)
    from app.knowledge.parser import _extract_frontmatter, _parse_tags
    old_meta, new_meta = _extract_frontmatter(note.markdown), _extract_frontmatter(markdown)
    tags = _parse_tags(new_meta.get('tags')) if old_meta.get('tags') != new_meta.get('tags') else None
    updated = await note_service.update_note(arguments.note_id,
        markdown=markdown, tags=tags,
        expected_content_hash=arguments.expected_content_hash, defer_vectors=True)
    return {'note_id': updated.note_id, 'content_hash': hashlib.sha256(updated.markdown.encode()).hexdigest()}


def register(registry):
    for name, model, executor, permission, description in [
        ('markdown.catalog', CatalogArguments, catalog, None, 'List supported Markdown formats, callouts, rendering constraints and safe editing workflow.'),
        ('markdown.compose', ComposeArguments, compose, None, 'Build a Markdown fragment, table, callout, Mermaid, math or YAML metadata without writing a file. First table row is the header.'),
        ('notes.patch_markdown', PatchArguments, patch, 'notes.write', 'Replace one exact Markdown fragment after verifying notes.read content_hash. Reject ambiguous matches and concurrent edits. Can update all Markdown formats and frontmatter.'),
    ]:
        registry.register(ToolDefinition(name=name, description=description, parameters=model.model_json_schema(), permission=permission), model, executor)
