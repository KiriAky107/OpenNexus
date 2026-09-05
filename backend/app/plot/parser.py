"""Function Plot 表达式解析：白名单数学语法，绝不执行 eval / 函数构造器 / 属性访问。

安全模型：先用 ``ast.parse(mode='eval')`` 把表达式变成纯 AST（这一步不执行任何代码），
再逐节点白名单校验（只允许数字、变量 ``x``、常量 ``pi/e``、白名单函数调用与四则/幂
运算），最后用递归解释器直接计算数值——全程不 ``compile``/``exec`` 字符串。
"""

from __future__ import annotations

import ast
import math
import re
from typing import NoReturn

from app.plot.model import (
    FunctionPlot,
    FunctionPlotExpression,
    FunctionPlotParseResult,
    PlotAxes,
    PlotDiagnostic,
)

# 白名单函数（ln 是 log 的别名）；abs 用内置函数，其余映射到 math
_FUNCTION_IMPL: dict[str, object] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "ln": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "sqrt": math.sqrt,
    "abs": abs,
}
_FUNCTIONS = frozenset(_FUNCTION_IMPL)
_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)
_DIRECTIVE_KEYS = frozenset({"domain", "range", "xlabel", "ylabel", "grid"})
_NUMBER_RE = re.compile(r"^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")

# 表达式复杂度上限：深层嵌套或海量节点在递归校验/求值时会触发 RecursionError，
# 用白名单校验提前拦截，保证失败走正常诊断路径而不是异常逃逸出导出链路。
_MAX_AST_DEPTH = 200
_MAX_AST_NODES = 1000
# 单块 function-plot 允许的表达式数量上限，防止海量表达式导致超大 SVG 与海量采样求值
_MAX_EXPRESSIONS = 16


class PlotParseError(Exception):
    """表达式解析/校验失败，携带可定位诊断。"""

    def __init__(self, diagnostic: PlotDiagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def _unsafe(message: str) -> NoReturn:
    raise PlotParseError(
        PlotDiagnostic(severity="error", code="FUNCTION_PLOT_EXPRESSION_UNSAFE", message=message)
    )


def _is_number(tok: str) -> bool:
    return bool(_NUMBER_RE.match(tok))


def _tokenize(s: str) -> list[str]:
    """把预处理后的表达式切成数字/标识符/运算符/括号 token。"""
    tokens: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or ch == ".":
            j = i
            while j < n and (s[j].isdigit() or s[j] == "."):
                j += 1
            # 科学计数法：数字后紧跟 e/E[+-]数字 视为同一数字
            if j < n and s[j] in "eE":
                k = j + 1
                if k < n and s[k] in "+-":
                    k += 1
                if k < n and s[k].isdigit():
                    while k < n and s[k].isdigit():
                        k += 1
                    j = k
            tokens.append(s[i:j])
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            tokens.append(s[i:j])
            i = j
            continue
        if ch == "*" and i + 1 < n and s[i + 1] == "*":
            tokens.append("**")
            i += 2
            continue
        tokens.append(ch)
        i += 1
    return tokens


def _is_value_end(tok: str) -> bool:
    """该 token 之后允许补乘号（数字/右括号/变量 x/常量）。"""
    return tok == ")" or _is_number(tok) or tok == "x" or tok in _CONSTANTS


def _is_value_start(tok: str) -> bool:
    """该 token 可作为乘号右侧起点（左括号/数字/任意标识符，含函数名）。"""
    return tok == "(" or _is_number(tok) or (tok and (tok[0].isalpha() or tok[0] == "_"))


def _insert_implicit_multiplication(s: str) -> str:
    """补隐式乘法：2x、2(x+1)、(x+1)(x-1)、x sin(x) 等；函数名后的 ``(`` 是调用不补。"""
    tokens = _tokenize(s)
    out: list[str] = []
    prev: str | None = None
    for tok in tokens:
        if prev is not None and _is_value_end(prev) and _is_value_start(tok):
            out.append("*")
        out.append(tok)
        prev = tok
    return "".join(out)


def _preprocess(expr: str) -> str:
    """``^`` 视为幂，补隐式乘法后再交给 ast.parse。"""
    return _insert_implicit_multiplication(expr.replace("^", "**"))


def _check_node(node: ast.AST, depth: int = 0, counter: list[int] | None = None) -> None:
    """白名单校验：任何越界节点都抛 FUNCTION_PLOT_EXPRESSION_UNSAFE。

    同时限制 AST 深度与节点总数，避免超长/超深表达式在递归校验或求值时触发
    RecursionError 而绕过解析失败路径。
    """
    if counter is None:
        counter = [0]
    if depth > _MAX_AST_DEPTH:
        _unsafe(f"表达式嵌套过深（超过 {_MAX_AST_DEPTH} 层）")
    counter[0] += 1
    if counter[0] > _MAX_AST_NODES:
        _unsafe(f"表达式过于复杂（节点数超过 {_MAX_AST_NODES}）")
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            _unsafe(f"不支持的常量 {node.value!r}")
        return
    if isinstance(node, ast.Name):
        if node.id == "x" or node.id in _CONSTANTS:
            return
        _unsafe(f"未知标识符 {node.id!r}")
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            _unsafe(f"不支持的运算符 {type(node.op).__name__}")
        _check_node(node.left, depth + 1, counter)
        _check_node(node.right, depth + 1, counter)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARY):
            _unsafe(f"不支持的运算符 {type(node.op).__name__}")
        _check_node(node.operand, depth + 1, counter)
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            _unsafe(f"不支持的函数调用 {ast.dump(node.func)!r}")
        if node.keywords:
            _unsafe("函数调用不支持关键字参数")
        # 白名单内所有函数均恰取 1 个参数，提前校验避免求值期 TypeError
        if len(node.args) != 1:
            _unsafe(f"{node.func.id} 需要 1 个参数，实际 {len(node.args)} 个")
        for arg in node.args:
            _check_node(arg, depth + 1, counter)
        return
    _unsafe(f"不支持的语法 {type(node).__name__}")


def parse_expression(expr: str) -> ast.Expression:
    """把数学表达式解析为已通过白名单校验的 AST（可直接交给 evaluate）。"""
    preprocessed = _preprocess(expr)
    try:
        tree = ast.parse(preprocessed, mode="eval")
    except SyntaxError as exc:
        raise PlotParseError(
            PlotDiagnostic(
                severity="error",
                code="FUNCTION_PLOT_PARSE_FAILED",
                message=f"表达式语法错误：{exc.msg}",
            )
        ) from exc
    except RecursionError as exc:
        # 极深嵌套可能在 ast.parse 阶段就触发 RecursionError，转为可定位诊断
        raise PlotParseError(
            PlotDiagnostic(
                severity="error",
                code="FUNCTION_PLOT_PARSE_FAILED",
                message="表达式嵌套过深，无法解析",
            )
        ) from exc
    _check_node(tree.body)
    return tree


def evaluate(expr_ast: ast.Expression, x: float) -> float:
    """递归解释已校验 AST 得到数值，全程不编译/执行代码。"""
    return _eval_node(expr_ast.body, x)


def _eval_node(node: ast.AST, x: float) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return x if node.id == "x" else _CONSTANTS[node.id]
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, x)
        right = _eval_node(node.right, x)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        # 负数底 + 非整数指数会得到复数，数学绘图不支持，抛 ValueError 让采样点作为断点处理
        if left < 0 and not right.is_integer():
            raise ValueError("negative base with fractional exponent")
        return left**right
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand, x)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Call):
        args = [_eval_node(arg, x) for arg in node.args]
        return _FUNCTION_IMPL[node.func.id](*args)  # type: ignore[operator]
    raise ValueError("unreachable node")


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _parse_pair(value: str) -> tuple[float, float]:
    """解析 ``min, max`` / ``min max`` 数值对。"""
    parts = [p for p in re.split(r"[,，\s]+", value.strip()) if p]
    if len(parts) != 2:
        raise ValueError("需要两个数值")
    return float(parts[0]), float(parts[1])


def _parse_directive(line: str) -> tuple[str, str] | None:
    """指令行形如 ``key: value``（表达式不含冒号，冒号是可靠判别）。"""
    if ":" not in line or "=" in line:
        return None
    key, _, value = line.partition(":")
    key = key.strip().lower()
    if not key or " " in key:
        return None
    return key, value.strip()


def parse_source(source: str) -> FunctionPlotParseResult:
    """把 function-plot fenced block 源码解析为 FunctionPlot + 诊断。"""
    diagnostics: list[PlotDiagnostic] = []
    expressions: list[FunctionPlotExpression] = []
    domain: tuple[float, float] = (-10.0, 10.0)
    range_: tuple[float, float] | None = None
    xlabel: str | None = None
    ylabel: str | None = None
    grid: bool = True
    has_error = False

    for lineno, raw_line in enumerate(source.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        directive = _parse_directive(line)
        if directive is not None:
            key, value = directive
            if key == "domain":
                try:
                    domain = _parse_pair(value)
                except ValueError:
                    diagnostics.append(
                        PlotDiagnostic(
                            severity="warning",
                            code="FUNCTION_PLOT_PARSE_FAILED",
                            message=f"domain 需要两个数值，已忽略：{value!r}",
                            line=lineno,
                        )
                    )
            elif key == "range":
                try:
                    range_ = _parse_pair(value)
                except ValueError:
                    diagnostics.append(
                        PlotDiagnostic(
                            severity="warning",
                            code="FUNCTION_PLOT_PARSE_FAILED",
                            message=f"range 需要两个数值，已忽略：{value!r}",
                            line=lineno,
                        )
                    )
            elif key == "xlabel":
                xlabel = value or None
            elif key == "ylabel":
                ylabel = value or None
            elif key == "grid":
                grid = value.lower() in ("true", "1", "yes", "on")
            else:
                diagnostics.append(
                    PlotDiagnostic(
                        severity="warning",
                        code="FUNCTION_PLOT_PARSE_FAILED",
                        message=f"未知指令 {key!r} 已忽略",
                        line=lineno,
                    )
                )
            continue

        # 表达式行：y = <expr> 或裸 <expr>
        expr_text = _strip_comment(line)
        if not expr_text:
            continue
        if "=" in expr_text:
            lhs, _, rhs = expr_text.partition("=")
            if lhs.strip().lower() not in ("y", ""):
                diagnostics.append(
                    PlotDiagnostic(
                        severity="error",
                        code="FUNCTION_PLOT_PARSE_FAILED",
                        message="表达式应形如 'y = <expr>'",
                        line=lineno,
                    )
                )
                has_error = True
                continue
            expr_text = rhs.strip()
        if not expr_text:
            diagnostics.append(
                PlotDiagnostic(
                    severity="error",
                    code="FUNCTION_PLOT_PARSE_FAILED",
                    message="表达式为空",
                    line=lineno,
                )
            )
            has_error = True
            continue

        try:
            parse_expression(expr_text)
        except PlotParseError as exc:
            exc.diagnostic.line = lineno
            diagnostics.append(exc.diagnostic)
            has_error = True
            continue
        expressions.append(FunctionPlotExpression(expression=expr_text))
        # 表达式数量超限：整块回退并提前终止，避免对海量表达式做采样求值
        if len(expressions) > _MAX_EXPRESSIONS:
            diagnostics.append(
                PlotDiagnostic(
                    severity="error",
                    code="FUNCTION_PLOT_TOO_MANY_EXPRESSIONS",
                    message=f"表达式数量超过上限 {_MAX_EXPRESSIONS}，已回退为源码占位",
                )
            )
            return FunctionPlotParseResult(plot=None, diagnostics=diagnostics)

    if has_error:
        return FunctionPlotParseResult(plot=None, diagnostics=diagnostics)
    if not expressions:
        diagnostics.append(
            PlotDiagnostic(
                severity="error",
                code="FUNCTION_PLOT_PARSE_FAILED",
                message="没有找到任何函数表达式",
            )
        )
        return FunctionPlotParseResult(plot=None, diagnostics=diagnostics)

    plot = FunctionPlot(
        expressions=expressions,
        domain=domain,
        range=range_,
        axes=PlotAxes(xlabel=xlabel, ylabel=ylabel, grid=grid),
    )
    return FunctionPlotParseResult(plot=plot, diagnostics=diagnostics)
