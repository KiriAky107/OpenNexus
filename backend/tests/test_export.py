"""Export Service 的单元与端到端测试。

沿用 conftest 隔离机制：APP_DATA_DIR / DB / Vault / exports 目录都落在临时目录，
不读写真实数据。导出采用「创建即 queued + 后台 Task 执行」的异步模型，测试在同一
事件循环内创建并等待后台任务结束，得到终态 ExportJob 后再断言。
"""

from __future__ import annotations

import asyncio
import base64
import re
import zlib
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.contracts import (
    ExportFormat,
    ExportJob,
    ExportOptions,
    ExportRequest,
    ExportSource,
    ExportSourceType,
    ExportStatus,
)
from app.errors import ApiError
from app.export import service as export_service
from app.export.exporters.html import HtmlExporter
from app.export.markdown import parse_document

MD = """# 进程调度

一些 **加粗** 和 *斜体*，[链接](https://a.b) 与 `code`。

- 项目一
- 项目二

```python
print(1)
```

```mermaid
graph LR
```

```function_plot
y = x
```

| a | b |
|---|---|
| 1 | 2 |

行内 $x^2$ 与块级
$$
y = mx + b
$$
"""


@pytest.fixture(autouse=True)
def _reset_export_state():
    """清空内存注册表，避免跨用例的任务/取消标志互相污染。

    每个用例经 `asyncio.run()` 使用独立事件循环，模块级 Semaphore 会绑定到首个
    循环，跨用例复用会触发「bound to a different event loop」；此处每例重建槽位。
    """
    export_service._jobs.clear()
    export_service._tasks.clear()
    export_service._cancel_flags.clear()
    export_service._render_slots = asyncio.Semaphore(export_service.MAX_CONCURRENT_RENDERS)
    yield
    export_service._jobs.clear()
    export_service._tasks.clear()
    export_service._cancel_flags.clear()


def _create_and_wait(request: ExportRequest) -> object:
    """创建导出并在同一事件循环内等待后台任务结束，返回终态 ExportJob。"""

    async def _execute():
        job = await export_service.create_export(request)
        return await export_service.wait_for_export(job.job_id)

    return asyncio.run(_execute())


# --------------------------------------------------------------------------- #
# markdown → Document AST
# --------------------------------------------------------------------------- #
def _types(nodes) -> list[str]:
    return [n.type for n in nodes]


def test_parse_document_heading_and_inline() -> None:
    doc = parse_document("# 标题\n\n一段 **加粗** 和 [链接](https://a.b)。")

    assert doc.type == "document"
    heading = doc.children[0]
    assert heading.type == "heading"
    assert heading.attributes["level"] == 1

    para = doc.children[1]
    assert para.type == "paragraph"
    kinds = _types(para.children)
    assert "text" in kinds
    assert "strong" in kinds
    assert "link" in kinds

    link = next(c for c in para.children if c.type == "link")
    assert link.attributes["href"] == "https://a.b"


def test_parse_document_list_and_code_fencing() -> None:
    doc = parse_document("- a\n- b\n\n```mermaid\ngraph LR\n```\n\n```function_plot\ny=x\n```\n\n```python\nx\n```")

    kinds = [c.type for c in doc.children]
    assert kinds[0] == "list"
    assert kinds[1] == "mermaid"
    assert kinds[2] == "function_plot"
    assert kinds[3] == "code_block"

    code = doc.children[3]
    assert code.attributes["language"] == "python"
    assert code.text == "x"


def test_parse_document_table_and_math() -> None:
    doc = parse_document("| a | b |\n|---|---|\n| 1 | 2 |\n\n$x^2$\n\n$$\ny=mx\n$$")

    table = doc.children[0]
    assert table.type == "table"
    assert table.children[0].type == "table_row"
    assert table.children[0].children[0].attributes["head"] is True

    # 表格后是「行内数学所在段落」与「块级数学」
    kinds = [c.type for c in doc.children[1:]]
    assert "paragraph" in kinds
    assert "math_block" in kinds


def test_parse_document_image_maps_src_alt_title() -> None:
    doc = parse_document('![替代文本](https://a.b/img.png "标题")')
    img = doc.children[0].children[0]
    assert img.type == "image"
    assert img.attributes["src"] == "https://a.b/img.png"
    assert img.attributes["alt"] == "替代文本"
    assert img.attributes["title"] == "标题"


def test_parse_document_function_plot_dash_alias() -> None:
    doc = parse_document("```function-plot\ny = x^2\n```")
    assert doc.children[0].type == "function_plot"
    assert doc.children[0].text == "y = x^2"


# --------------------------------------------------------------------------- #
# HtmlExporter
# --------------------------------------------------------------------------- #
async def _render(markdown: str, *, title: str = "") -> str:
    doc = parse_document(markdown)
    doc.attributes["title"] = title
    result = await HtmlExporter().export(doc, ExportOptions())
    return result.content.decode("utf-8")


def test_html_exporter_renders_basic_nodes_and_escapes() -> None:
    html = asyncio.run(_render("# 标题\n\n**加粗** [链接](https://a.b) 与 <b>原始</b>。"))

    assert "<h1>标题</h1>" in html
    assert "<strong>加粗</strong>" in html
    assert '<a href="https://a.b">链接</a>' in html
    # 原始 HTML 必须被转义，不能注入文档
    assert "&lt;b&gt;原始&lt;/b&gt;" in html
    assert "<b>原始</b>" not in html


def test_html_exporter_marks_mermaid_and_function_plot() -> None:
    result = asyncio.run(HtmlExporter().export(parse_document("```mermaid\ngraph LR\n```"), ExportOptions()))

    html = result.content.decode("utf-8")
    assert '<pre class="mermaid">graph LR</pre>' in html
    assert any("mermaid" in w for w in result.warnings)


def test_html_exporter_rejects_unsafe_link_protocol() -> None:
    result = asyncio.run(
        HtmlExporter().export(parse_document("[点我](javascript:alert(1))"), ExportOptions())
    )
    html = result.content.decode("utf-8")
    assert "javascript:" not in html
    assert "点我" in html
    assert any("不安全" in w for w in result.warnings)


def test_html_exporter_rejects_unsafe_image_protocol() -> None:
    result = asyncio.run(
        HtmlExporter().export(parse_document("![alt](data:text/html,<script>)"), ExportOptions())
    )
    html = result.content.decode("utf-8")
    assert "data:" not in html
    assert "<img" not in html
    assert "alt" in html
    assert any("不安全" in w for w in result.warnings)


def test_html_exporter_preserves_raw_html_block() -> None:
    result = asyncio.run(
        HtmlExporter().export(parse_document("<div>重要正文</div>"), ExportOptions())
    )
    html = result.content.decode("utf-8")
    assert "重要正文" in html
    assert "<div>" not in html
    assert "&lt;div&gt;重要正文&lt;/div&gt;" in html
    assert any("原始 HTML" in w for w in result.warnings)


def test_html_exporter_include_title_and_metadata() -> None:
    doc = parse_document("正文")
    doc.attributes["title"] = "操作系统复习"
    doc.attributes["metadata"] = {"tags": ["os", "复习"]}

    opts = ExportOptions(include_title=True, include_metadata=True)
    result = asyncio.run(HtmlExporter().export(doc, opts))
    html = result.content.decode("utf-8")

    assert '<h1 class="title">操作系统复习</h1>' in html
    assert "os, 复习" in html


# --------------------------------------------------------------------------- #
# ExportService
# --------------------------------------------------------------------------- #
def _markdown_request(markdown: str, *, format: ExportFormat = ExportFormat.html) -> ExportRequest:
    return ExportRequest(
        source=ExportSource(type=ExportSourceType.markdown, markdown=markdown),
        format=format,
    )


def test_export_markdown_source_completes_and_writes_file() -> None:
    finished = _create_and_wait(_markdown_request(MD))

    assert finished.status == ExportStatus.completed
    assert finished.file is not None
    assert finished.file.mime_type == "text/html"
    assert finished.file.size > 0
    assert len(finished.file.sha256) == 64

    path = get_settings().exports_path / f"{finished.job_id}.html"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "进程调度" in content


def test_export_note_source_resolves_title_and_metadata() -> None:
    from app.services import note_service

    async def _go():
        note = await note_service.create_note(
            title="操作系统复习", markdown="# 进程调度\n\n内容。", folder="导出", tags=["os"]
        )
        request = ExportRequest(
            source=ExportSource(type=ExportSourceType.note, note_id=note.note_id),
            format=ExportFormat.html,
            options=ExportOptions(include_metadata=True),
        )
        job = await export_service.create_export(request)
        return await export_service.wait_for_export(job.job_id)

    finished = asyncio.run(_go())
    assert finished.status == ExportStatus.completed
    assert finished.file is not None
    assert finished.file.file_name == "操作系统复习.html"
    content = (get_settings().exports_path / f"{finished.job_id}.html").read_text(encoding="utf-8")
    assert "操作系统复习" in content
    assert "进程调度" in content


# --------------------------------------------------------------------------- #
# PDF / DOCX 导出
# --------------------------------------------------------------------------- #
def test_export_pdf_completes_with_pdf_magic_bytes() -> None:
    finished = _create_and_wait(_markdown_request(MD, format=ExportFormat.pdf))

    assert finished.status == ExportStatus.completed
    assert finished.file is not None
    assert finished.file.mime_type == "application/pdf"
    assert finished.file.file_name.endswith(".pdf")

    path = get_settings().exports_path / f"{finished.job_id}.pdf"
    assert path.exists()
    assert path.read_bytes()[:4] == b"%PDF"


def test_export_docx_completes_with_zip_magic_bytes() -> None:
    finished = _create_and_wait(_markdown_request(MD, format=ExportFormat.docx))

    assert finished.status == ExportStatus.completed
    assert finished.file is not None
    assert finished.file.mime_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert finished.file.file_name.endswith(".docx")

    path = get_settings().exports_path / f"{finished.job_id}.docx"
    assert path.exists()
    assert path.read_bytes()[:2] == b"PK"


def test_pdf_exporter_marks_plot_and_mermaid_as_placeholders() -> None:
    from app.export.exporters.pdf import PdfExporter

    md = "```mermaid\ngraph LR\n```\n\n```function_plot\ny = x\n```"
    result = asyncio.run(PdfExporter().export(parse_document(md), ExportOptions()))
    assert result.content[:4] == b"%PDF"
    assert any("mermaid" in w for w in result.warnings)
    assert any("函数图像" in w for w in result.warnings)


def test_docx_exporter_marks_plot_and_mermaid_as_placeholders() -> None:
    from app.export.exporters.docx import DocxExporter

    md = "```mermaid\ngraph LR\n```\n\n```function_plot\ny = x\n```"
    result = asyncio.run(DocxExporter().export(parse_document(md), ExportOptions()))
    assert result.content[:2] == b"PK"
    assert any("mermaid" in w for w in result.warnings)
    assert any("函数图像" in w for w in result.warnings)


def test_pdf_exporter_embeds_cjk_font() -> None:
    from app.export.exporters.pdf import PdfExporter

    doc = parse_document("# 进程调度\n\n一些中文正文。")
    doc.attributes["title"] = "操作系统复习"
    result = asyncio.run(PdfExporter().export(doc, ExportOptions(include_title=True)))
    assert result.content[:4] == b"%PDF"
    # 中文字体通过 STSong-Light CID 字体嵌入，PDF 内应引用该 BaseFont
    assert b"STSong-Light" in result.content


def test_docx_exporter_contains_cjk_text() -> None:
    import zipfile
    from io import BytesIO

    from app.export.exporters.docx import DocxExporter

    doc = parse_document("# 进程调度\n\n一些中文正文。")
    result = asyncio.run(DocxExporter().export(doc, ExportOptions()))
    with zipfile.ZipFile(BytesIO(result.content)) as zf:
        xml = zf.read("word/document.xml")
    assert "进程调度".encode("utf-8") in xml


def _pdf_unescape(raw: bytes) -> bytes:
    """反转义 PDF 字符串字面量（八进制转义与 \n \r \t 等）。"""
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        if b == 0x5C and i + 1 < n:  # 反斜杠转义
            nxt = raw[i + 1]
            if 0x30 <= nxt <= 0x37:  # 八进制（如 \000）
                j = i + 1
                digits = bytearray()
                while j < n and j < i + 4 and 0x30 <= raw[j] <= 0x37:
                    digits.append(raw[j])
                    j += 1
                out.append(int(digits.decode(), 8) & 0xFF)
                i = j
                continue
            simple = {0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09, 0x62: 0x08, 0x66: 0x0C}
            out.append(simple.get(nxt, nxt))
            i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


def _extract_pdf_text(content: bytes) -> str:
    """从 PDF 内容流提取文本（仅测试断言用，非完整 PDF 文本提取）。

    reportlab 对 CID 字体按 UTF-16BE（高位 0x00）编码，字符串写为 \000 前缀的八进制
    转义；这里解码 ASCII85+flate 内容流、反转义字符串并去掉 0x00 还原 ASCII 正文。
    """
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", content, re.DOTALL):
        raw = m.group(1).strip()
        if raw.endswith(b"~>"):
            raw = raw[:-2]
        try:
            dec = zlib.decompress(base64.a85decode(raw))
        except Exception:
            try:
                dec = zlib.decompress(raw)
            except Exception:
                dec = raw
        for sm in re.finditer(rb"\(((?:[^()\\]|\\.)*)\)\s*Tj", dec):
            text = _pdf_unescape(sm.group(1))
            if text.count(0) > len(text) // 4:
                text = text.replace(b"\x00", b"")
            chunks.append(text.decode("latin-1"))
    return "".join(chunks)


# --------------------------------------------------------------------------- #
# 审阅回归：结构内容验证（不只校验魔法字节，还验证产物正文）
# --------------------------------------------------------------------------- #
def test_pdf_blockquote_preserves_content() -> None:
    from app.export.exporters.pdf import PdfExporter

    # P2：引用块正文不能因「把块级子节点交给行内渲染器」而丢失
    result = asyncio.run(
        PdfExporter().export(parse_document("> quoted **content**"), ExportOptions())
    )
    text = _extract_pdf_text(result.content)
    assert "quoted" in text
    assert "content" in text
    assert not any("无法表示" in w for w in result.warnings)


def test_docx_blockquote_preserves_content() -> None:
    import zipfile
    from io import BytesIO

    from app.export.exporters.docx import DocxExporter

    result = asyncio.run(
        DocxExporter().export(parse_document("> quoted **content**"), ExportOptions())
    )
    with zipfile.ZipFile(BytesIO(result.content)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "quoted" in xml
    assert "content" in xml
    assert not any("无法表示" in w for w in result.warnings)


def test_pdf_nested_list_parent_before_child() -> None:
    from app.export.exporters.pdf import PdfExporter

    # P2：嵌套列表输出顺序颠倒——父级正文应在子列表之前
    result = asyncio.run(
        PdfExporter().export(parse_document("- parent\n  - child"), ExportOptions())
    )
    text = _extract_pdf_text(result.content)
    assert text.index("parent") < text.index("child")


def test_docx_nested_list_parent_before_child() -> None:
    import zipfile
    from io import BytesIO

    from app.export.exporters.docx import DocxExporter

    result = asyncio.run(
        DocxExporter().export(parse_document("- parent\n  - child"), ExportOptions())
    )
    with zipfile.ZipFile(BytesIO(result.content)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert xml.index("parent") < xml.index("child")


def test_export_cancel_queued_job_waiting_for_slot(monkeypatch) -> None:
    # P2：等待渲染槽位的任务取消后应立即进入 cancelled，不必等前面的渲染完成
    import threading

    real_render = export_service._render_document
    release = threading.Event()
    entered = 0
    lock = threading.Lock()

    def blocking_render(document, options, format):
        nonlocal entered
        with lock:
            entered += 1
        release.wait(timeout=5)
        return real_render(document, options, format)

    monkeypatch.setattr(export_service, "_render_document", blocking_render)

    async def _go():
        a = await export_service.create_export(_markdown_request("# a"))
        b = await export_service.create_export(_markdown_request("# b"))
        # 等 a/b 两个任务都拿到槽位并阻塞在渲染里
        for _ in range(2000):
            if entered >= 2:
                break
            await asyncio.sleep(0.001)
        c = await export_service.create_export(_markdown_request("# c"))
        await asyncio.sleep(0.01)  # 让 c 进入排队等待槽位
        export_service.cancel_export(c.job_id)
        finished_c = await export_service.wait_for_export(c.job_id)
        release.set()  # 放行前面的任务，避免测试挂起
        await asyncio.gather(
            export_service.wait_for_export(a.job_id),
            export_service.wait_for_export(b.job_id),
        )
        return finished_c

    finished = asyncio.run(_go())
    assert finished.status == ExportStatus.cancelled
    assert finished.file is None


def test_export_unknown_note_404() -> None:
    request = ExportRequest(
        source=ExportSource(type=ExportSourceType.note, note_id="note_missing"),
        format=ExportFormat.html,
    )
    with pytest.raises(ApiError) as exc:
        asyncio.run(export_service.create_export(request))
    assert exc.value.status_code == 404
    assert exc.value.code == "EXPORT_SOURCE_NOT_FOUND"


def test_export_empty_markdown_invalid() -> None:
    with pytest.raises(ApiError) as exc:
        asyncio.run(export_service.create_export(_markdown_request("   ")))
    assert exc.value.status_code == 400
    assert exc.value.code == "EXPORT_OPTIONS_INVALID"


def test_export_cancel_queued_job() -> None:
    async def _go():
        job = await export_service.create_export(_markdown_request("# x"))
        cancelled = export_service.cancel_export(job.job_id)
        assert cancelled is not None
        return await export_service.wait_for_export(job.job_id)

    finished = asyncio.run(_go())
    assert finished.status == ExportStatus.cancelled
    assert finished.file is None


def test_export_file_expired_410() -> None:
    async def _go():
        job = await export_service.create_export(_markdown_request("# x"))
        finished = await export_service.wait_for_export(job.job_id)
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        export_service._jobs[job.job_id] = finished.model_copy(
            update={"file": finished.file.model_copy(update={"expires_at": past})}
        )
        return job.job_id

    job_id = asyncio.run(_go())
    path = get_settings().exports_path / f"{job_id}.html"
    with pytest.raises(ApiError) as exc:
        export_service.get_export_file(job_id)
    assert exc.value.status_code == 410
    assert exc.value.code == "EXPORT_FILE_EXPIRED"
    assert not path.exists()  # 过期即清理产物文件
    assert export_service.get_export(job_id) is None  # 内存记录一并清理


def test_export_eviction_deletes_file() -> None:
    finished = _create_and_wait(_markdown_request("# 淘汰"))
    victim_path = get_settings().exports_path / f"{finished.job_id}.html"
    assert victim_path.exists()

    # 塞满 MAX_JOBS 个终态任务，下一次 create 会淘汰最旧的终态（finished 最先插入）
    for i in range(export_service.MAX_JOBS):
        export_service._jobs[f"export_fake_{i}"] = ExportJob(
            job_id=f"export_fake_{i}",
            status=ExportStatus.completed,
            format=ExportFormat.html,
            created_at=datetime.now(timezone.utc),
        )
    _create_and_wait(_markdown_request("# 触发淘汰"))
    assert not victim_path.exists()


def test_cleanup_orphan_files() -> None:
    exports_dir = get_settings().exports_path
    exports_dir.mkdir(parents=True, exist_ok=True)
    orphan = exports_dir / "export_orphan.html"
    orphan.write_text("stale", encoding="utf-8")

    finished = _create_and_wait(_markdown_request("# 保留"))
    keep_path = exports_dir / f"{finished.job_id}.html"
    assert keep_path.exists()

    removed = export_service.cleanup_orphan_files()
    assert removed >= 1
    assert not orphan.exists()
    assert keep_path.exists()  # 仍在注册表中的任务文件保留


def test_export_cancel_during_running(monkeypatch) -> None:
    import threading
    import time

    real_parse = parse_document
    started = threading.Event()

    def slow_parse(markdown: str):
        started.set()
        time.sleep(0.1)
        return real_parse(markdown)

    monkeypatch.setattr(export_service, "parse_document", slow_parse)

    async def _go():
        job = await export_service.create_export(_markdown_request("# 运行中取消"))
        while not started.is_set():
            await asyncio.sleep(0)
        export_service.cancel_export(job.job_id)
        return await export_service.wait_for_export(job.job_id)

    finished = asyncio.run(_go())
    assert finished.status == ExportStatus.cancelled
    assert finished.file is None
    assert not (get_settings().exports_path / f"{finished.job_id}.html").exists()


def test_export_list_and_get() -> None:
    finished = _create_and_wait(_markdown_request("# 列表测试"))

    items, total = export_service.list_exports(limit=50, offset=0)
    assert total == 1
    assert items[0].job_id == finished.job_id

    got = export_service.get_export(finished.job_id)
    assert got is not None and got.status == ExportStatus.completed

    assert export_service.get_export("export_missing") is None


# --------------------------------------------------------------------------- #
# 契约校验
# --------------------------------------------------------------------------- #
def test_export_source_requires_matching_field() -> None:
    with pytest.raises(ValidationError):
        ExportSource(type=ExportSourceType.note, note_id=None)
    with pytest.raises(ValidationError):
        ExportSource(type=ExportSourceType.markdown, markdown=None)


# --------------------------------------------------------------------------- #
# 审阅回归：资源上限
# --------------------------------------------------------------------------- #
def test_export_note_source_size_limit(monkeypatch) -> None:
    # P1：note 源超出 MAX_MARKDOWN_CHARS 应在创建期拒绝，不进入后台渲染
    from app.services import note_service

    monkeypatch.setattr(export_service, "MAX_MARKDOWN_CHARS", 10)

    async def _go():
        note = await note_service.create_note(
            title="超长笔记", markdown="a" * 20, folder="导出", tags=[]
        )
        return await export_service.create_export(
            ExportRequest(
                source=ExportSource(type=ExportSourceType.note, note_id=note.note_id),
                format=ExportFormat.html,
            )
        )

    with pytest.raises(ApiError) as exc:
        asyncio.run(_go())
    assert exc.value.status_code == 400
    assert exc.value.code == "EXPORT_OPTIONS_INVALID"


def test_export_output_too_large(monkeypatch) -> None:
    # P1：产物超出 MAX_EXPORT_BYTES 应标记 failed 且不落盘
    monkeypatch.setattr(export_service, "MAX_EXPORT_BYTES", 10)

    finished = _create_and_wait(_markdown_request("# 产物超限"))
    assert finished.status == ExportStatus.failed
    assert finished.error_code == "EXPORT_OUTPUT_TOO_LARGE"
    assert finished.file is None
    assert not (get_settings().exports_path / f"{finished.job_id}.html").exists()


def test_export_limits_concurrent_rendering(monkeypatch) -> None:
    # P1：并发渲染受 MAX_CONCURRENT_RENDERS 限制，大量任务不会同时占满工作线程
    import threading
    import time

    real_render = export_service._render_document
    active = 0
    peak = 0
    lock = threading.Lock()

    def slow_render(document, options, format):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return real_render(document, options, format)

    monkeypatch.setattr(export_service, "_render_document", slow_render)

    async def _go():
        jobs = [
            await export_service.create_export(_markdown_request(f"# t{i}"))
            for i in range(6)
        ]
        return [await export_service.wait_for_export(j.job_id) for j in jobs]

    finished = asyncio.run(_go())
    assert all(j.status == ExportStatus.completed for j in finished)
    assert peak <= export_service.MAX_CONCURRENT_RENDERS
