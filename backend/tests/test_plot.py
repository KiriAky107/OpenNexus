"""Function Plot 的解析与静态 SVG 渲染测试。

覆盖 parser 的白名单表达式（幂/隐式乘法/函数/常量）、拒绝项（属性访问、任意调用等）、
parse_source 指令与回退，以及 render 的 SVG 输出与 HTML 导出链路集成。
"""

from __future__ import annotations

import asyncio
import math

import pytest

from app.contracts import ExportOptions
from app.export.exporters.html import HtmlExporter
from app.export.markdown import parse_document
from app.plot.parser import PlotParseError, evaluate, parse_expression, parse_source
from app.plot.render import render_svg
from app.plot.renderer import (
    FunctionPlotStaticRenderer,
    MermaidStaticRenderer,
    StaticRenderRequest,
)


# --------------------------------------------------------------------------- #
# 表达式解析
# --------------------------------------------------------------------------- #
def test_parse_expression_power_and_implicit_multiplication() -> None:
    assert evaluate(parse_expression("x^2"), 3) == 9.0
    assert evaluate(parse_expression("2^3"), 0) == 8.0
    assert evaluate(parse_expression("2x+1"), 3) == 7.0
    assert evaluate(parse_expression("2(x+1)"), 3) == 8.0
    assert evaluate(parse_expression("(x+1)(x-1)"), 3) == 8.0


def test_parse_expression_functions_and_constants() -> None:
    assert evaluate(parse_expression("sin(0)"), 0) == 0.0
    assert math.isclose(evaluate(parse_expression("sin(pi/2)"), 0), 1.0)
    assert math.isclose(evaluate(parse_expression("ln(e)"), 0), 1.0)
    assert evaluate(parse_expression("abs(-3)"), 0) == 3.0


def test_parse_expression_rejects_unsafe() -> None:
    unsafe = [
        "os.system('x')",
        "__import__('os')",
        "foo(x)",
        "eval('x')",
        "x[0]",
        "x.attr",
        "lambda: 1",
    ]
    for expr in unsafe:
        with pytest.raises(PlotParseError) as exc:
            parse_expression(expr)
        assert exc.value.diagnostic.code == "FUNCTION_PLOT_EXPRESSION_UNSAFE", expr


def test_parse_expression_syntax_error() -> None:
    with pytest.raises(PlotParseError) as exc:
        parse_expression("x +")
    assert exc.value.diagnostic.code == "FUNCTION_PLOT_PARSE_FAILED"


# --------------------------------------------------------------------------- #
# fenced 源码解析
# --------------------------------------------------------------------------- #
def test_parse_source_directives() -> None:
    result = parse_source("domain: 0, 10\nrange: -1, 1\nxlabel: x\ngrid: false\ny = x^2")
    assert result.plot is not None
    assert result.plot.domain == (0.0, 10.0)
    assert result.plot.range == (-1.0, 1.0)
    assert result.plot.axes.xlabel == "x"
    assert result.plot.axes.grid is False
    assert len(result.plot.expressions) == 1
    assert result.plot.expressions[0].expression == "x^2"


def test_parse_source_bare_and_multi_expression() -> None:
    result = parse_source("x^2\nsin(x)")
    assert result.plot is not None
    assert [e.expression for e in result.plot.expressions] == ["x^2", "sin(x)"]


def test_parse_source_unknown_directive_warns() -> None:
    result = parse_source("foo: bar\ny = x")
    assert result.plot is not None  # 未知指令仅 warning，不阻断
    assert any(d.severity == "warning" for d in result.diagnostics)


def test_parse_source_error_returns_no_plot() -> None:
    result = parse_source("y = os.system('x')")
    assert result.plot is None
    assert any(d.severity == "error" for d in result.diagnostics)


# --------------------------------------------------------------------------- #
# SVG 渲染
# --------------------------------------------------------------------------- #
def test_render_svg_contains_polyline_and_axes() -> None:
    plot = parse_source("y = x^2").plot
    rendered = render_svg(plot)
    svg = rendered.content
    assert "<svg" in svg
    assert "<polyline" in svg
    assert "<line" in svg  # 坐标轴/网格
    assert "<script" not in svg
    assert rendered.width == 640
    assert rendered.height == 504  # Includes the legend row.


def test_render_svg_multiple_functions() -> None:
    plot = parse_source("y = x^2\ny = sin(x)").plot
    rendered = render_svg(plot)
    assert rendered.content.count("<polyline") >= 2


def test_render_svg_uses_latex_vector_paths_for_expression_legends() -> None:
    plot = parse_source("y = sin(x)\ny = x^2 / 5").plot
    svg = render_svg(plot).content
    assert svg.count("plot-math-label") == 2
    assert 'data-latex="y = \\sin\\left(x\\right)"' in svg
    assert 'data-latex="y = \\frac{{x}^{2}}{5}"' in svg
    assert "<path" in svg
    assert ">y = sin(x)<" not in svg


@pytest.mark.parametrize("expression", [
    "asin(x)", "acos(x)", "atan(x)", "sinh(x)", "cosh(x)", "tanh(x)",
    "exp(x)", "ln(x)", "log10(x)", "log2(x)", "sqrt(x)", "abs(x)",
])
def test_render_svg_latex_supports_every_plot_function(expression: str) -> None:
    plot = parse_source(f"domain: 0.1, 1\ny = {expression}").plot
    assert "plot-math-label" in render_svg(plot).content


def test_render_svg_labels() -> None:
    plot = parse_source("xlabel: 时间\nylabel: 数值\ny = x").plot
    rendered = render_svg(plot)
    assert "时间" in rendered.content
    assert "数值" in rendered.content


# --------------------------------------------------------------------------- #
# HTML 导出链路集成
# --------------------------------------------------------------------------- #
def test_html_exporter_embeds_function_plot_svg() -> None:
    md = "```function-plot\ny = x^2\n```"
    result = asyncio.run(HtmlExporter().export(parse_document(md), ExportOptions()))
    html = result.content.decode("utf-8")
    assert '<figure class="function-plot">' in html
    assert "<svg" in html
    assert "<polyline" in html


def test_html_exporter_function_plot_fallback_on_error() -> None:
    md = "```function-plot\ny = os.system('x')\n```"
    result = asyncio.run(HtmlExporter().export(parse_document(md), ExportOptions()))
    html = result.content.decode("utf-8")
    assert '<pre class="function-plot">' in html
    assert "<svg" not in html
    assert any("函数图像" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# 审阅回归：浮点刻度 / 求值异常 / 无效范围
# --------------------------------------------------------------------------- #
def test_render_svg_huge_domain_ticks_bounded() -> None:
    # P1：巨大 domain 下步长受浮点精度限制无法推进，刻度应有限而非死循环
    plot = parse_source("domain: 10000000000000000, 10000000000000002\nrange: -1, 1\ny = 0").plot
    rendered = render_svg(plot)
    assert "<svg" in rendered.content


def test_parse_expression_rejects_wrong_arg_count() -> None:
    # P2：sin() / sin(1, 2) 应在解析期拒绝，而非求值期 TypeError
    with pytest.raises(PlotParseError):
        parse_expression("sin()")
    with pytest.raises(PlotParseError):
        parse_expression("sin(1, 2)")


def test_render_svg_nonreal_samples_are_break_points() -> None:
    # P2：x^0.5 在负数域产生复数，应作为断点处理，正半轴仍可绘制
    plot = parse_source("domain: -4, 4\ny = x^0.5").plot
    rendered = render_svg(plot)
    assert "<polyline" in rendered.content


def test_render_svg_invalid_range_falls_back() -> None:
    # P2：退化 range（1, 1）应丢弃并自动采样，而非 ZeroDivisionError
    plot = parse_source("range: 1, 1\ny = x").plot
    rendered = render_svg(plot)
    assert "<polyline" in rendered.content
    assert any("range" in w for w in rendered.warnings)


def test_render_svg_nonfinite_range_falls_back() -> None:
    # P2：非有限 range 端点应丢弃并自动采样
    plot = parse_source("range: nan, 1\ny = x").plot
    rendered = render_svg(plot)
    assert "<polyline" in rendered.content


def test_html_exporter_function_plot_render_error_falls_back(monkeypatch) -> None:
    # P2：渲染异常不阻断整篇导出，回退占位并记 warning
    import app.plot.renderer as renderer_mod

    def boom(plot):
        raise RuntimeError("boom")

    monkeypatch.setattr(renderer_mod, "render_svg", boom)
    md = "```function-plot\ny = x\n```"
    result = asyncio.run(HtmlExporter().export(parse_document(md), ExportOptions()))
    html = result.content.decode("utf-8")
    assert '<pre class="function-plot">' in html
    assert any("渲染失败" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# 审阅回归：复杂表达式 / 极端数值范围
# --------------------------------------------------------------------------- #
def test_parse_expression_rejects_excessive_depth() -> None:
    # P2：超长加法链的 AST 深度超限，应拒绝为 PlotParseError 而非触发 RecursionError
    expr = "+".join(["1"] * 300)
    with pytest.raises(PlotParseError) as exc:
        parse_expression(expr)
    assert exc.value.diagnostic.code == "FUNCTION_PLOT_EXPRESSION_UNSAFE"


def test_parse_expression_rejects_excessive_nodes() -> None:
    # P2：浅层但节点超限的表达式（满二叉树）应被节点数上限拦截
    def balanced(depth: int) -> str:
        if depth == 0:
            return "x"
        return f"({balanced(depth - 1)}+{balanced(depth - 1)})"

    expr = balanced(10)  # ~2047 个节点，深度仅 ~10
    with pytest.raises(PlotParseError) as exc:
        parse_expression(expr)
    assert exc.value.diagnostic.code == "FUNCTION_PLOT_EXPRESSION_UNSAFE"


def test_html_exporter_function_plot_deep_expression_falls_back() -> None:
    # P2：复杂表达式解析失败应回退占位，不阻断整篇导出
    expr = "+".join(["1"] * 300)
    md = f"```function-plot\ny = {expr}\n```"
    result = asyncio.run(HtmlExporter().export(parse_document(md), ExportOptions()))
    html = result.content.decode("utf-8")
    assert '<pre class="function-plot">' in html
    assert "<svg" not in html
    assert any("函数图像" in w for w in result.warnings)


def test_render_svg_extreme_domain_no_nan() -> None:
    # P2：有限但跨度溢出的 domain 应回退安全范围，SVG 不得含 nan/inf
    plot = parse_source("domain: -1e308, 1e308\nrange: -1, 1\ny = 0").plot
    rendered = render_svg(plot)
    assert "<svg" in rendered.content
    assert "nan" not in rendered.content
    assert "inf" not in rendered.content
    assert any("domain" in w for w in rendered.warnings)


def test_render_svg_extreme_range_no_nan() -> None:
    # P2：有限但跨度溢出的 range 应回退自动范围，SVG 不得含 nan/inf
    plot = parse_source("domain: -1, 1\nrange: -1e308, 1e308\ny = x").plot
    rendered = render_svg(plot)
    assert "<svg" in rendered.content
    assert "nan" not in rendered.content
    assert "inf" not in rendered.content
    assert any("range" in w for w in rendered.warnings)


# --------------------------------------------------------------------------- #
# 审阅回归：函数/图像数量上限
# --------------------------------------------------------------------------- #
def test_parse_source_rejects_too_many_expressions() -> None:
    # P1：单块表达式数量超限应整块回退，避免海量采样求值
    source = "\n".join(f"y = x + {i}" for i in range(50))
    result = parse_source(source)
    assert result.plot is None
    assert any(d.code == "FUNCTION_PLOT_TOO_MANY_EXPRESSIONS" for d in result.diagnostics)


def test_html_exporter_limits_function_plot_count() -> None:
    # P1：文档内函数图像数量超限，超出部分回退占位，不耗尽资源
    blocks = "\n\n".join("```function-plot\ny = x\n```" for _ in range(20))
    result = asyncio.run(HtmlExporter().export(parse_document(blocks), ExportOptions()))
    html = result.content.decode("utf-8")
    # 上限 16：前 16 个渲染为 SVG，其余 4 个回退占位
    assert html.count('<figure class="function-plot">') == 16
    assert html.count('<pre class="function-plot">') == 4
    assert any("函数图像数量超过上限" in w for w in result.warnings)


def test_html_exporter_limits_total_plot_nodes(monkeypatch) -> None:
    # P1：文档级累计 AST 节点预算超限后，后续图像回退占位，防止组合复杂度耗尽 CPU
    import app.export.exporters._common as common_mod

    monkeypatch.setattr(common_mod, "MAX_TOTAL_PLOT_NODES", 5)
    # 第一个图块 y=x（1 节点）在预算内；第二个图块 y=x+x+x+x（7 节点）累计超限
    md = "```function-plot\ny = x\n```\n\n```function-plot\ny = x + x + x + x\n```"
    result = asyncio.run(HtmlExporter().export(parse_document(md), ExportOptions()))
    html = result.content.decode("utf-8")
    assert html.count('<figure class="function-plot">') == 1
    assert html.count('<pre class="function-plot">') == 1
    assert any("累计复杂度" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# StaticRenderer 内部契约（§10.4）
# --------------------------------------------------------------------------- #
def test_function_plot_static_renderer_renders_svg() -> None:
    renderer = FunctionPlotStaticRenderer()
    result = renderer.render(StaticRenderRequest(kind="function_plot", source="y = x^2"))
    assert "<svg" in result.content
    assert "<polyline" in result.content
    assert result.mime_type == "image/svg+xml"
    assert result.width == 640
    assert result.height == 504  # Includes the legend row.


def test_function_plot_static_renderer_parse_exposes_node_count() -> None:
    renderer = FunctionPlotStaticRenderer()
    parsed = renderer.parse(StaticRenderRequest(kind="function_plot", source="y = x + x"))
    assert parsed.plot is not None
    assert parsed.plot.node_count > 0


def test_function_plot_static_renderer_raises_on_no_plot() -> None:
    renderer = FunctionPlotStaticRenderer()
    request = StaticRenderRequest(kind="function_plot", source="y = os.system('x')")
    with pytest.raises(ValueError):
        renderer.render(request)


def test_mermaid_static_renderer_returns_placeholder() -> None:
    renderer = MermaidStaticRenderer()
    result = renderer.render(StaticRenderRequest(kind="mermaid", source="graph LR"))
    assert result.content == ""
    assert any("mermaid" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# 共享几何与 reportlab 后端（PDF 内嵌函数图像）
# --------------------------------------------------------------------------- #
def test_compute_geometry_shares_pixel_segments() -> None:
    from app.plot.render import compute_geometry

    plot = parse_source("y = x^2\ny = sin(x)").plot
    geo = compute_geometry(plot)
    assert geo.width == 640
    assert geo.height == 480
    assert len(geo.polylines) == 2
    assert geo.colors == ["#0969da", "#d1242f"]
    assert geo.xticks and geo.yticks
    for segments in geo.polylines:
        assert segments
        for seg in segments:
            assert seg
            for px, py in seg:
                assert math.isfinite(px) and math.isfinite(py)
                assert 0 <= px <= geo.width
                assert 0 <= py <= geo.height


def test_render_reportlab_builds_drawing() -> None:
    from reportlab.graphics.shapes import Drawing, Group, Line, PolyLine, String

    from app.plot.render_reportlab import render_drawing

    plot = parse_source("xlabel: 时间\nylabel: 数值\ny = x^2").plot
    drawing = render_drawing(plot, width=480)
    assert isinstance(drawing, Drawing)
    assert drawing.renderScale == 0.75  # 480 / 640
    kinds = {type(c).__name__ for c in drawing.contents}
    assert {"Line", "PolyLine", "String", "Group"} <= kinds
    strings = [c for c in drawing.contents if isinstance(c, String)]
    from app.export.fonts import FONT
    assert any(s.fontName == FONT for s in strings)
    assert any(s.text == "时间" for s in strings)
    # ylabel 在旋转 Group 内
    groups = [c for c in drawing.contents if isinstance(c, Group)]
    assert groups
    group_texts = [s.text for g in groups for s in g.contents if isinstance(s, String)]
    assert "数值" in group_texts


def test_render_reportlab_uses_vector_latex_for_expression_legend() -> None:
    from reportlab.graphics.shapes import Group, Path
    from app.plot.render_reportlab import render_drawing

    drawing = render_drawing(parse_source("y = x^2 / 5").plot)
    math_groups = [item for item in drawing.contents if isinstance(item, Group)
                   and any(isinstance(child, Path) for child in item.contents)]
    assert math_groups


def test_render_reportlab_curves_are_finite_and_bounded() -> None:
    from reportlab.graphics.shapes import PolyLine

    from app.plot.render_reportlab import render_drawing

    plot = parse_source("y = x").plot
    drawing = render_drawing(plot)
    polylines = [c for c in drawing.contents if isinstance(c, PolyLine)]
    assert polylines
    for pl in polylines:
        pts = pl.points  # 扁平 [x0,y0,x1,y1,...]
        for x, y in zip(pts[0::2], pts[1::2]):
            assert math.isfinite(x) and math.isfinite(y)
            assert 0 <= x <= 640
            assert 0 <= y <= 480


def test_compute_geometry_clips_curves_to_plot_rect() -> None:
    # P2：显式 range 外的曲线应裁剪到绘图矩形，避免 PDF 中曲线覆盖页面其他内容
    from app.plot.render import (
        _PLOT_X0,
        _PLOT_X1,
        _PLOT_Y0,
        _PLOT_Y1,
        compute_geometry,
    )

    plot = parse_source("range: -1, 1\ny = 10*x").plot
    geo = compute_geometry(plot)
    assert geo.polylines
    assert any(geo.polylines)  # 曲线穿越 range 后在绘图区内仍有可见段
    for segments in geo.polylines:
        for seg in segments:
            assert seg
            for px, py in seg:
                assert _PLOT_X0 <= px <= _PLOT_X1
                assert _PLOT_Y0 <= py <= _PLOT_Y1


def test_render_reportlab_ylabel_within_drawing_bounds() -> None:
    # P2：纵轴标签旋转后边界应落在 Drawing 范围内，不能甩到负 x 区域
    from reportlab.graphics.shapes import Group, String

    from app.plot.render_reportlab import render_drawing

    plot = parse_source("ylabel: 数值\ny = x").plot
    drawing = render_drawing(plot)
    groups = [c for c in drawing.contents if isinstance(c, Group)]
    ylabel_groups = [
        g
        for g in groups
        if any(isinstance(s, String) and s.text == "数值" for s in g.contents)
    ]
    assert ylabel_groups
    x0, y0, x1, y1 = ylabel_groups[0].getBounds()
    assert 0 <= x0 <= x1 <= 640
    assert 0 <= y0 <= y1 <= 480


def test_compute_geometry_breaks_at_asymptote() -> None:
    # P2：渐近点落在两个采样点之间时，两侧采样仍有限，若不断段会被 Liang-Barsky
    # 裁剪成贯穿绘图区的伪竖线；这里断言不存在跨越上下边界的伪连接线段。
    from app.plot.render import _PLOT_Y0, _PLOT_Y1, compute_geometry

    plot = parse_source("domain: -1, 1\nrange: -10, 10\ny = 1/(x-0.013)").plot
    geo = compute_geometry(plot)
    assert any(geo.polylines)  # 渐近线两侧的曲线分支仍在绘图区内可见
    full_height = _PLOT_Y1 - _PLOT_Y0
    for segments in geo.polylines:
        for seg in segments:
            # 相邻点垂直跨度若接近整个绘图区高度，即为渐近线伪连接
            for (_, py0), (_, py1) in zip(seg, seg[1:]):
                assert abs(py1 - py0) < full_height * 0.5


@pytest.mark.parametrize('slope,root', [(1000, 0.0025), (-1000, 0.0025), (1000000, 0.002731)])
def test_steep_continuous_crossing_survives_svg_and_pdf(slope, root):
    from app.plot.render import compute_geometry, _PLOT_Y0, _PLOT_Y1, _PLOT_X0, _PLOT_X1
    from app.plot.render_reportlab import render_drawing
    from reportlab.graphics.shapes import PolyLine
    plot = parse_source(f'domain: -1, 1\nrange: -1, 1\ny = {slope}*(x-{root})').plot
    segments = compute_geometry(plot).polylines[0]
    assert len(segments) == 1
    points = segments[0]
    assert min(y for x,y in points) == pytest.approx(_PLOT_Y0)
    assert max(y for x,y in points) == pytest.approx(_PLOT_Y1)
    for x,y in points:
        data_x = (x-_PLOT_X0)/(_PLOT_X1-_PLOT_X0)*2-1
        data_y = 1-(y-_PLOT_Y0)/(_PLOT_Y1-_PLOT_Y0)*2
        assert data_y == pytest.approx(slope*(data_x-root),abs=1e-7)
    assert '<polyline ' in render_svg(plot).content
    assert any(isinstance(item,PolyLine) for item in render_drawing(plot).contents)


def test_crossing_refinement_has_bounded_work(monkeypatch):
    import app.plot.render as rendering
    calls = []
    def jump(tree, x):
        calls.append(x)
        return -2 if x < 0.123456789 else 2
    monkeypatch.setattr(rendering, 'evaluate', jump)
    samples = rendering._refine_crossing(None, (0,-2), (1,2), -1,1)
    assert None in samples
    assert len(calls) <= rendering._REFINE_MAX_EVALUATIONS


def test_visible_midpoint_does_not_bridge_a_pole():
    from app.plot.render import compute_geometry, _PLOT_Y0, _PLOT_Y1, _PLOT_X0, _PLOT_X1
    plot = parse_source('domain: 0, 2\nrange: -1, 1\ny = 1000*(x-0.0025)+0.001/(x-0.001)').plot
    segments = compute_geometry(plot).polylines[0]
    assert segments
    for seg in segments:
        for px, py in seg:
            x = (px-_PLOT_X0)/(_PLOT_X1-_PLOT_X0)*2
            y = 1-(py-_PLOT_Y0)/(_PLOT_Y1-_PLOT_Y0)*2
            # On the visible branch, 1000*t + .001/t - 1.5 >= .5.
            assert x > .001
            assert y >= .5-1e-8
            assert y == pytest.approx(1000*(x-.0025)+.001/(x-.001),abs=.002)


def test_refined_extreme_samples_never_emit_nonfinite_coordinates():
    from app.plot.render import compute_geometry
    from app.plot.render_reportlab import render_drawing
    from reportlab.graphics.shapes import PolyLine
    plot = parse_source('domain: 0, 2\nrange: -1e-308, 1e-308\ny = 1e-304*(x-0.00125)-1e308*x*(x-0.005)*(x-0.00125)').plot
    geo = compute_geometry(plot)
    for segments in geo.polylines:
        for seg in segments:
            assert all(math.isfinite(v) for point in seg for v in point)
    svg = render_svg(plot).content
    assert 'nan' not in svg and 'inf' not in svg
    for shape in render_drawing(plot).contents:
        if isinstance(shape, PolyLine):
            assert all(math.isfinite(v) for v in shape.points)


def test_refinement_budget_is_shared_by_both_subtrees(monkeypatch):
    import app.plot.render as rendering
    calls = []
    def oscillate(tree, x):
        calls.append(x)
        return .9*math.sin(1e9*x)
    monkeypatch.setattr(rendering, 'evaluate', oscillate)
    samples = rendering._refine_crossing(None, (0,-2), (1,2), -1,1)
    assert len(calls) == rendering._REFINE_MAX_EVALUATIONS
    assert None in samples  # Exhaustion leaves gaps, never unchecked chords.


@pytest.mark.parametrize('factor,pole', [(0.0001,.001),(-0.0001,.001),(.001,.001),(.0001,.0025),(.0001,.00419)])
def test_visible_endpoints_do_not_hide_a_pole(factor, pole):
    from app.plot.render import compute_geometry, _PLOT_X0, _PLOT_X1
    plot = parse_source(f'domain: 0, 2\nrange: -1, 1\ny = {factor}/(x-{pole})').plot
    segments = compute_geometry(plot).polylines[0]
    assert segments
    left = right = False
    for segment in segments:
        xs = [(px-_PLOT_X0)/(_PLOT_X1-_PLOT_X0)*2 for px,py in segment]
        assert not min(xs) < pole < max(xs)
        left |= max(xs) < pole
        right |= min(xs) > pole
    assert left and right


@pytest.mark.parametrize('expression', ['x', 'x^2', 'sin(x)', 'exp(x)', 'sqrt(x)', 'log(x)'])
def test_smooth_and_domain_limited_curves_remain_visible(expression):
    from app.plot.render import compute_geometry, _PLOT_X0, _PLOT_X1, _PLOT_Y0, _PLOT_Y1
    plot = parse_source(f'domain: -2, 2\nrange: -2, 5\ny = {expression}').plot
    geometry = compute_geometry(plot)
    assert geometry.polylines[0]
    assert not geometry.warnings
    for segment in geometry.polylines[0]:
        for x,y in segment:
            assert math.isfinite(x) and math.isfinite(y)
            assert _PLOT_X0-1e-8 <= x <= _PLOT_X1+1e-8
            assert _PLOT_Y0-1e-8 <= y <= _PLOT_Y1+1e-8


def test_curve_refinement_has_one_shared_budget(monkeypatch):
    import app.plot.render as rendering
    calls=[]
    def oscillate(tree, x):
        calls.append(x)
        return .9*math.sin(1e9*x)
    monkeypatch.setattr(rendering,'evaluate',oscillate)
    warnings=[]
    rendering._sample_segments(None,0,2,-1,1,warnings)
    assert len(calls) <= rendering._SAMPLES+1+rendering._CURVE_MAX_REFINEMENT_EVALUATIONS
    assert len(warnings)==1
