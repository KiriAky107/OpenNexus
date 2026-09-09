"""Function Plot → reportlab 矢量 Drawing（供 PDF 内嵌）。

消费 ``render.compute_geometry`` 的共享几何，产出 ``reportlab.graphics.shapes.Drawing``：
网格/坐标轴用 ``Line``、曲线用 ``PolyLine``、刻度数字与轴标签用 ``String``。
reportlab 原点在左下（y-up），与 SVG 的 y-down 相反，故对几何里的像素 y 统一翻转；
轴标签（ylabel）用 ``Group.rotate`` 旋转为竖向文本。中文字体复用内置 STSong-Light，
guarded 注册避免与 pdf.py 重复注册。
"""

from __future__ import annotations

from reportlab.graphics.shapes import Drawing, Group, Line, PolyLine, String
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from app.plot.model import FunctionPlot
from app.plot.math_label import expression_latex, render_math_reportlab
from app.plot.render import PlotGeometry, _fmt_num, _sx, _sy, compute_geometry

from app.export.fonts import FONT as _FONT

_GRID_COLOR = HexColor("#eaeef2")
_AXIS_COLOR = HexColor("#57606a")
_LABEL_COLOR = HexColor("#1f2328")
_TICK_FONT_SIZE = 10
_LABEL_FONT_SIZE = 12


def _build_drawing(geo: PlotGeometry, palette=None) -> Drawing:
    """由共享几何构建矢量 Drawing（坐标翻转后仍沿用 SVG 的像素布局）。"""
    drawing = Drawing(geo.width, geo.height)
    grid_color = HexColor(palette['border']) if palette else _GRID_COLOR
    axis_color = HexColor(palette['muted']) if palette else _AXIS_COLOR
    label_color = HexColor(palette['text']) if palette else _LABEL_COLOR

    # SVG y-down → reportlab y-up：翻转像素 y
    def sx(x: float) -> float:
        return _sx(x, geo.xmin, geo.xmax)

    def sy(y: float) -> float:
        return geo.height - _sy(y, geo.ymin, geo.ymax)

    # 网格
    if geo.grid:
        for x in geo.xticks:
            drawing.add(
                Line(sx(x), sy(geo.ymin), sx(x), sy(geo.ymax), strokeColor=grid_color, strokeWidth=0.5)
            )
        for y in geo.yticks:
            drawing.add(
                Line(sx(geo.xmin), sy(y), sx(geo.xmax), sy(y), strokeColor=grid_color, strokeWidth=0.5)
            )

    # 坐标轴（过原点画在原点，否则贴边，与 SVG 一致）
    drawing.add(
        Line(sx(geo.xmin), sy(geo.x_axis_y), sx(geo.xmax), sy(geo.x_axis_y), strokeColor=axis_color, strokeWidth=0.7)
    )
    drawing.add(
        Line(sx(geo.y_axis_x), sy(geo.ymin), sx(geo.y_axis_x), sy(geo.ymax), strokeColor=axis_color, strokeWidth=0.7)
    )

    # 刻度数字（x 轴下方、y 轴左侧）
    for x in geo.xticks:
        drawing.add(
            String(
                sx(x), sy(geo.x_axis_y) - 14, _fmt_num(x),
                fontName=_FONT, fontSize=_TICK_FONT_SIZE, fillColor=axis_color, textAnchor="middle",
            )
        )
    for y in geo.yticks:
        drawing.add(
            String(
                sx(geo.y_axis_x) - 6, sy(y) - 3, _fmt_num(y),
                fontName=_FONT, fontSize=_TICK_FONT_SIZE, fillColor=axis_color, textAnchor="end",
            )
        )

    # 曲线（非有限点处已由几何断成多段）
    for segments, color in zip(geo.polylines, geo.colors):
        for seg in segments:
            flipped = [(px, geo.height - py) for px, py in seg]
            drawing.add(PolyLine(flipped, strokeColor=HexColor(color), strokeWidth=1.4))

    # 轴标签
    if geo.xlabel:
        drawing.add(
            String(
                geo.width / 2, 10, geo.xlabel,
                fontName=_FONT, fontSize=_LABEL_FONT_SIZE, fillColor=label_color, textAnchor="middle",
            )
        )
    if geo.ylabel:
        # 竖向标签：Group.rotate(90) 在 y-up 坐标下等价于 SVG 的 rotate(-90)。
        # 文本放在组内局部坐标 (0,0)，先平移后旋转得到 T·R（先绕原点旋转、再平移到
        # 目标位置），避免用绝对坐标定位又用相同坐标当旋转中心造成的重复变换，
        # 后者会把标签甩到画布之外（负 x 区域）。
        label = Group()
        label.add(
            String(
                0, 0, geo.ylabel,
                fontName=_FONT, fontSize=_LABEL_FONT_SIZE, fillColor=label_color, textAnchor="middle",
            )
        )
        label.translate(16, geo.height / 2)
        label.rotate(90)
        drawing.add(label)

    return drawing


def render_drawing(plot: FunctionPlot, width: float | None = None, palette=None, unlimited=False, max_height=None) -> Drawing:
    """把已解析的 FunctionPlot 渲染为 reportlab Drawing（可直接追加到 platypus story）。

    ``width`` 为目标输出宽度（点），用于把 640px 的几何缩放到页面内容宽；省略则按
    原始尺寸输出。缩放只影响 PDF 渲染，不改动共享几何。
    """
    geo = compute_geometry(plot, unlimited=unlimited)
    if palette:
        from reportlab.lib.colors import HexColor as color
        bg = color(palette['surface'])
        if .2126*bg.red + .7152*bg.green + .0722*bg.blue < .5:
            colors = ['#79c0ff','#ff9b9b','#7ee787','#d2a8ff','#f2cc60','#ffa657']
            geo.colors = [value if plot.expressions[i].color else colors[i % len(colors)] for i,value in enumerate(geo.colors)]
    drawing = _build_drawing(geo, palette)
    legend_height = ((len(plot.expressions)+1)//2)*24
    drawing.height += legend_height
    for index, expression in enumerate(plot.expressions):
        x = 24 + (index % 2) * 310
        visual_top = drawing.height - 4 - (index // 2) * 24
        if expression.label:
            drawing.add(String(x, visual_top - 12, expression.label, fontName=_FONT, fontSize=12,
                               fillColor=HexColor(geo.colors[index])))
        else:
            drawing.add(render_math_reportlab(expression_latex(expression.expression), x=x,
                                               visual_top=visual_top, color=HexColor(geo.colors[index])))
    if width is not None and width > 0:
        drawing.renderScale = min(1.0, width / geo.width, max_height / drawing.height if max_height else 1.0)
    return drawing
