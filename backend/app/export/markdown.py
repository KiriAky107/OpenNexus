"""Markdown → Document AST：用 mistune 的 ast renderer 产出通用 token，再映射为内部节点。

选用 mistune 内置 'ast' renderer 而非自写 BaseRenderer，是因为 mistune 的行内渲染按
字符串拼接、无法承载结构化子节点；ast renderer 直接给出带 children/attrs/raw 的 token
树，映射层只做 token → DocumentNode 的搬运，不掺入任何 HTML。
"""

from __future__ import annotations

import mistune
from mistune.plugins.table import table_in_list, table_in_quote
import re
from copy import deepcopy
from app.export.themes import CALLOUTS, ALIASES

from app.export.document import Document, DocumentNode

_PLUGINS = ["table", "math", "url", "task_lists"]

# fenced code 语言分流：命中则转为专用节点，其余按普通代码块
_MERMAID_LANG = "mermaid"
_FUNCTION_PLOT_LANGS = {"function-plot", "function_plot", "functionplot"}


def parse_document(markdown: str) -> Document:
    """把 Markdown 文本解析为 Document AST 根节点。"""
    renderer = mistune.create_markdown(renderer="ast", plugins=_PLUGINS)
    table_in_quote(renderer)
    table_in_list(renderer)
    tokens = renderer(markdown)
    mapper = _AstMapper()
    return Document(node_id=mapper.next_id(), children=mapper.map_blocks(tokens))


class _AstMapper:
    """token 树 → DocumentNode 树的映射器；node_id 按遍历顺序递增，无需跨请求稳定。"""

    def __init__(self) -> None:
        self._seq = 0

    def next_id(self) -> str:
        self._seq += 1
        return f"node_{self._seq:03d}"

    def map_blocks(self, tokens: list[dict]) -> list[DocumentNode]:
        nodes: list[DocumentNode] = []
        for token in tokens:
            node = self.map_block(token)
            if node is not None:
                nodes.append(node)
        return nodes

    def map_block(self, token: dict) -> DocumentNode | None:
        kind = token["type"]
        if kind == "heading":
            return DocumentNode(
                type="heading",
                node_id=self.next_id(),
                attributes={"level": token["attrs"]["level"]},
                children=self.map_inline(token.get("children", [])),
            )
        if kind in ("paragraph", "block_text"):
            # block_text 是列表项内的段落块，仍按 paragraph 表达，由 list_item 包裹
            return DocumentNode(
                type="paragraph",
                node_id=self.next_id(),
                children=self.map_inline(token.get("children", [])),
            )
        if kind == "list":
            return DocumentNode(
                type="list",
                node_id=self.next_id(),
                attributes={"ordered": bool(token.get("attrs", {}).get("ordered"))},
                children=[self.map_list_item(child) for child in token.get("children", [])],
            )
        if kind == "block_code":
            return self._map_code(token)
        if kind == "block_quote":
            children = deepcopy(token.get('children', []))
            first = children[0] if children else {}
            inline = first.get('children', [])
            if first.get('type') == 'paragraph' and inline and inline[0].get('type') == 'text':
                match = re.match(r'^\[!([\w-]+)\]([+-]?)[ \t]*', inline[0].get('raw', ''))
                if match:
                    name = match[1].lower()
                    name = ALIASES.get(name, name)
                    if name not in CALLOUTS:
                        name = 'note'
                    inline[0]['raw'] = inline[0]['raw'][match.end():]
                    split = next((i for i,t in enumerate(inline) if t['type'] in ('softbreak','linebreak')),len(inline))
                    title = inline[:split]
                    if not any(t.get('raw') or t.get('children') for t in title):
                        title = [{'type':'text','raw':match[1].lower().capitalize()}]
                    first['children'] = inline[split+1:]
                    if not first['children']:
                        children.pop(0)
                    heading = DocumentNode(type='paragraph',node_id=self.next_id(),children=self.map_inline(title))
                    return DocumentNode(type='callout',node_id=self.next_id(),
                            attributes={'kind':name,'fold':match[2]},
                            children=[heading,*self.map_blocks(children)])
            return DocumentNode(
                type="blockquote",
                node_id=self.next_id(),
                children=self.map_blocks(token.get("children", [])),
            )
        if kind == "table":
            return self._map_table(token)
        if kind == "block_math":
            return DocumentNode(
                type="math_block", node_id=self.next_id(), text=token.get("raw", "")
            )
        if kind == "thematic_break":
            return DocumentNode(type="thematic_break", node_id=self.next_id())
        if kind == "blank_line":
            return None
        if kind == "block_html":
            # 原始 HTML 块降级为纯文本节点，由 HtmlExporter 转义并记 warning，避免静默丢失正文
            return DocumentNode(
                type="html_block", node_id=self.next_id(), text=token.get("raw", "")
            )
        # 未知块级 token 保守保留原文；映射为带 text 子节点的 paragraph，避免被渲染层丢弃
        raw = token.get("raw", "")
        if raw:
            return DocumentNode(
                type="paragraph",
                node_id=self.next_id(),
                children=[DocumentNode(type="text", node_id=self.next_id(), text=raw)],
            )
        return None

    def map_list_item(self, token: dict) -> DocumentNode:
        """列表项：block_text 展平为行内子节点，嵌套 list 保留为子节点。"""
        attributes: dict = {}
        if token["type"] == "task_list_item":
            attributes = {"task": True, "checked": bool(token.get("attrs", {}).get("checked"))}
        children: list[DocumentNode] = []
        for child in token.get("children", []):
            if child["type"] == "block_text":
                children.extend(self.map_inline(child.get("children", [])))
            elif child["type"] == "list":
                children.append(self.map_block(child))
            else:
                node = self.map_block(child)
                if node is not None:
                    children.append(node)
        return DocumentNode(
            type="list_item", node_id=self.next_id(), attributes=attributes, children=children
        )

    def map_inline(self, tokens: list[dict]) -> list[DocumentNode]:
        nodes: list[DocumentNode] = []
        for token in tokens:
            node = self.map_inline_token(token)
            if node is not None:
                nodes.append(node)
        return nodes

    def map_inline_token(self, token: dict) -> DocumentNode | None:
        kind = token["type"]
        if kind == "text":
            return DocumentNode(type="text", node_id=self.next_id(), text=token.get("raw", ""))
        if kind == "strong":
            return DocumentNode(
                type="strong", node_id=self.next_id(),
                children=self.map_inline(token.get("children", [])),
            )
        if kind == "emphasis":
            return DocumentNode(
                type="emphasis", node_id=self.next_id(),
                children=self.map_inline(token.get("children", [])),
            )
        if kind == "link":
            attrs = token.get("attrs", {})
            attributes = {"href": attrs.get("url", "")}
            if attrs.get("title"):
                attributes["title"] = attrs["title"]
            return DocumentNode(
                type="link", node_id=self.next_id(), attributes=attributes,
                children=self.map_inline(token.get("children", [])),
            )
        if kind == "inline_html":
            # 保留行内 HTML 的来源标记，仅供 PDF 资源扫描识别 img；最终 HTML 仍由前端净化。
            return DocumentNode(type="text", node_id=self.next_id(), text=token.get("raw", ""), attributes={"raw_html": True})
        if kind == "codespan":
            return DocumentNode(type="codespan", node_id=self.next_id(), text=token.get("raw", ""))
        if kind == "image":
            # mistune 图片 token：src 在 attrs.url，alt 来自 children 的文本，title 在 attrs.title
            attrs = token.get("attrs", {})
            alt = "".join(
                child.get("raw", "")
                for child in token.get("children", [])
                if child.get("type") == "text"
            )
            attributes = {"src": attrs.get("url", "")}
            if alt:
                attributes["alt"] = alt
            if attrs.get("title"):
                attributes["title"] = attrs["title"]
            return DocumentNode(type="image", node_id=self.next_id(), attributes=attributes)
        if kind == "inline_math":
            return DocumentNode(
                type="math_inline", node_id=self.next_id(), text=token.get("raw", "")
            )
        if kind == "softbreak":
            # HTML 中换行会折叠为空白，软换行按空格表达
            return DocumentNode(type="text", node_id=self.next_id(), text=" ")
        if kind == "linebreak":
            return DocumentNode(type="linebreak", node_id=self.next_id())
        # 未知行内 token 保守保留原文
        raw = token.get("raw", "")
        if raw:
            return DocumentNode(type="text", node_id=self.next_id(), text=raw)
        return None

    def _map_code(self, token: dict) -> DocumentNode:
        info = (token.get("attrs", {}).get("info") or "").strip()
        lang = info.split()[0].lower() if info else ""
        code = token.get("raw", "").rstrip("\n")
        if lang == _MERMAID_LANG:
            return DocumentNode(type="mermaid", node_id=self.next_id(), text=code)
        if lang in _FUNCTION_PLOT_LANGS:
            return DocumentNode(type="function_plot", node_id=self.next_id(), text=code)
        attributes = {"language": lang} if lang else {}
        return DocumentNode(
            type="code_block", node_id=self.next_id(), attributes=attributes, text=code
        )

    def _map_table(self, token: dict) -> DocumentNode:
        rows: list[DocumentNode] = []
        for child in token.get("children", []):
            if child["type"] == "table_head":
                rows.append(self._map_table_row(child, head=True))
            elif child["type"] == "table_body":
                for row in child.get("children", []):
                    if row["type"] == "table_row":
                        rows.append(self._map_table_row(row, head=False))
            elif child["type"] == "table_row":
                rows.append(self._map_table_row(child, head=False))
        return DocumentNode(type="table", node_id=self.next_id(), children=rows)

    def _map_table_row(self, token: dict, *, head: bool) -> DocumentNode:
        cells: list[DocumentNode] = []
        for cell in token.get("children", []):
            if cell["type"] != "table_cell":
                continue
            attrs = cell.get("attrs", {})
            cell_attributes = {"head": bool(attrs.get("head", head))}
            if attrs.get("align"):
                cell_attributes["align"] = attrs["align"]
            cells.append(
                DocumentNode(
                    type="table_cell",
                    node_id=self.next_id(),
                    attributes=cell_attributes,
                    children=self.map_inline(cell.get("children", [])),
                )
            )
        return DocumentNode(
            type="table_row", node_id=self.next_id(), attributes={"head": head}, children=cells
        )
