"""导出器共享工具：URL 协议校验与占位 warning 文案。

html / pdf / docx 三个导出器共用同一套安全规则，避免各写一份导致行为漂移。
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

# 链接/图片地址允许的协议；无 scheme 的相对地址视为安全，其余协议一律降级
ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})

MERMAID_WARNING = "mermaid 需前端渲染，已保留为占位代码块"
RAW_HTML_WARNING = "原始 HTML 已按纯文本转义保留"
# PDF/DOCX 暂不支持静态渲染函数图像，统一回退源码占位
PLOT_PLACEHOLDER_WARNING = "函数图像：该格式暂不支持静态渲染，已保留为源码占位"


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
