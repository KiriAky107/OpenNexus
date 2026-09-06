"""HtmlExporter：Document AST → 完整 HTML5 文档（内嵌基础 CSS）。

mermaid 等无法静态表达的节点渲染为占位代码块并记 warning，不静默丢失；function_plot
解析为静态 SVG 内嵌（解析失败回退占位并转诊断）；严重内容缺失由 service 层以
EXPORT_UNSUPPORTED_CONTENT 判定，本层只负责逐节点渲染。
"""

from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import urlparse

from app.contracts import ExportOptions
from app.export.document import Document, DocumentNode, ExportResult
from app.export.exporters._common import FunctionPlotBudget, format_plot_diagnostic
from app.plot.renderer import FunctionPlotStaticRenderer, StaticRenderRequest

_MERMAID_WARNING = "mermaid 需前端渲染，已保留为占位代码块"
_RAW_HTML_WARNING = "原始 HTML 已按纯文本转义保留"

# 链接/图片地址允许的协议；无 scheme 的相对地址视为安全，其余协议一律降级
_ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})


def _safe_url(url: str) -> str | None:
    """校验 URL 协议；安全返回原串，不安全返回 None。"""
    url = url.strip()
    if not url:
        return None
    scheme = urlparse(url).scheme.lower()
    if scheme and scheme not in _ALLOWED_URL_SCHEMES:
        return None
    return url

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
figure.function-plot { margin: 1em 0; text-align: center; }
figure.function-plot svg { max-width: 100%; height: auto; }
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

    def render(self, document: Document, options: ExportOptions) -> ExportResult:
        """同步渲染；CPU 密集，调用方应放入线程执行，避免阻塞事件循环。"""
        self._options = options
        self._plot_budget = FunctionPlotBudget()
        self._plot_renderer = FunctionPlotStaticRenderer()
        warnings: list[str] = []
        body = self._render_children(document.children, warnings)
        content = self._assemble(document, options, body, warnings)
        return ExportResult(
            content=content.encode("utf-8"), mime_type="text/html", warnings=warnings
        )

    async def export(self, document: Document, options: ExportOptions) -> ExportResult:
        """契约要求的 async 接口；渲染本身同步，直接转发到 render。"""
        return self.render(document, options)

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
        # 文档级数量上限：超出部分直接回退占位，不解析不采样，防止海量图像耗尽资源
        over = self._plot_budget.check_count()
        if over is not None:
            warnings.append(over)
            return f'<pre class="function-plot">{html.escape(node.text)}</pre>'
        # 解析与渲染共同纳入局部异常回退：单个图像失败只回退占位 + warning，
        # 绝不阻断整篇导出（含复杂表达式触发的 RecursionError 等异常）。
        try:
            request = StaticRenderRequest(
                kind="function_plot", source=node.text, theme=self._options.theme_id
            )
            parsed = self._plot_renderer.parse(request)
            for diag in parsed.diagnostics:
                warnings.append(format_plot_diagnostic(diag))
            if parsed.plot is None:
                return f'<pre class="function-plot">{html.escape(node.text)}</pre>'
            # 文档级累计复杂度预算：超出后回退占位，不再采样求值
            over = self._plot_budget.check_nodes(parsed.plot.node_count)
            if over is not None:
                warnings.append(over)
                return f'<pre class="function-plot">{html.escape(node.text)}</pre>'
            rendered = self._plot_renderer.render_plot(parsed.plot)
        except Exception as exc:
            warnings.append(f"函数图像：解析或渲染失败，已回退占位（{exc}）")
            return f'<pre class="function-plot">{html.escape(node.text)}</pre>'
        warnings.extend(rendered.warnings)
        return f'<figure class="function-plot">{rendered.content}</figure>'

    def _render_math_block(self, node: DocumentNode, warnings: list[str]) -> str:
        return f'<div class="math-block">$${html.escape(node.text)}$$</div>'

    def _render_html_block(self, node: DocumentNode, warnings: list[str]) -> str:
        # 原始 HTML 不可信，转义为纯文本展示，保证正文不丢且无注入风险
        warnings.append(_RAW_HTML_WARNING)
        return f'<div class="raw-html">{html.escape(node.text)}</div>'

    # --- 行内 ---
    def _render_text(self, node: DocumentNode, warnings: list[str]) -> str:
        return html.escape(node.text)

    def _render_emphasis(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"<em>{self._render_children(node.children, warnings)}</em>"

    def _render_strong(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"<strong>{self._render_children(node.children, warnings)}</strong>"

    def _render_link(self, node: DocumentNode, warnings: list[str]) -> str:
        inner = self._render_children(node.children, warnings)
        href = str(node.attributes.get("href") or "")
        safe_href = _safe_url(href)
        if safe_href is None:
            # 危险协议（如 javascript:）降级为纯文本，不输出可点击链接
            warnings.append(f"链接协议不安全，已降级为纯文本：{href!r}")
            return inner
        title = str(node.attributes.get("title") or "")
        attrs = [f'href="{html.escape(safe_href)}"']
        if title:
            attrs.append(f'title="{html.escape(title)}"')
        return f"<a {' '.join(attrs)}>{inner}</a>"

    def _render_codespan(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"<code>{html.escape(node.text)}</code>"

    def _render_image(self, node: DocumentNode, warnings: list[str]) -> str:
        src = str(node.attributes.get("src") or "")
        alt = str(node.attributes.get("alt") or "")
        safe_src = _safe_url(src)
        if safe_src is None:
            # 危险协议（如 data:/javascript:）跳过图片，仅输出 alt 文本
            warnings.append(f"图片地址不安全，已跳过：{src!r}")
            return html.escape(alt) if alt else ""
        title = str(node.attributes.get("title") or "")
        attrs = [f'src="{html.escape(safe_src)}"', f'alt="{html.escape(alt)}"']
        if title:
            attrs.append(f'title="{html.escape(title)}"')
        return f"<img {' '.join(attrs)}>"

    def _render_math_inline(self, node: DocumentNode, warnings: list[str]) -> str:
        return f"\\({html.escape(node.text)}\\)"

    def _render_linebreak(self, node: DocumentNode, warnings: list[str]) -> str:
        return "<br>"
