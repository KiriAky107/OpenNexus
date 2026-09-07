"""Function Plot 内部数据模型。

FunctionPlot 供预览和导出共享；StaticRenderResult 同时是交互预览端点的响应内容。
模型保留在独立包内，由 plot_routes 中的请求与响应类型注册 OpenAPI。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FunctionPlotExpression(BaseModel):
    """单条函数表达式；expression 为数学表达式文本（不含 ``y =`` 前缀）。"""

    expression: str
    label: str | None = None
    color: str | None = None


class PlotAxes(BaseModel):
    xlabel: str | None = None
    ylabel: str | None = None
    grid: bool = True


class FunctionPlot(BaseModel):
    version: int = 1
    expressions: list[FunctionPlotExpression]
    domain: tuple[float, float] = (-10.0, 10.0)
    range: tuple[float, float] | None = None
    axes: PlotAxes = Field(default_factory=PlotAxes)
    # 该块所有表达式 AST 节点数之和，供导出器做文档级累计复杂度预算
    node_count: int = 0


class PlotDiagnostic(BaseModel):
    severity: Literal["warning", "error"]
    code: str
    message: str
    line: int | None = None


class FunctionPlotParseResult(BaseModel):
    """解析结果：任一表达式 error 时 plot 为 None（整块回退占位），仅 warning 时 plot 有效。"""

    plot: FunctionPlot | None = None
    diagnostics: list[PlotDiagnostic] = Field(default_factory=list)


class StaticRenderResult(BaseModel):
    content: str
    mime_type: str = "image/svg+xml"
    width: int
    height: int
    warnings: list[str] = Field(default_factory=list)
