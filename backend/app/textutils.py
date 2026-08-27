"""文本分词工具，供 FTS5 与轻量 Embedding 共用。

FTS5 默认 unicode61 分词器不切分中文（连续汉字算一个 token），导致「死锁」这类
子串无法命中。这里把文本统一拆成 ASCII 单词 + CJK 单字 + CJK 相邻双字，写入与查询
走同一套拆分，实现中文子串/词级召回。
"""

import re

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(r"[一-鿿]+")


def tokens(text: str) -> list[str]:
    """返回文本的检索 token 序列（含重复）。"""
    out: list[str] = []
    out.extend(_WORD_RE.findall(text.lower()))
    for run in _CJK_RUN_RE.findall(text):
        out.extend(run)  # 单字
        out.extend(run[i : i + 2] for i in range(len(run) - 1))  # 相邻双字
    return out


def unique_tokens(text: str) -> list[str]:
    """去重但保序的 token 列表，用于压缩查询/索引体积。"""
    seen: set[str] = set()
    result: list[str] = []
    for tok in tokens(text):
        if tok not in seen:
            seen.add(tok)
            result.append(tok)
    return result


def segment(text: str) -> str:
    """把文本转成空格分隔的 token，写入 FTS5 的 content 列。"""
    return " ".join(unique_tokens(text))


def match_query(query: str) -> str | None:
    """把用户查询转成 FTS5 MATCH 表达式（OR 连接、按 token 精确匹配）。"""
    toks = unique_tokens(query)
    if not toks:
        return None
    return " OR ".join(f'"{tok}"' for tok in toks)


def count_tokens(text: str) -> int:
    """粗略 token 数：ASCII 单词 + CJK 单字。仅用于展示，不做精确计量。"""
    return len(_WORD_RE.findall(text.lower())) + sum(len(run) for run in _CJK_RUN_RE.findall(text))


def make_snippet(content: str, query: str, max_len: int = 160) -> str:
    """从原文生成命中片段：优先定位较长的 query token，向前后扩展窗口。"""
    toks = unique_tokens(query)
    lowered = content.lower()
    best = -1
    # 优先用较长的 token（双字/单词）定位，命中最准确
    for tok in toks:
        if len(tok) >= 2:
            idx = lowered.find(tok)
            if idx != -1:
                best = idx
                break
    if best == -1:
        for tok in toks:
            idx = lowered.find(tok)
            if idx != -1:
                best = idx
                break

    if best == -1:
        snippet = content
    else:
        start = max(0, best - max_len // 3)
        end = min(len(content), best + max_len)
        snippet = content[start:end]
        snippet = ("…" if start > 0 else "") + snippet + ("…" if end < len(content) else "")

    if len(snippet) > max_len + 20:
        snippet = snippet[:max_len] + "…"
    return snippet
