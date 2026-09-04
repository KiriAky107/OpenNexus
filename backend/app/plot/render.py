"""Function Plot → 静态 SVG 渲染。

只输出纯几何与 <text> 的 SVG（无 script/foreignObject/内联事件），可安全内嵌 HTML。
所有文本与颜色都经过转义/校验，不把用户输入直接拼进标记。
"""

from __future__ import annotations

import html
import math
import re
from typing import Callable

from app.plot.model import FunctionPlot, StaticRenderResult
from app.plot.parser import PlotParseError, evaluate, parse_expression

_WIDTH = 640
_HEIGHT = 480
_MARGIN = 52  # 四周留白，放轴刻度与标签
_SAMPLES = 400
_PALETTE = ["#0969da", "#d1242f", "#1a7f37", "#8250df", "#bf8700", "#e36209"]
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _safe_color(color: str | None, fallback: str) -> str:
    return color.strip() if color and _COLOR_RE.match(color.strip()) else fallback


def _fmt_num(v: float) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1e6 or abs(v) < 1e-6:
        return f"{v:.2e}"
    return f"{v:.6g}"


def _nice_step(span: float, target_ticks: int = 6) -> float:
    raw = abs(span) / target_ticks
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _ticks(lo: float, hi: float, step: float) -> list[float]:
    first = math.ceil(lo / step) * step
    values: list[float] = []
    v = first
    while v <= hi + step * 1e-9:
        values.append(v)
        v += step
    return values


def _compute_range(
    plot: FunctionPlot,
    fns: list[tuple[object, object]],
    xmin: float,
    xmax: float,
) -> tuple[float, float]:
    """采样确定 y 范围；指定 range 则优先，否则取有限样本的 min/max 加 5% 余量。"""
    if plot.range is not None:
        return float(plot.range[0]), float(plot.range[1])

    ys: list[float] = []
    for _expr, tree in fns:
        for i in range(_SAMPLES + 1):
            x = xmin + (xmax - xmin) * i / _SAMPLES
            try:
                y = evaluate(tree, x)  # type: ignore[arg-type]
            except (ValueError, ZeroDivisionError, OverflowError):
                continue
            if math.isfinite(y):
                ys.append(y)

    if not ys:
        return -10.0, 10.0
    lo, hi = min(ys), max(ys)
    if lo == hi:
        lo -= 1.0
        hi += 1.0
    pad = (hi - lo) * 0.05
    return lo - pad, hi + pad


def _polyline(
    tree: object,
    xmin: float,
    xmax: float,
    sx: Callable[[float], float],
    sy: Callable[[float], float],
    color: str,
) -> str:
    """采样并把非有限点处断开成多段 polyline，避免画穿渐近线。"""
    segments: list[str] = []
    points: list[str] = []
    for i in range(_SAMPLES + 1):
        x = xmin + (xmax - xmin) * i / _SAMPLES
        try:
            y = evaluate(tree, x)  # type: ignore[arg-type]
        except (ValueError, ZeroDivisionError, OverflowError):
            y = math.nan
        if not math.isfinite(y):
            if points:
                segments.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}"/>')
                points = []
            continue
        px = sx(x)
        py = sy(y)
        points.append(f"{px:.2f},{py:.2f}")
    if points:
        segments.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}"/>')
    return "".join(segments)


def _grid(
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    sx: Callable[[float], float],
    sy: Callable[[float], float],
) -> str:
    parts: list[str] = []
    for x in _ticks(xmin, xmax, _nice_step(xmax - xmin)):
        parts.append(f'<line x1="{sx(x):.2f}" y1="{sy(ymin):.2f}" x2="{sx(x):.2f}" y2="{sy(ymax):.2f}" stroke="#eaeef2"/>')
    for y in _ticks(ymin, ymax, _nice_step(ymax - ymin)):
        parts.append(f'<line x1="{sx(xmin):.2f}" y1="{sy(y):.2f}" x2="{sx(xmax):.2f}" y2="{sy(y):.2f}" stroke="#eaeef2"/>')
    return "".join(parts)


def _axes(
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    sx: Callable[[float], float],
    sy: Callable[[float], float],
) -> str:
    parts: list[str] = []
    # 坐标轴：过原点则画在原点，否则贴边，保证始终有参照系
    x_axis_y = 0.0 if ymin <= 0 <= ymax else ymin
    y_axis_x = 0.0 if xmin <= 0 <= xmax else xmin
    parts.append(
        f'<line x1="{sx(xmin):.2f}" y1="{sy(x_axis_y):.2f}" x2="{sx(xmax):.2f}" y2="{sy(x_axis_y):.2f}" stroke="#57606a"/>'
    )
    parts.append(
        f'<line x1="{sx(y_axis_x):.2f}" y1="{sy(ymin):.2f}" x2="{sx(y_axis_x):.2f}" y2="{sy(ymax):.2f}" stroke="#57606a"/>'
    )
    # x 轴刻度数字（画在轴下方）
    for x in _ticks(xmin, xmax, _nice_step(xmax - xmin)):
        parts.append(
            f'<text x="{sx(x):.2f}" y="{sy(x_axis_y) + 14:.2f}" text-anchor="middle" font-size="10" fill="#57606a">{html.escape(_fmt_num(x))}</text>'
        )
    # y 轴刻度数字（画在轴左侧）
    for y in _ticks(ymin, ymax, _nice_step(ymax - ymin)):
        parts.append(
            f'<text x="{sx(y_axis_x) - 6:.2f}" y="{sy(y) + 3:.2f}" text-anchor="end" font-size="10" fill="#57606a">{html.escape(_fmt_num(y))}</text>'
        )
    return "".join(parts)


def _labels(plot: FunctionPlot, sx: Callable[[float], float], sy: Callable[[float], float]) -> str:
    parts: list[str] = []
    if plot.axes.xlabel:
        parts.append(
            f'<text x="{(_WIDTH / 2):.2f}" y="{_HEIGHT - 10:.2f}" text-anchor="middle" font-size="12" fill="#1f2328">{html.escape(plot.axes.xlabel)}</text>'
        )
    if plot.axes.ylabel:
        parts.append(
            f'<text x="16" y="{(_HEIGHT / 2):.2f}" text-anchor="middle" font-size="12" fill="#1f2328" transform="rotate(-90 16 {_HEIGHT / 2:.2f})">{html.escape(plot.axes.ylabel)}</text>'
        )
    return "".join(parts)


def render_svg(plot: FunctionPlot) -> StaticRenderResult:
    """把已解析的 FunctionPlot 渲染为内嵌 SVG。"""
    warnings: list[str] = []
    xmin, xmax = plot.domain
    if xmin >= xmax:
        warnings.append("domain 无效，回退到 [-10, 10]")
        xmin, xmax = -10.0, 10.0

    # 重新解析并编译表达式（parse_source 已校验，这里异常只在模型被绕过时触发）
    fns: list[tuple[object, object]] = []
    for expr in plot.expressions:
        try:
            tree = parse_expression(expr.expression)
        except PlotParseError as exc:
            warnings.append(f"表达式无法渲染，已跳过：{expr.expression}（{exc.diagnostic.message}）")
            continue
        fns.append((expr, tree))

    ymin, ymax = _compute_range(plot, fns, xmin, xmax)
    if plot.range is not None and plot.range[0] >= plot.range[1]:
        warnings.append("range 无效，改用自动范围")
        ymin, ymax = _compute_range(plot, fns, xmin, xmax)

    def sx(x: float) -> float:
        return _MARGIN + (x - xmin) / (xmax - xmin) * (_WIDTH - 2 * _MARGIN)

    def sy(y: float) -> float:
        return _HEIGHT - _MARGIN - (y - ymin) / (ymax - ymin) * (_HEIGHT - 2 * _MARGIN)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {_HEIGHT}" role="img">'
    ]
    if plot.axes.grid:
        parts.append(_grid(xmin, xmax, ymin, ymax, sx, sy))
    parts.append(_axes(xmin, xmax, ymin, ymax, sx, sy))
    for i, (expr, tree) in enumerate(fns):
        color = _safe_color(expr.color, _PALETTE[i % len(_PALETTE)])
        parts.append(_polyline(tree, xmin, xmax, sx, sy, color))
    parts.append(_labels(plot, sx, sy))
    parts.append("</svg>")

    return StaticRenderResult(
        content="".join(parts),
        width=_WIDTH,
        height=_HEIGHT,
        warnings=warnings,
    )
