"""导出器共享工具：URL 协议校验、函数图像预算与占位 warning 文案。

html / pdf / docx 三个导出器共用同一套安全规则与函数图像资源预算，避免各写一份
导致行为漂移。
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

# 链接/图片地址允许的协议；无 scheme 的相对地址视为安全，其余协议一律降级
ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})

MERMAID_WARNING = "mermaid 需前端渲染，已保留为占位代码块"
RAW_HTML_WARNING = "原始 HTML 已按纯文本转义保留"
# DOCX 暂不支持静态渲染函数图像，统一回退源码占位
PLOT_PLACEHOLDER_WARNING = "函数图像：该格式暂不支持静态渲染，已保留为源码占位"

# 单篇文档允许的函数图像数量上限，超出部分回退占位，防止多图块并发采样耗尽内存/线程
MAX_FUNCTION_PLOTS = 16
# 单篇文档允许的函数图像累计 AST 节点预算，超出部分回退占位，防止组合复杂度（多图块
# × 多表达式 × 深表达式）在采样求值时长时间占满 CPU
MAX_TOTAL_PLOT_NODES = 8000


class FunctionPlotBudget:
    """函数图像文档级资源预算：数量上限 + 累计 AST 节点上限。

    HTML 与 PDF 导出器在渲染每个 function-plot 图块前先问预算，超限即回退源码占位，
    不解析不采样，避免多图块组合复杂度耗尽内存/CPU。
    """

    def __init__(self, max_plots: int | None = None, max_total_nodes: int | None = None) -> None:
        # 默认读模块常量（便于测试 monkeypatch 常量后重新生效）
        self.max_plots = MAX_FUNCTION_PLOTS if max_plots is None else max_plots
        self.max_total_nodes = MAX_TOTAL_PLOT_NODES if max_total_nodes is None else max_total_nodes
        self.count = 0
        self.total_nodes = 0

    def check_count(self) -> str | None:
        """图块数量 +1；超限返回 warning 文案，否则返回 None。"""
        self.count += 1
        if self.count > self.max_plots:
            return f"函数图像：文档内函数图像数量超过上限 {self.max_plots}，已回退为源码占位"
        return None

    def check_nodes(self, node_count: int) -> str | None:
        """累计节点预算校验；超限返回 warning 文案（不累加），否则累加并返回 None。"""
        if self.total_nodes + node_count > self.max_total_nodes:
            return f"函数图像：文档内函数图像累计复杂度超过上限 {self.max_total_nodes} 节点，已回退为源码占位"
        self.total_nodes += node_count
        return None


def format_plot_diagnostic(diag) -> str:
    """把解析诊断格式化为面向用户的 warning 文案。"""
    loc = f"（第 {diag.line} 行）" if diag.line else ""
    return f"函数图像：{diag.message}{loc}"


def safe_url(url: str) -> str | None:
    """校验 URL 协议；安全返回原串，不安全返回 None。"""
    url = url.strip()
    if not url:
        return None
    scheme = urlparse(url).scheme.lower()
    if scheme and scheme not in ALLOWED_URL_SCHEMES:
        return None
    return url


def format_meta_value(value: object) -> str:
    """把元数据值转成可读文本：datetime 转 ISO、列表用逗号连接。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)
