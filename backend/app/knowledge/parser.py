"""Markdown 解析与 Note Block 切分。

Block 由 Markdown 文本生成：标题行独立成块（heading_path 含自身），正文按空行分段，
每块记录其在原文中的 start_offset / end_offset，用于 Citation 跳转定位。block_id 由
(note_id, heading_path, content) 稳定派生，内容不变则 ID 稳定。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.contracts import NoteBlock
from app.textutils import count_tokens

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)\s*$")
_FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")


@dataclass
class ParsedNote:
    note_id: str
    title: str
    file_path: str
    folder: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    blocks: list[NoteBlock] = field(default_factory=list)


def note_id_for_path(rel_path: str) -> str:
    """由相对路径派生稳定 note_id（路径哈希而非路径本身，见团队约定「不用路径当 ID」）。

    MVP 阶段 ID 随文件移动而变化；后续 move 流程会保留原 ID。"""
    normalized = rel_path.replace("\\", "/").strip("/")
    return "note_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def parse_note(
    *,
    markdown: str,
    file_path: str,
    folder: str,
    tags: list[str] | None = None,
    created_at: datetime,
    updated_at: datetime,
) -> ParsedNote:
    """解析一篇 Markdown，生成 ParsedNote（元数据 + Block 列表）。"""
    note_id = note_id_for_path(file_path)
    frontmatter = _extract_frontmatter(markdown)

    fallback_title = Path(file_path).stem
    title = frontmatter.get("title") or _first_heading(markdown) or fallback_title
    resolved_tags = list(tags) if tags is not None else _parse_tags(frontmatter.get("tags"))

    blocks = parse_blocks(markdown, note_id)
    return ParsedNote(
        note_id=note_id,
        title=title,
        file_path=file_path,
        folder=folder,
        tags=resolved_tags,
        created_at=created_at,
        updated_at=updated_at,
        blocks=blocks,
    )


def parse_blocks(markdown: str, note_id: str) -> list[NoteBlock]:
    """把 Markdown 切成 Block，offset 相对原文（含 frontmatter）。"""
    lines = _split_lines(markdown)
    content_start = _content_start(markdown)

    blocks: list[NoteBlock] = []
    heading_stack: list[str] = []
    body: list[tuple[str, int]] = []
    id_counters: dict[str, int] = {}

    def make_block(path: list[str], chunk: list[tuple[str, int]]) -> None:
        if not chunk:
            return
        content = "\n".join(line for line, _ in chunk)
        start = chunk[0][1]
        end = chunk[-1][1] + len(chunk[-1][0])
        block_id = _stable_block_id(note_id, path, content, id_counters)
        blocks.append(
            NoteBlock(
                block_id=block_id,
                note_id=note_id,
                heading_path=list(path),
                start_offset=start,
                end_offset=end,
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
                token_count=count_tokens(content),
            )
        )

    def flush_body() -> None:
        nonlocal body
        make_block(heading_stack, body)
        body = []

    for line, offset in lines:
        if offset < content_start:
            continue  # 跳过 frontmatter 区域，但保留 offset 准确性

        heading = _HEADING_RE.match(line)
        if heading:
            flush_body()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            heading_stack = heading_stack[: level - 1] + [title]
            # 标题自身作为一个 Block，便于按章节定位
            make_block(heading_stack, [(line, offset)])
        elif line.strip() == "":
            flush_body()  # 空行分隔段落
        else:
            body.append((line, offset))

    flush_body()
    return blocks


def _stable_block_id(note_id: str, path: list[str], content: str, counters: dict[str, int]) -> str:
    base = hashlib.sha256(
        f"{note_id}\x1f{chr(31).join(path)}\x1f{content}".encode("utf-8")
    ).hexdigest()[:16]
    block_id = f"blk_{base}"
    # 同一篇笔记内极少出现的重复段落用后缀消歧，保证唯一
    n = counters.get(block_id, 0)
    counters[block_id] = n + 1
    return block_id if n == 0 else f"{block_id}_{n}"


def _split_lines(text: str) -> list[tuple[str, int]]:
    """按行拆分并记录每行在原文中的起始字符偏移。"""
    result: list[tuple[str, int]] = []
    start = 0
    for raw in text.splitlines(keepends=True):
        line = raw
        if line.endswith("\r\n"):
            line = line[:-2]
        elif line.endswith("\n") or line.endswith("\r"):
            line = line[:-1]
        result.append((line, start))
        start += len(raw)
    return result


def _content_start(markdown: str) -> int:
    """返回正文起始偏移：有 frontmatter 时跳过 --- 分隔块，否则为 0。"""
    if markdown.startswith("---"):
        end = markdown.find("\n---", 3)
        if end != -1:
            return end + 4
    return 0


def _extract_frontmatter(markdown: str) -> dict[str, str]:
    """极简 frontmatter 解析，只提取 key: value 行。"""
    if not markdown.startswith("---"):
        return {}
    end = markdown.find("\n---", 3)
    if end == -1:
        return {}
    meta: dict[str, str] = {}
    for line in markdown[3:end].splitlines():
        m = _FRONTMATTER_KEY_RE.match(line)
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()
    return meta


def _first_heading(markdown: str) -> str | None:
    for line in markdown.splitlines():
        m = re.match(r"^#\s+(.*?)\s*$", line)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [t.strip().strip("'\"") for t in raw.split(",") if t.strip()]
