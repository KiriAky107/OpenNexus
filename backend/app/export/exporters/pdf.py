"""PdfExporter：Document AST → PDF（reportlab platypus）。

v1 为文本优先：标题/段落/行内强调与链接/列表/引用/表格/代码块/数学文本均可导出；
function_plot 内嵌为矢量图（reportlab Drawing），mermaid 保留源码占位并记 warning。
中文字体用 reportlab 内置 STSong-Light CID 字体，避免外部字体依赖。CID 字体无独立
bold/italic 字重，故行内强调退化为普通文本（内容不丢、样式简化），标题靠字号区分层级。
"""

from __future__ import annotations

import html as _html
from io import BytesIO

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    Indenter,
    XPreformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from app.contracts import ExportOptions
from app.export.themes import CALLOUTS, pdf_palette
from app.export.document import Document, DocumentNode, ExportResult
from app.export.exporters._common import (
    MERMAID_WARNING,
    RAW_HTML_WARNING,
    format_meta_value,
    format_plot_diagnostic,
    safe_url,
)
from app.plot.render_reportlab import render_drawing
from app.plot.renderer import FunctionPlotStaticRenderer, StaticRenderRequest

from app.export.fonts import FONT as _FONT

_MIME = "application/pdf"

_PAGE_SIZES = {"a4": A4, "letter": letter}

# 标题字号随层级递减；标题不依赖粗体（CID 无粗体字重），靠字号拉开层级
_HEADING_SIZES = {1: 20, 2: 16, 3: 14, 4: 12, 5: 11, 6: 10.5}
# 引用块文字颜色，与 HtmlExporter 的引用灰一致
_QUOTE_COLOR = "#57606a"


def _make_styles(palette) -> dict[str, ParagraphStyle]:
    body = ParagraphStyle(
        "pdf-body",
        fontName=_FONT,
        textColor=palette["text"],
        fontSize=10.5,
        leading=16,
        spaceAfter=6,
    )
    title = ParagraphStyle("pdf-title", parent=body, fontSize=22, leading=28, spaceAfter=12)
    quote = ParagraphStyle(
        "pdf-quote",
        parent=body,
        leftIndent=14,
        textColor=palette["muted"],
        spaceBefore=4,
        spaceAfter=6,
    )
    code = ParagraphStyle(
        "pdf-code",
        parent=body,
        fontSize=9,
        leading=12,
        leftIndent=6,
        rightIndent=6,
        backColor=palette["code"],
        borderColor=palette["border"],
        borderWidth=0.5,
        borderPadding=6,
        spaceBefore=4,
        spaceAfter=8,
    )
    math = ParagraphStyle("pdf-math", parent=body, alignment=TA_CENTER, spaceBefore=6)
    cell = ParagraphStyle("pdf-cell", parent=body, fontSize=10, leading=14, spaceAfter=0)
    cell_head = ParagraphStyle(
        "pdf-cell-head", parent=cell, textColor=palette["text"], fontSize=10
    )
    meta = ParagraphStyle("pdf-meta", parent=body, fontSize=8.5, leading=13, textColor=palette["muted"])
    styles: dict[str, ParagraphStyle] = {
        "body": body,
        "title": title,
        "quote": quote,
        "code": code,
        "math": math,
        "cell": cell,
        "cell_head": cell_head,
        "meta": meta,
    }
    for level, size in _HEADING_SIZES.items():
        styles[f"h{level}"] = ParagraphStyle(
            f"pdf-h{level}",
            parent=body,
            fontSize=size,
            leading=size * 1.4,
            spaceBefore=14 if level <= 2 else 10,
            spaceAfter=6,
            keepWithNext=True,
        )
    return styles


class PdfExporter:
    """实现 DocumentExporter：递归渲染 Document AST 为 PDF 字节流。"""

    def render(self, document: Document, options: ExportOptions) -> ExportResult:
        """同步渲染；CPU 密集，调用方应放入线程执行，避免阻塞事件循环。"""
        warnings: list[str] = []
        self._palette = pdf_palette(options, warnings)
        self._styles = _make_styles(self._palette)
        if _FONT == "STSong-Light": warnings.append("PDF 使用 CID 字体，阅读器需提供中文字体；可配置 APP_EXPORT_FONT 嵌入 TrueType 字体")

        page = _PAGE_SIZES.get((options.page_size or "A4").lower(), A4)
        self._options = options
        self._plot_renderer = FunctionPlotStaticRenderer()
        # 内容区宽度（左右各 20mm 边距），供函数图像缩放适配页面
        self._plot_width = page[0] - 40 * mm - 12
        self._plot_height = page[1] - 36 * mm - 12
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=page,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=str(document.attributes.get("title") or "") or None,
        )

        story: list = []
        self._render_header(document, options, story)
        self._render_children(document.children, story, warnings)

        def paint_page(canvas, template):
            canvas.saveState()
            canvas.setFillColor(self._palette['page'])
            canvas.rect(0, 0, page[0], page[1], fill=1, stroke=0)
            canvas.setFillColor(self._palette['surface'])
            canvas.roundRect(12*mm, 10*mm, page[0]-24*mm, page[1]-20*mm, 5*mm, fill=1, stroke=0)
            canvas.restoreState()
        doc.build(story, onFirstPage=paint_page, onLaterPages=paint_page)
        return ExportResult(content=buf.getvalue(), mime_type=_MIME, warnings=warnings)

    async def export(self, document: Document, options: ExportOptions) -> ExportResult:
        """契约要求的 async 接口；渲染本身同步，直接转发到 render。"""
        return self.render(document, options)

    # --- 文档头部 ---
    def _render_header(self, document: Document, options: ExportOptions, story: list) -> None:
        title = str(document.attributes.get("title") or "")
        if options.include_title and title:
            story.append(Paragraph(_html.escape(title), self._styles["title"]))
        if options.include_metadata:
            metadata = document.attributes.get("metadata")
            if metadata:
                for key, value in metadata.items():
                    text = f"{_html.escape(str(key))}: {_html.escape(format_meta_value(value))}"
                    story.append(Paragraph(text, self._styles["meta"]))

    # --- 块级 ---
    def _render_children(self, children: list[DocumentNode], story: list, warnings: list[str]) -> None:
        for child in children:
            self._render_block(child, story, warnings)

    def _render_block(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        if node.attributes.get('static_png'):
            from reportlab.platypus import Image
            image = Image(BytesIO(node.attributes['static_png']))
            scale = min(1, self._plot_width / image.imageWidth, self._plot_height / image.imageHeight)
            image.drawWidth = image.imageWidth * scale
            image.drawHeight = image.imageHeight * scale
            story.append(image)
            return
        handler = getattr(self, f"_block_{node.type}", None)
        if handler is not None:
            handler(node, story, warnings)
        else:
            warnings.append(f"无法表示的节点类型已跳过：{node.type}")

    def _block_heading(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        level = max(1, min(6, int(node.attributes.get("level", 1))))
        inline = self._render_inline(node.children, warnings)
        story.append(Paragraph(inline, self._styles[f"h{level}"]))

    def _block_paragraph(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        story.append(Paragraph(self._render_inline(node.children, warnings), self._styles["body"]))

    def _block_callout(self, node, story, warnings):
        kind = node.attributes['kind']
        icon, color = CALLOUTS[kind]
        from reportlab.lib.colors import HexColor
        background = HexColor(self._palette['code'])
        if .2126*background.red + .7152*background.green + .0722*background.blue < .5:
            color = {'#0969da':'#a5d6ff','#7041a0':'#d2a8ff','#176f41':'#7ee787','#805400':'#f2cc60','#b42318':'#ffa198','#57606a':self._palette['muted']}[color]
        title = self._render_inline(node.children[0].children,warnings)
        style = ParagraphStyle('callout-'+kind,parent=self._styles['body'],textColor=color,
            backColor=self._palette['code'],borderColor=color,borderWidth=1,borderPadding=6,spaceBefore=8,spaceAfter=8)
        story.append(Paragraph(_html.escape(icon)+' '+title,style))
        self._render_children(node.children[1:],story,warnings)

    def _block_blockquote(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        # 引用块的直接子节点是块级节点（paragraph/list 等），不能交给行内渲染器，
        # 否则正文会被当作「无法表示的行内节点」丢弃；逐个渲染并继承引用缩进/颜色。
        for child in node.children:
            if child.type == "paragraph":
                story.append(
                    Paragraph(self._render_inline(child.children, warnings), self._styles["quote"])
                )
            elif child.type == "list":
                self._block_list(child, story, warnings, indent=14, color=self._palette['muted'])
            else:
                self._render_block(child, story, warnings)

    def _block_list(
        self,
        node: DocumentNode,
        story: list,
        warnings: list[str],
        indent: int = 14,
        color: str | None = None,
    ) -> None:
        ordered = bool(node.attributes.get("ordered"))
        for index, item in enumerate(node.children, start=1):
            self._block_list_item(item, story, warnings, ordered, index, indent, color)

    def _block_list_item(
        self,
        item: DocumentNode,
        story: list,
        warnings: list[str],
        ordered: bool,
        index: int,
        indent: int,
        color: str | None = None,
    ) -> None:
        if item.attributes.get("task"):
            marker = "☑ " if item.attributes.get("checked") else "☐ "
        else:
            marker = f"{index}. " if ordered else "• "
        style_kwargs: dict = dict(
            parent=self._styles["body"],
            leftIndent=indent,
            firstLineIndent=-7,
            spaceAfter=2,
        )
        if color:
            style_kwargs["textColor"] = color
        style = ParagraphStyle(f"pdf-li-{indent}-{color or 'normal'}", **style_kwargs)
        # 按 AST 顺序逐段输出：正文暂存为行内标记文本，遇到嵌套列表先 flush 再递归、
        # 之后继续后续正文，保持「父段—子列表—后续段」的原始顺序（而不是把所有正文
        # 都挤到子列表之前）。直接行内节点（text/strong/link 等）走 _render_inline_node，
        # 保留加粗/链接等语义，不能只渲染其 children 而丢掉格式。
        parts: list[str] = []
        first = True

        def flush() -> None:
            nonlocal first
            text = "<br/>".join(parts)
            if first:
                text = marker + text
                first = False
            if text:
                story.append(Paragraph(text, style))
            parts.clear()

        for child in item.children:
            if child.type == "list":
                flush()
                self._block_list(child, story, warnings, indent + 14, color)
            elif child.type == "paragraph":
                parts.append(self._render_inline(child.children, warnings))
            elif hasattr(self, f"_block_{child.type}"):
                flush()
                # Keep block content inside the list frame, including tables and callouts.
                story.append(Indenter(left=indent))
                self._render_block(child, story, warnings)
                story.append(Indenter(left=-indent))
            else:
                parts.append(self._render_inline_node(child, warnings))
        flush()

    def _block_table(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        rows = node.children
        if not rows:
            return
        data: list[list[Paragraph]] = []
        head_row_count = 0
        for row in rows:
            head = bool(row.attributes.get("head"))
            if head:
                head_row_count += 1
            cells = [
                Paragraph(
                    self._render_inline(cell.children, warnings),
                    self._styles["cell_head" if cell.attributes.get("head") else "cell"],
                )
                for cell in row.children
            ]
            data.append(cells)
        table = Table(data, repeatRows=head_row_count)
        commands = [
            ("GRID", (0, 0), (-1, -1), 0.5, self._palette["border"]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        if head_row_count:
            commands.append(("BACKGROUND", (0, 0), (-1, head_row_count - 1), self._palette["code"]))
        table.setStyle(TableStyle(commands))
        story.append(table)

    def _block_code_block(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        story.append(XPreformatted(_html.escape(node.text), self._styles["code"]))

    def _block_thematic_break(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        story.append(Spacer(1, 4))
        story.append(HRFlowable(width="100%", color=self._palette["border"], thickness=0.5))
        story.append(Spacer(1, 6))

    def _block_mermaid(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        warnings.append(MERMAID_WARNING)
        story.append(XPreformatted(_html.escape(node.text), self._styles["code"]))

    def _block_function_plot(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        # 解析与渲染共同纳入局部异常回退：单个图像失败只回退占位 + warning，
        # 绝不阻断整篇导出（含复杂表达式触发的 RecursionError 等异常）。
        try:
            request = StaticRenderRequest(
                kind="function_plot", source=node.text, theme=self._options.theme_id
            )
            from app.plot.parser import parse_source
            parsed = parse_source(request.source, unlimited=True)
            for diag in parsed.diagnostics:
                warnings.append(format_plot_diagnostic(diag))
            if parsed.plot is None:
                story.append(XPreformatted(_html.escape(node.text), self._styles["code"]))
                return
            # Drawing 本身即 Flowable，缩放后追加到 story，与 HTML 视觉一致
            drawing = render_drawing(parsed.plot, width=self._plot_width, palette=self._palette, unlimited=True, max_height=self._plot_height)
            story.append(drawing)
        except Exception as exc:
            warnings.append(f"函数图像：解析或渲染失败，已回退占位（{exc}）")
            story.append(XPreformatted(_html.escape(node.text), self._styles["code"]))

    def _block_math_block(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        story.append(Paragraph(f"$${_html.escape(node.text)}$$", self._styles["math"]))

    def _block_html_block(self, node: DocumentNode, story: list, warnings: list[str]) -> None:
        # 原始 HTML 不可信，按纯文本保留正文
        warnings.append(RAW_HTML_WARNING)
        story.append(Paragraph(_html.escape(node.text), self._styles["body"]))

    # --- 行内（产出 reportlab Paragraph 标记文本） ---
    def _render_inline(self, children: list[DocumentNode], warnings: list[str]) -> str:
        return "".join(self._render_inline_node(child, warnings) for child in children)

    def _render_inline_node(self, node: DocumentNode, warnings: list[str]) -> str:
        if node.attributes.get('static_png'):
            import base64
            from PIL import Image as PILImage
            raw = node.attributes['static_png']
            with PILImage.open(BytesIO(raw)) as image:
                scale = min(.4 if node.type.startswith('math') else 1, 350/image.width, 160/image.height)
                width, height = image.width*scale, image.height*scale
            data = base64.b64encode(raw).decode()
            return f'<img src="data:image/png;base64,{data}" width="{width}" height="{height}" valign="middle"/>'
        t = node.type
        if t == "text":
            return _html.escape(node.text)
        if t in ("strong", "emphasis"):
            return self._render_inline(node.children, warnings)
        if t == "codespan":
            return f'<font size="9">{_html.escape(node.text)}</font>'
        if t == "link":
            inner = self._render_inline(node.children, warnings)
            href = str(node.attributes.get("href") or "")
            safe_href = safe_url(href)
            if safe_href is None:
                warnings.append(f"链接协议不安全，已降级为纯文本：{href!r}")
                return inner
            return f'<a href="{_html.escape(safe_href)}" color="{self._palette["accent"]}">{inner}</a>'
        if t == "image":
            src = str(node.attributes.get("src") or "")
            alt = str(node.attributes.get("alt") or "")
            if safe_url(src) is None:
                warnings.append(f"图片地址不安全，已跳过：{src!r}")
            else:
                warnings.append("图片未内嵌到 PDF，已用替代文本表示")
            return _html.escape(alt) if alt else ""
        if t == "math_inline":
            return f"\\({_html.escape(node.text)}\\)"
        if t == "linebreak":
            return "<br/>"
        warnings.append(f"无法表示的行内节点已跳过：{t}")
        return ""
