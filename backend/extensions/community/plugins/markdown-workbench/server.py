"""Markdown checks over MCP stdio; Python standard library only, no I/O tools."""
from __future__ import annotations

import json
import re
import sys

VERSION = '1.0.0'
MAX_TEXT = 100_000
MAX_ITEMS = 200


def inspect_markdown(text: str) -> dict:
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError('text 必须是字符串，最多 100000 个字符。')
    lines = text.splitlines()
    headings, tasks, issues = [], [], []
    previous_level = 0
    titles = set()
    fence = None
    frontmatter_end = -1
    if lines and lines[0].lstrip('\ufeff') == '---':
        frontmatter_end = next((i for i in range(1, len(lines)) if lines[i] in ('---', '...')), -1)
    for index, line in enumerate(lines):
        number = index + 1
        if index <= frontmatter_end:
            continue
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            continue
        if marker and not (marker[1][0] == '`' and '`' in marker[2]):
            fence = (marker[1][0], len(marker[1]), number)
            continue
        # Indented code and blockquotes are excluded from these line-based checks.
        if line.startswith(('    ', '\t', '>')):
            continue
        heading = re.match(r'^ {0,3}(#{1,6})(?:\s+(.*)|$)', line)
        level, title = 0, ''
        if heading:
            level = len(heading[1])
            title = re.sub(r'\s+#+\s*$', '', heading[2] or '').strip()
        elif index + 1 < len(lines) and line.strip() and re.fullmatch(r' {0,3}(=+|-+)\s*', lines[index + 1]) and not re.match(r'^\s*(?:[-*+]\s|\d+[.)]\s|[-=]+\s*$)', line):
            level = 1 if lines[index + 1].lstrip().startswith('=') else 2
            title = line.strip()
        if level:
            headings.append({'line': number, 'level': level, 'title': title[:300]})
            if previous_level and level > previous_level + 1:
                issues.append({'line': number, 'code': 'heading_jump', 'message': f'标题从 H{previous_level} 跳到 H{level}。'})
            if title.casefold() in titles:
                issues.append({'line': number, 'code': 'duplicate_heading', 'message': '存在同名标题，请确认是否需要区分。'})
            if not title:
                issues.append({'line': number, 'code': 'empty_heading', 'message': '标题内容为空。'})
            titles.add(title.casefold())
            previous_level = level
        task = re.match(r'^ {0,3}(?:[-*+]|\d+[.)])\s+\[([ xX])\]\s+(.*)$', line)
        if task:
            tasks.append({'line': number, 'done': task[1].lower() == 'x', 'text': task[2][:300]})
    if fence:
        issues.append({'line': fence[2], 'code': 'unclosed_fence', 'message': '代码围栏没有闭合。'})
    return {
        'summary': {'lines': len(lines), 'characters': len(text), 'headings': len(headings),
                    'tasks': len(tasks), 'open_tasks': sum(not item['done'] for item in tasks), 'issues': len(issues)},
        'headings': headings[:MAX_ITEMS], 'tasks': tasks[:MAX_ITEMS], 'issues': issues[:MAX_ITEMS],
        'truncated': any(len(items) > MAX_ITEMS for items in (headings, tasks, issues)),
        'method': 'line-based Markdown checks; line numbers refer to the supplied text',
    }


TOOLS = [
    {'name': 'inspect_markdown', 'description': '本地检查 Markdown，返回标题、待办事项、格式问题及 1 起始行号。不会读取或修改文件。',
     'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string', 'maxLength': MAX_TEXT}}, 'required': ['text'], 'additionalProperties': False}},
    {'name': 'selection_report', 'description': 'NotesAgent 当前选区检查命令。',
     'inputSchema': {'type': 'object', 'properties': {'_notesagent': {'type': 'object'}}, 'required': ['_notesagent'], 'additionalProperties': False}},
]


def call_tool(name: str, arguments: dict) -> dict:
    if name == 'inspect_markdown':
        result = inspect_markdown(arguments.get('text'))
    elif name == 'selection_report':
        envelope = arguments.get('_notesagent', {})
        if not isinstance(envelope, dict) or not isinstance(envelope.get('context', {}), dict):
            raise ValueError('命令上下文无效。')
        report = inspect_markdown(envelope.get('context', {}).get('selection', ''))
        summary = report['summary']
        details = '；'.join(f"第 {item['line']} 行：{item['message']}" for item in report['issues'][:3])
        result = {'type': 'notification', 'payload': {'level': 'info', 'message':
            f"Markdown 检查：{summary['lines']} 行，{summary['headings']} 个标题，{summary['open_tasks']} 项未完成任务，{summary['issues']} 项提示。" + details}}
    else:
        raise ValueError('未知工具。')
    return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}], 'structuredContent': result, 'isError': False}


def main() -> None:
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    for raw in sys.stdin:
        request_id = None
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError('请求必须为对象。')
            request_id = message.get('id')
            if request_id is None:
                continue
            method, params = message.get('method'), message.get('params') or {}
            if method == 'initialize':
                result = {'protocolVersion': params.get('protocolVersion'), 'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': {'name': 'markdown-workbench', 'version': VERSION}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': TOOLS}
            elif method == 'tools/call':
                try:
                    result = call_tool(params.get('name'), params.get('arguments') or {})
                except (ValueError, TypeError, AttributeError) as error:
                    result = {'content': [{'type': 'text', 'text': str(error)}], 'isError': True}
            else:
                raise ValueError('不支持的方法。')
            response = {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        except (ValueError, TypeError, AttributeError):
            response = {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32600, 'message': 'Invalid request'}}
        print(json.dumps(response, ensure_ascii=False, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
