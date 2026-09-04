"""Function Plot 内部数据模型。

契约 §12.2 的 FunctionPlot 结构与 §10.4 的 StaticRenderResult 只在导出链路的后端内部
流转，不进入 HTTP 契约，因此与 Document AST 一样放在独立包内，不进 contracts.py。
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
