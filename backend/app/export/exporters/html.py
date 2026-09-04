"""HtmlExporter：Document AST → 完整 HTML5 文档（内嵌基础 CSS）。

对无法静态表达的节点（mermaid / function_plot）渲染为占位代码块并记 warning，不静默丢失；
严重内容缺失由 service 层以 EXPORT_UNSUPPORTED_CONTENT 判定，本层只负责逐节点渲染。
"""

from __future__ import annotations

import html
from datetime import datetime

from app.contracts import ExportOptions
from app.export.document import Document, DocumentNode, ExportResult

_MERMAID_WARNING = "mermaid 需前端渲染，已保留为占位代码块"
_FUNCTION_PLOT_WARNING = "函数图像渲染将在后续版本提供，已保留为占位代码块"

_BASE_CSS = """
body { margin: 0; background: #f6f7f9; color: #1f2328; font: 15px/1.7 -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif; }
article { max-width: 860px; margin: 0 auto; padding: 40px 48px; background: #fff; }
article.theme-dark { background: #0d1117; color: #c9d1d9; }
h1, h2, h3, h4, h5, h6 { line-height: 1.3; margin: 1.4em 0 0.6em; }
h1.title { margin-top: 0; }
p { margin: 0.6em 0; }
a { color: #0969da; }
code { font-family: 'JetBrains Mono', Consolas, monospace; font-size: 0.9em; background: #f0f1f3; padding: 0.15em 0.35em; border-radius: 3px; }
pre { background: #f6f8fa; padding: 14px 16px; border-radius: 6px; overflow-x: auto; }
pre.code-theme-github-dark { background: #0d1117; color: #c9d1d9; }
pre code { background: none; padding: 0; }
pre.mermaid, pre.function-plot { border: 1px dashed #d0d7de; }
blockquote { margin: 0.8em 0; padding: 0.2em 1em; border-left: 4px solid #d0d7de; color: #57606a; }
img { max-width: 100%; }
table { border-collapse: collapse; margin: 0.8em 0; }
th, td { border: 1px solid #d0d7de; padding: 6px 12px; }
th { background: #f6f8fa; }
dl.metadata { font-size: 0.85em; color: #57606a; border-top: 1px solid #eaeef2; border-bottom: 1px solid #eaeef2; padding: 0.6em 0; }
dl.metadata dt { display: inline; font-weight: 600; margin-right: 0.4em; }
dl.metadata dd { display: inline; margin: 0 1.2em 0 0; }
.math, .math-block { overflow-x: auto; padding: 0.4em 0; }
.task-list-item { list-style: none; }
.task-list-item input { margin-right: 0.4em; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 1.4em 0; }
""".strip()


class HtmlExporter:
    """实现 DocumentExporter：递归渲染 Document AST 为完整 HTML5 文档。"""

    async def export(self, document: Document, options: ExportOptions) -> ExportResult:
        self._options = options
        warnings: list[str] = []
        body = self._render_children(document.children, warnings)
        content = self._assemble(document, options, body, warnings)
        return ExportResult(
            content=content.encode("utf-8"), mime_type="text/html", warnings=warnings
        )

    def _assemble(
        self, document: Document, options: ExportOptions, body: str, warnings: list[str]
    ) -> str:
        title = str(document.attributes.get("title") or "")
        parts = [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
        ]
        if title:
            parts.append(f"<title>{html.escape(title)}</title>")
        parts.append(f"<style>{_BASE_CSS}</style>")
        parts.append("</head>")
        parts.append("<body>")
        parts.append(f'<article class="theme-{html.escape(options.theme_id)}">')
        if options.include_title and title:
            parts.append(f'<h1 class="title">{html.escape(title)}</h1>')
        if options.include_metadata:
            metadata = document.attributes.get("metadata")
            if metadata:
                parts.append(self._render_metadata(metadata))
        parts.append(body)
        parts.append("</article>")
        parts.append("</body>")
        parts.append("</html>")
        return "\n".join(parts) + "\n"

    def _render_metadata(self, metadata: dict) -> str:
        entries = ["<dl", ' class="metadata">']
        for key, value in metadata.items():
            entries.append(f"<dt>{html.escape(str(key))}</dt>")
            entries.append(f"<dd>{html.escape(self._fmt_meta_value(value))}</dd>")
        entries.append("</dl>")
        return "".join(entries)

    @staticmethod
    def _fmt_meta_value(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def _render_children(self, children: list[DocumentNode], warnings: list[str]) -> str:
        return "".join(self._render_node(child, warnings) for child in children)

    def _render_node(self, node: DocumentNode, warnings: list[str]) -> str:
        handler = getattr(self, f"_render_{node.type}", None)
        if handler is not None:
            return handler(node, warnings)
        warnings.append(f"无法表示的节点类型已跳过：{node.type}")
        return ""

    # --- 块级 ---
    def _render_heading(self, node: DocumentNode, warnings: list[str]) -> str:
        level = max(1, min(6, int(node.attributes.get("level", 1))))
        return f"<h{level}>{self._render_children(node.children, warnings)}</h{level}>"

    def _render_paragraph(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"<p>{self._render_children(node.children, warnings)}</p>"

    def _render_blockquote(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"<blockquote>{self._render_children(node.children, warnings)}</blockquote>"

    def _render_list(self, node: DocumentNode, warnings: list[str]) -> str:
        tag = "ol" if node.attributes.get("ordered") else "ul"
        return f"<{tag}>{self._render_children(node.children, warnings)}</{tag}>"

    def _render_list_item(self, node: DocumentNode, warnings: list[str]) -> str:
        inner = self._render_children(node.children, warnings)
        if node.attributes.get("task"):
            checked = " checked" if node.attributes.get("checked") else ""
            return (
                '<li class="task-list-item">'
                f'<input type="checkbox" disabled{checked}>{inner}</li>'
            )
        return f"<li>{inner}</li>"

    def _render_table(self, node: DocumentNode, warnings: list[str]) -> str:
        rows = node.children
        head_rows = [r for r in rows if r.attributes.get("head")]
        body_rows = [r for r in rows if not r.attributes.get("head")]
        parts = ["<table>"]
        if head_rows:
            parts.append("<thead>")
            parts.extend(self._render_node(r, warnings) for r in head_rows)
            parts.append("</thead>")
        if body_rows:
            parts.append("<tbody>")
            parts.extend(self._render_node(r, warnings) for r in body_rows)
            parts.append("</tbody>")
        parts.append("</table>")
        return "".join(parts)

    def _render_table_row(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"<tr>{self._render_children(node.children, warnings)}</tr>"

    def _render_table_cell(self, node: DocumentNode, warnings: list[str]) -> str:
        tag = "th" if node.attributes.get("head") else "td"
        return f"<{tag}>{self._render_children(node.children, warnings)}</{tag}>"

    def _render_code_block(self, node: DocumentNode, warnings: list[str]) -> str:
        lang = str(node.attributes.get("language") or "")
        code = html.escape(node.text)
        lang_cls = f' class="language-{html.escape(lang)}"' if lang else ""
        theme = html.escape(self._options.code_theme)
        return f'<pre class="code-theme-{theme}"><code{lang_cls}>{code}</code></pre>'

    def _render_thematic_break(self, node: DocumentNode, warnings: list[str]) -> str:
        return "<hr>"

    def _render_mermaid(self, node: DocumentNode, warnings: list[str]) -> str:
        warnings.append(_MERMAID_WARNING)
        return f'<pre class="mermaid">{html.escape(node.text)}</pre>'

    def _render_function_plot(self, node: DocumentNode, warnings: list[str]) -> str:
        warnings.append(_FUNCTION_PLOT_WARNING)
        return f'<pre class="function-plot">{html.escape(node.text)}</pre>'

    def _render_math_block(self, node: DocumentNode, warnings: list[str]) -> str:
        return f'<div class="math-block">$${html.escape(node.text)}$$</div>'

    # --- 行内 ---
    def _render_text(self, node: DocumentNode, warnings: list[str]) -> str:
        return html.escape(node.text)

    def _render_emphasis(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"<em>{self._render_children(node.children, warnings)}</em>"

    def _render_strong(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"<strong>{self._render_children(node.children, warnings)}</strong>"

    def _render_link(self, node: DocumentNode, warnings: list[str]) -> str:
        href = html.escape(str(node.attributes.get("href") or ""))
        title = str(node.attributes.get("title") or "")
        attrs = [f'href="{href}"']
        if title:
            attrs.append(f'title="{html.escape(title)}"')
        return f"<a {' '.join(attrs)}>{self._render_children(node.children, warnings)}</a>"

    def _render_codespan(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"<code>{html.escape(node.text)}</code>"

    def _render_image(self, node: DocumentNode, warnings: list[str]) -> str:
        src = html.escape(str(node.attributes.get("src") or ""))
        alt = html.escape(str(node.attributes.get("alt") or ""))
        title = str(node.attributes.get("title") or "")
        attrs = [f'src="{src}"', f'alt="{alt}"']
        if title:
            attrs.append(f'title="{html.escape(title)}"')
        return f"<img {' '.join(attrs)}>"

    def _render_math_inline(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"\\({html.escape(node.text)}\\)"

    def _render_linebreak(self, node: DocumentNode, warnings: list[str]) -> str:
        return "<br>"
