"""Function Plot → 静态 SVG 渲染 + 共享几何计算。

只输出纯几何与 <text> 的 SVG（无 script/foreignObject/内联事件），可安全内嵌 HTML。
所有文本与颜色都经过转义/校验，不把用户输入直接拼进标记。

几何计算（范围解析、采样、刻度、非有限点分段）统一收敛到 ``compute_geometry``，
返回像素坐标的 ``PlotGeometry``；``render_svg`` 只做 SVG 序列化，reportlab 后端
（``render_reportlab.py``）消费同一份几何，保证 PDF 与 SVG 视觉一致。
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass

from app.plot.model import FunctionPlot, StaticRenderResult
from app.plot.math_label import expression_latex, render_math_svg
from app.plot.parser import PlotParseError, evaluate, parse_expression

_WIDTH = 640
_HEIGHT = 480
_MARGIN = 52  # 四周留白，放轴刻度与标签
_SAMPLES = 400
_PALETTE = ["#0969da", "#d1242f", "#1a7f37", "#8250df", "#bf8700", "#e36209"]
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
# 绘图矩形（像素，SVG y-down）：曲线与坐标轴所在区域，坐标轴/网格均在此范围内
_PLOT_X0 = _MARGIN
_PLOT_Y0 = _MARGIN
_PLOT_X1 = _WIDTH - _MARGIN
_PLOT_Y1 = _HEIGHT - _MARGIN


def _safe_color(color: str | None, fallback: str) -> str:
    return color.strip() if color and _COLOR_RE.match(color.strip()) else fallback


def _valid_span(lo: float, hi: float) -> bool:
    """范围跨度有效：端点有限、跨度有限且大于零。

    端点相减可能溢出为 ``inf``（如 ``-1e308`` 到 ``1e308``），需单独校验跨度，
    否则后续坐标换算会生成含 ``nan`` 的 SVG。
    """
    span = hi - lo
    return math.isfinite(lo) and math.isfinite(hi) and math.isfinite(span) and span > 0


def _fmt_num(v: float) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1e6 or abs(v) < 1e-6:
        return f"{v:.2e}"
    return f"{v:.6g}"


def _nice_step(span: float, target_ticks: int = 6) -> float:
    raw = abs(span) / target_ticks
    if not math.isfinite(raw) or raw <= 0:
        return 1.0  # 兜底步长，避免 span 为 0/inf 时产生非法刻度
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _ticks(lo: float, hi: float, step: float) -> list[float]:
    # 防御：非法步长直接返回空，避免除零
    if not math.isfinite(step) or step <= 0:
        return []
    first = math.ceil(lo / step) * step
    values: list[float] = []
    v = first
    # 有上限的整数索引推进 + 步长推进校验，防止浮点精度导致 v+step==v 的死循环
    for _ in range(1000):
        if v > hi + step * 1e-9:
            break
        values.append(v)
        nxt = v + step
        if nxt <= v:
            break  # 步长小于当前数值的浮点精度，已无法推进
        v = nxt
    return values


def _compute_range(
    fns: list[tuple[object, object]],
    xmin: float,
    xmax: float,
) -> tuple[float, float]:
    """采样确定 y 范围；取有限样本的 min/max 加 5% 余量。"""
    ys: list[float] = []
    for _expr, tree in fns:
        for i in range(_SAMPLES + 1):
            x = xmin + (xmax - xmin) * i / _SAMPLES
            try:
                y = evaluate(tree, x)  # type: ignore[arg-type]
            except (ValueError, ZeroDivisionError, OverflowError, TypeError):
                continue
            # 复数等非实数结果直接跳过，不参与范围统计
            if isinstance(y, (int, float)) and math.isfinite(y):
                ys.append(y)

    if not ys:
        return -10.0, 10.0
    lo, hi = min(ys), max(ys)
    if lo == hi:
        lo -= 1.0
        hi += 1.0
    pad = (hi - lo) * 0.05
    return lo - pad, hi + pad


def _sx(x: float, xmin: float, xmax: float) -> float:
    """数据 x → 像素 x（SVG y-down 约定，原点左上）。"""
    return _MARGIN + (x - xmin) / (xmax - xmin) * (_WIDTH - 2 * _MARGIN)


def _sy(y: float, ymin: float, ymax: float) -> float:
    """数据 y → 像素 y（SVG y-down 约定，原点左上）。"""
    return _HEIGHT - _MARGIN - (y - ymin) / (ymax - ymin) * (_HEIGHT - 2 * _MARGIN)


@dataclass
class PlotGeometry:
    """已解析的几何：范围、轴位置、刻度、曲线像素点段、标签与 warnings。

    像素坐标统一为 SVG y-down 约定；reportlab 后端（y-up）自行翻转 y。
    """

    width: int
    height: int
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    x_axis_y: float  # 数据空间里 x 轴所在 y（过原点则 0，否则贴边）
    y_axis_x: float  # 数据空间里 y 轴所在 x（过原点则 0，否则贴边）
    xticks: list[float]
    yticks: list[float]
    polylines: list[list[list[tuple[float, float]]]]  # 按表达式分组：段 → 像素点
    colors: list[str]  # 与 polylines 对齐
    xlabel: str | None
    ylabel: str | None
    grid: bool
    warnings: list[str]


def _clip_segment(
    p0: tuple[float, float],
    p1: tuple[float, float],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Liang-Barsky：把线段裁剪到轴对齐矩形 [x0,x1]×[y0,y1]，完全在外返回 None。"""
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    p = (-dx, dx, -dy, dy)
    q = (p0[0] - x0, x1 - p0[0], p0[1] - y0, y1 - p0[1])
    u1, u2 = 0.0, 1.0
    for pk, qk in zip(p, q):
        if pk == 0:
            if qk < 0:
                return None
        else:
            r = qk / pk
            if pk < 0:
                if r > u2:
                    return None
                if r > u1:
                    u1 = r
            else:
                if r < u1:
                    return None
                if r < u2:
                    u2 = r
    if u1 > u2:
        return None
    return (p0[0] + u1 * dx, p0[1] + u1 * dy), (p0[0] + u2 * dx, p0[1] + u2 * dy)


def _points_close(
    a: tuple[float, float], b: tuple[float, float], eps: float = 1e-9
) -> bool:
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def _clip_polyline(
    points: list[tuple[float, float]],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> list[list[tuple[float, float]]]:
    """把折线裁剪到矩形，返回若干连续子段；相邻点不衔接处自动断段。"""
    if not points:
        return []
    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for i in range(len(points) - 1):
        clipped = _clip_segment(points[i], points[i + 1], x0, y0, x1, y1)
        if clipped is None:
            if current:
                segments.append(current)
                current = []
            continue
        a, b = clipped
        # 共享点被裁剪修改（折线短暂越界后折返）时，a 与上一段末点不衔接，需断段
        if current and not _points_close(a, current[-1]):
            segments.append(current)
            current = []
        if not current:
            current.append(a)
        current.append(b)
    if current:
        segments.append(current)
    return segments


_REFINE_MAX_DEPTH = 24
_REFINE_MAX_EVALUATIONS = 256
_CURVE_MAX_REFINEMENT_EVALUATIONS = 8192


def _refine_crossing(tree, left, right, ymin, ymax, budget=None):
    """Adaptively check both halves of a crossing; None explicitly breaks a path.

    A visible midpoint is not a continuity proof. Accept a visible chord only
    when its midpoint error is within a quarter pixel; otherwise subdivide both
    halves. Depth, evaluation and floating-point limits always break unresolved
    intervals instead of joining them. Entirely off-screen triples can be culled.
    """
    remaining = _REFINE_MAX_EVALUATIONS
    if budget is None:
        budget = [_REFINE_MAX_EVALUATIONS]
    tolerance = (ymax - ymin) / (_PLOT_Y1 - _PLOT_Y0) / 4

    def refine(a, b, depth):
        nonlocal remaining
        x = a[0] + (b[0] - a[0]) / 2
        if depth >= _REFINE_MAX_DEPTH or remaining == 0 or budget[0] == 0 or not a[0] < x < b[0]:
            return [a, None, b]
        remaining -= 1
        budget[0] -= 1
        try:
            y = evaluate(tree, x)
        except (ValueError, ZeroDivisionError, OverflowError, TypeError):
            y = math.nan
        if not isinstance(y, (int, float)):
            y = math.nan
        mid = (x, y)
        values = (a[1], y, b[1])
        if all(math.isfinite(v) for v in values):
            if max(values) < ymin or min(values) > ymax:
                return [a, None, b]  # No visible chord; do not connect across it.
            error = abs(y - (a[1] / 2 + b[1] / 2))
            if any(ymin <= v <= ymax for v in values) and error <= tolerance:
                return [a, mid, b]
        # Refine either side of a nonfinite midpoint too: dropping the whole
        # interval would erase valid branches between the original samples.
        first = refine(a, mid, depth + 1)
        second = refine(mid, b, depth + 1)
        return first + second[1:]

    return refine(left, right, 0)


def _sample_segments(
    tree: object,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    warnings: list[str] | None = None,
) -> list[list[tuple[float, float]]]:
    """采样并映射为像素点段，再裁剪到绘图矩形。

    每个相邻有限采样区间都检查中点，避免端点在可见范围内的渐近线漏判。
    自适应细分受区间与整条曲线预算限制，未解析区间以断点保守处理。
    """
    segments: list[list[tuple[float, float]]] = []
    points: list[tuple[float, float]] = []
    prev_y: float | None = None
    prev_x = xmin
    budget = [_CURVE_MAX_REFINEMENT_EVALUATIONS]
    for i in range(_SAMPLES + 1):
        x = xmin + (xmax - xmin) * i / _SAMPLES
        try:
            y = evaluate(tree, x)  # type: ignore[arg-type]
        except (ValueError, ZeroDivisionError, OverflowError, TypeError):
            y = math.nan
        if not isinstance(y, (int, float)) or not math.isfinite(y):
            if points:
                segments.append(points)
                points = []
            prev_y = None
            continue
        px = _sx(x, xmin, xmax)
        py = _sy(y, ymin, ymax)
        # 映射后的坐标必须有限：显式 range 下极端 y 值可能让像素坐标溢出为 inf
        if not (math.isfinite(px) and math.isfinite(py)):
            if points:
                segments.append(points)
                points = []
            prev_y = None
            continue
        if prev_y is not None:
            refined = _refine_crossing(tree, (prev_x, prev_y), (x, y), ymin, ymax, budget)
            samples = refined[1:]  # The previous endpoint is already in points.
        else:
            samples = [(x, y)]
        for sample in samples:
            mapped = None if sample is None else (
                _sx(sample[0], xmin, xmax), _sy(sample[1], ymin, ymax)
            )
            if mapped is None or not all(math.isfinite(value) for value in mapped):
                if points:
                    segments.append(points)
                    points = []
            else:
                points.append(mapped)
        prev_y = y
        prev_x = x
    if points:
        segments.append(points)

    if budget[0] == 0 and warnings is not None:
        warning = "曲线细分达到求值上限，未解析区间已断开；请缩小 domain 后重试"
        if warning not in warnings:
            warnings.append(warning)

    # 裁剪到绘图矩形：reportlab 无 SVG viewport 那样的自动裁剪，超出显式 range 的
    # 曲线会覆盖页面其他内容，故在共享几何层统一裁剪（SVG 也一并收敛到绘图区）。
    clipped: list[list[tuple[float, float]]] = []
    for seg in segments:
        clipped.extend(_clip_polyline(seg, _PLOT_X0, _PLOT_Y0, _PLOT_X1, _PLOT_Y1))
    return clipped


def compute_geometry(plot: FunctionPlot, unlimited: bool = False) -> PlotGeometry:
    """解析并计算几何，供 SVG 与 reportlab 后端复用。"""
    warnings: list[str] = []
    xmin, xmax = plot.domain
    if not _valid_span(xmin, xmax):
        warnings.append("domain 无效，回退到 [-10, 10]")
        xmin, xmax = -10.0, 10.0

    # 重新解析并编译表达式（parse_source 已校验，这里异常只在模型被绕过时触发）
    fns: list[tuple[object, object]] = []
    for expr in plot.expressions:
        try:
            tree = parse_expression(expr.expression, unlimited=unlimited)
        except PlotParseError as exc:
            warnings.append(f"表达式无法渲染，已跳过：{expr.expression}（{exc.diagnostic.message}）")
            continue
        fns.append((expr, tree))

    # 纵轴范围：显式 range 有效则用之；无效（退化/非有限/跨度溢出）丢弃并自动采样重算
    if plot.range is not None:
        lo, hi = float(plot.range[0]), float(plot.range[1])
        if _valid_span(lo, hi):
            ymin, ymax = lo, hi
        else:
            warnings.append("range 无效，改用自动范围")
            ymin, ymax = _compute_range(fns, xmin, xmax)
    else:
        ymin, ymax = _compute_range(fns, xmin, xmax)

    # 最终防线：自动范围在极端样本下也可能溢出，坐标映射前必须保证跨度有限且大于零
    if not _valid_span(ymin, ymax):
        warnings.append("y 范围跨度无法表示，回退到 [-10, 10]")
        ymin, ymax = -10.0, 10.0

    x_axis_y = 0.0 if ymin <= 0 <= ymax else ymin
    y_axis_x = 0.0 if xmin <= 0 <= xmax else xmin
    xticks = _ticks(xmin, xmax, _nice_step(xmax - xmin))
    yticks = _ticks(ymin, ymax, _nice_step(ymax - ymin))

    polylines: list[list[list[tuple[float, float]]]] = []
    colors: list[str] = []
    for i, (expr, tree) in enumerate(fns):
        color = _safe_color(expr.color, _PALETTE[i % len(_PALETTE)])
        colors.append(color)
        polylines.append(_sample_segments(tree, xmin, xmax, ymin, ymax, warnings))

    return PlotGeometry(
        width=_WIDTH,
        height=_HEIGHT,
        xmin=xmin,
        xmax=xmax,
        ymin=ymin,
        ymax=ymax,
        x_axis_y=x_axis_y,
        y_axis_x=y_axis_x,
        xticks=xticks,
        yticks=yticks,
        polylines=polylines,
        colors=colors,
        xlabel=plot.axes.xlabel,
        ylabel=plot.axes.ylabel,
        grid=plot.axes.grid,
        warnings=warnings,
    )


# --- SVG 序列化（与 compute_geometry 共用，保证字节级稳定） ---
def _grid_svg(geo: PlotGeometry) -> str:
    sx = lambda x: _sx(x, geo.xmin, geo.xmax)
    sy = lambda y: _sy(y, geo.ymin, geo.ymax)
    parts: list[str] = []
    for x in geo.xticks:
        parts.append(
            f'<line x1="{sx(x):.2f}" y1="{sy(geo.ymin):.2f}" x2="{sx(x):.2f}" '
            f'y2="{sy(geo.ymax):.2f}" stroke="#eaeef2" class="plot-grid"/>'
        )
    for y in geo.yticks:
        parts.append(
            f'<line x1="{sx(geo.xmin):.2f}" y1="{sy(y):.2f}" x2="{sx(geo.xmax):.2f}" '
            f'y2="{sy(y):.2f}" stroke="#eaeef2" class="plot-grid"/>'
        )
    return "".join(parts)


def _axes_svg(geo: PlotGeometry) -> str:
    sx = lambda x: _sx(x, geo.xmin, geo.xmax)
    sy = lambda y: _sy(y, geo.ymin, geo.ymax)
    parts: list[str] = []
    # 坐标轴：过原点则画在原点，否则贴边，保证始终有参照系
    parts.append(
        f'<line x1="{sx(geo.xmin):.2f}" y1="{sy(geo.x_axis_y):.2f}" x2="{sx(geo.xmax):.2f}" '
        f'y2="{sy(geo.x_axis_y):.2f}" stroke="#57606a" class="plot-axis"/>'
    )
    parts.append(
        f'<line x1="{sx(geo.y_axis_x):.2f}" y1="{sy(geo.ymin):.2f}" x2="{sx(geo.y_axis_x):.2f}" '
        f'y2="{sy(geo.ymax):.2f}" stroke="#57606a" class="plot-axis"/>'
    )
    # x 轴刻度数字（画在轴下方）
    for x in geo.xticks:
        parts.append(
            f'<text x="{sx(x):.2f}" y="{sy(geo.x_axis_y) + 14:.2f}" text-anchor="middle" '
            f'font-size="10" fill="#57606a">{html.escape(_fmt_num(x))}</text>'
        )
    # y 轴刻度数字（画在轴左侧）
    for y in geo.yticks:
        parts.append(
            f'<text x="{sx(geo.y_axis_x) - 6:.2f}" y="{sy(y) + 3:.2f}" text-anchor="end" '
            f'font-size="10" fill="#57606a">{html.escape(_fmt_num(y))}</text>'
        )
    return "".join(parts)


def _polylines_svg(geo: PlotGeometry) -> str:
    parts: list[str] = []
    for index, (segments, color) in enumerate(zip(geo.polylines, geo.colors)):
        for seg in segments:
            points = " ".join(f"{px:.2f},{py:.2f}" for px, py in seg)
            parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" class="plot-curve-{index % 6}"/>')
    return "".join(parts)


def _labels_svg(geo: PlotGeometry) -> str:
    parts: list[str] = []
    if geo.xlabel:
        parts.append(
            f'<text x="{geo.width / 2:.2f}" y="{geo.height - 10:.2f}" text-anchor="middle" '
            f'font-size="12" fill="#1f2328">{html.escape(geo.xlabel)}</text>'
        )
    if geo.ylabel:
        parts.append(
            f'<text x="16" y="{geo.height / 2:.2f}" text-anchor="middle" font-size="12" '
            f'fill="#1f2328" transform="rotate(-90 16 {geo.height / 2:.2f})">'
            f'{html.escape(geo.ylabel)}</text>'
        )
    return "".join(parts)


def render_svg(plot: FunctionPlot, theme_id: str = 'light', unlimited: bool = False) -> StaticRenderResult:
    """把已解析的 FunctionPlot 渲染为内嵌 SVG。"""
    geo = compute_geometry(plot, unlimited=unlimited)
    legend_height = ((len(plot.expressions) + 1) // 2) * 24
    height = geo.height + legend_height
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {geo.width} {height}" role="img" class="function-plot-svg">'
    ]
    if geo.grid:
        parts.append(_grid_svg(geo))
    parts.append(_axes_svg(geo))
    parts.append(_polylines_svg(geo))
    parts.append(_labels_svg(geo))
    for index, expression in enumerate(plot.expressions):
        x = 24 + (index % 2) * 310
        top = geo.height + 4 + (index // 2) * 24
        if expression.label:
            label = html.escape(expression.label)
            parts.append(f'<text x="{x}" y="{top + 14}" font-size="12" fill="{geo.colors[index]}" class="plot-legend-{index % 6}">{label}</text>')
        else:
            parts.append(render_math_svg(expression_latex(expression.expression), x=x, top=top,
                                         class_name=f"plot-legend-{index % 6}", color=geo.colors[index]))
    parts.append("</svg>")

    return StaticRenderResult(
        content=theme_svg("".join(parts), theme_id),
        width=geo.width,
        height=height,
        warnings=geo.warnings,
    )


def theme_svg(svg: str, theme_id: str) -> str:
    from app.export.themes import PALETTES
    palette = PALETTES.get(theme_id, PALETTES['light'])
    for source, target in [('#eaeef2', palette[5]), ('#57606a', palette[3]), ('#1f2328', palette[2])]:
        svg = svg.replace(source, target)
    if theme_id in {'dark', 'midnight-purple'}:
        for source, target in zip(_PALETTE, ['#79c0ff','#ff9b9b','#7ee787','#d2a8ff','#f2cc60','#ffa657']):
            svg = svg.replace(source, target)
    background = '<rect width="100%" height="100%" fill="' + palette[1] + '"/>'
    if re.search(r'<rect width="100%" height="100%" fill="[^"]*"/>', svg):
        return re.sub(r'<rect width="100%" height="100%" fill="[^"]*"/>', background, svg, count=1)
    return svg.replace('role="img" class="function-plot-svg">', 'role="img" class="function-plot-svg">' + background)
