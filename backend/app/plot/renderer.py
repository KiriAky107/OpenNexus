"""StaticRenderer 内部契约（契约 §10.4）。

把「静态可视化」抽象为统一请求/协议：导出器只面向 StaticRenderer，不再直接调用
``render_svg`` 等具体实现。后端当前仅能静态渲染函数图像；Mermaid 后端无渲染能力，
返回占位结果交前端渲染。
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.plot.model import FunctionPlot, FunctionPlotParseResult, StaticRenderResult
from app.plot.parser import parse_source
from app.plot.render import render_svg


class StaticRenderRequest(BaseModel):
    """一次静态渲染请求；source_hash 供缓存/去重，theme 供主题化渲染。"""

    kind: Literal["function_plot", "mermaid"]
    source: str
    source_hash: str = ""
    theme: str | None = None
    width: int | None = None
    height: int | None = None


class StaticRenderer(Protocol):
    """静态渲染器协议：请求 → 渲染结果（content 为可直接内嵌的标记）。"""

    def render(self, request: StaticRenderRequest) -> StaticRenderResult: ...


class FunctionPlotStaticRenderer:
    """函数图像渲染器：parse_source 解析 → render_svg 输出内嵌 SVG。

    ``parse`` 与 ``render_plot`` 拆开，供导出器在渲染前先拿 node_count 做文档级
    累计复杂度预算、并消费解析诊断。
    """

    def parse(self, request: StaticRenderRequest) -> FunctionPlotParseResult:
        return parse_source(request.source)

    def render(self, request: StaticRenderRequest) -> StaticRenderResult:
        parsed = self.parse(request)
        if parsed.plot is None:
            raise ValueError("function-plot source has no valid plot")
        return render_svg(parsed.plot, request.theme or 'light')

    def render_plot(self, plot: FunctionPlot) -> StaticRenderResult:
        return render_svg(plot)


class MermaidStaticRenderer:
    """Mermaid 后端无渲染能力：返回空占位结果，交前端渲染。"""

    def render(self, request: StaticRenderRequest) -> StaticRenderResult:
        return StaticRenderResult(
            content="",
            mime_type="text/plain",
            width=0,
            height=0,
            warnings=["mermaid 需前端渲染，已保留为占位代码块"],
        )
