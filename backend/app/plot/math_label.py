"""Safe AST-to-LaTeX conversion and vector math layout for plot labels."""

from __future__ import annotations

import ast
import html
import math
import threading
from dataclasses import dataclass
from functools import lru_cache

from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import MathTextParser
from matplotlib.path import Path as MplPath

from app.plot.parser import parse_expression

_MATH_PARSER = MathTextParser("path")
_RASTER_PARSER = MathTextParser("agg")
_MATH_LOCK = threading.Lock()


def _number(value: int | float) -> str:
    text = repr(value)
    if "e" not in text.lower():
        return text
    mantissa, exponent = text.lower().split("e", 1)
    return rf"{mantissa}\times 10^{{{int(exponent)}}}"


def _latex(node: ast.AST, parent_precedence: int = 0) -> str:
    if isinstance(node, ast.Constant):
        return _number(node.value)
    if isinstance(node, ast.Name):
        return r"\pi" if node.id == "pi" else node.id
    if isinstance(node, ast.UnaryOp):
        value = _latex(node.operand, 25)
        result = ("-" if isinstance(node.op, ast.USub) else "+") + value
        return rf"\left({result}\right)" if parent_precedence > 25 else result
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Div):
            return rf"\frac{{{_latex(node.left)}}}{{{_latex(node.right)}}}"
        if isinstance(node.op, ast.Pow):
            result = rf"{{{_latex(node.left, 30)}}}^{{{_latex(node.right)}}}"
            return rf"\left({result}\right)" if parent_precedence > 30 else result
        precedence = 20 if isinstance(node.op, ast.Mult) else 10
        operator = r" \cdot " if isinstance(node.op, ast.Mult) else (" + " if isinstance(node.op, ast.Add) else " - ")
        left = _latex(node.left, precedence)
        right = _latex(node.right, precedence + (1 if isinstance(node.op, ast.Sub) else 0))
        result = left + operator + right
        return rf"\left({result}\right)" if parent_precedence > precedence else result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        argument = _latex(node.args[0])
        name = node.func.id
        if name == "sqrt":
            return rf"\sqrt{{{argument}}}"
        if name == "abs":
            return rf"\left|{argument}\right|"
        if name in {"log10", "log2"}:
            return rf"\log_{{{name[3:]}}}\left({argument}\right)"
        if name in {"asin", "acos", "atan"}:
            return rf"\{name[1:]}^{{-1}}\left({argument}\right)"
        command = "log" if name == "ln" else name
        return rf"\{command}\left({argument}\right)"
    raise ValueError(f"Unsupported validated expression node: {type(node).__name__}")


def expression_latex(expression: str) -> str:
    """Convert one already-supported function expression to MathText-compatible LaTeX."""
    return "y = " + _latex(parse_expression(expression).body)


@dataclass(frozen=True)
class VectorPath:
    commands: tuple[tuple[str, tuple[float, ...]], ...]


@dataclass(frozen=True)
class MathLayout:
    width: float
    height: float
    depth: float
    paths: tuple[VectorPath, ...]
    rects: tuple[tuple[float, float, float, float], ...]


def _offset(values: tuple[float, ...], x: float, y: float) -> tuple[float, ...]:
    return tuple(value + (x if index % 2 == 0 else y) for index, value in enumerate(values))


@lru_cache(maxsize=256)
def math_layout(latex: str, size: float = 12.0) -> MathLayout:
    """Lay out LaTeX as reusable vector paths; calls are cached and serialized for FT2Font."""
    with _MATH_LOCK:
        parsed = _MATH_PARSER.parse(f"${latex}$", dpi=72, prop=FontProperties(size=size))
        paths: list[VectorPath] = []
        for font, font_size, _character, glyph, offset_x, offset_y in parsed.glyphs:
            font.set_size(font_size, 72)
            font.load_glyph(glyph)
            vertices, codes = font.get_path()
            commands: list[tuple[str, tuple[float, ...]]] = []
            for values, code in MplPath(vertices, codes).iter_segments(curves=True, simplify=False):
                command = {
                    MplPath.MOVETO: "M",
                    MplPath.LINETO: "L",
                    MplPath.CURVE3: "Q",
                    MplPath.CURVE4: "C",
                    MplPath.CLOSEPOLY: "Z",
                }[code]
                points = () if command == "Z" else _offset(tuple(float(value) for value in values), float(offset_x), float(offset_y))
                commands.append((command, points))
            paths.append(VectorPath(tuple(commands)))
        rects = tuple(tuple(float(value) for value in rect) for rect in parsed.rects)
        return MathLayout(float(parsed.width), float(parsed.height), float(parsed.depth), tuple(paths), rects)


def _svg_number(value: float) -> str:
    if math.isclose(value, round(value), abs_tol=1e-8):
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _svg_path(path: VectorPath) -> str:
    return " ".join(command + (" " + " ".join(_svg_number(value) for value in values) if values else "") for command, values in path.commands)


def render_math_svg(latex: str, *, x: float, top: float, class_name: str, color: str) -> str:
    """Return a script-free SVG group containing MathText vector glyphs."""
    layout = math_layout(latex)
    baseline = top + layout.height - layout.depth
    accessible = html.escape(latex, quote=True)
    parts = [
        f'<g class="{class_name} plot-math-label" fill="{color}" '
        f'transform="translate({_svg_number(x)} {_svg_number(baseline)}) scale(1 -1)" '
        f'aria-label="{accessible}" data-latex="{accessible}">'
    ]
    parts.extend(f'<path d="{_svg_path(path)}"/>' for path in layout.paths)
    for rx, ry, width, height in layout.rects:
        parts.append(
            f'<path d="M {_svg_number(rx)} {_svg_number(ry)} h {_svg_number(width)} '
            f'v {_svg_number(height)} h -{_svg_number(width)} Z"/>'
        )
    parts.append("</g>")
    return "".join(parts)


def render_math_reportlab(latex: str, *, x: float, visual_top: float, color: object):
    """Return a reportlab Group containing the same LaTeX glyph geometry as the SVG."""
    from reportlab.graphics.shapes import Group, Path, Rect

    layout = math_layout(latex)
    baseline = visual_top - (layout.height - layout.depth)
    group = Group()
    for vector in layout.paths:
        path = Path(fillColor=color, strokeColor=None)
        current = (0.0, 0.0)
        start = current
        for command, values in vector.commands:
            if command == "M":
                current = (values[0], values[1]); start = current
                path.moveTo(*current)
            elif command == "L":
                current = (values[0], values[1]); path.lineTo(*current)
            elif command == "Q":
                control, end = (values[0], values[1]), (values[2], values[3])
                first = (current[0] + 2 * (control[0] - current[0]) / 3,
                         current[1] + 2 * (control[1] - current[1]) / 3)
                second = (end[0] + 2 * (control[0] - end[0]) / 3,
                          end[1] + 2 * (control[1] - end[1]) / 3)
                path.curveTo(*first, *second, *end); current = end
            elif command == "C":
                path.curveTo(*values); current = (values[4], values[5])
            else:
                path.closePath(); current = start
        group.add(path)
    for rx, ry, width, height in layout.rects:
        group.add(Rect(rx, ry, width, height, fillColor=color, strokeColor=None))
    group.translate(x, baseline)
    return group


@lru_cache(maxsize=256)
def render_math_mask(latex: str, size: float = 12.0, dpi: float = 144.0) -> tuple[int, int, bytes]:
    """Rasterize LaTeX to an 8-bit alpha mask for DOCX/PNG export."""
    with _MATH_LOCK:
        parsed = _RASTER_PARSER.parse(f"${latex}$", dpi=dpi, prop=FontProperties(size=size))
        image = parsed.image
        height, width = image.shape
        return int(width), int(height), image.tobytes()
