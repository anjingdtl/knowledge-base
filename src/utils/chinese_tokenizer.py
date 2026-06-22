"""中文分词工具 — 基于 jieba"""
import re

import jieba


def tokenize_chinese(text: str) -> str:
    """用 jieba 搜索引擎模式分词，返回空格分隔的词组字符串"""
    words = jieba.cut_for_search(text)
    return " ".join(w.strip() for w in words if w.strip())


def tokenize_chinese_full(text: str) -> str:
    """jieba 全模式分词（MaxKB 风格），返回空格分隔词组。
    全模式会产出所有可能的词组组合，适合 FTS 索引。"""
    if not text.strip():
        return ""
    words = jieba.lcut(text, cut_all=True)
    return " ".join(w.strip() for w in words if w.strip())


def sanitize_fts_query(query: str, is_tokenized: bool = False) -> str:
    """清洗 FTS5 MATCH 查询字符串，避免特殊字符导致语法错误。"""
    if not query or not query.strip():
        return ""
    if is_tokenized:
        tokens = query.strip().split()
        if not tokens:
            return ""
        parts = []
        for t in tokens:
            clean = t.replace('"', "")
            if clean:
                parts.append(f'"{clean}"')
        return " OR ".join(parts)
    else:
        clean = query.strip().replace('"', "")
        if not clean:
            return ""
        return f'"{clean}"'


def tokenize_mixed_query_terms(text: str) -> list[str]:
    """Return stable FTS tokens for CJK + ASCII mixed business terms."""
    if not text or not text.strip():
        return []

    raw_parts = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", text)
    terms: list[str] = []

    def add(term: str):
        term = term.strip()
        if not term:
            return
        if re.fullmatch(r"[\u4e00-\u9fff]", term):
            return
        if term not in terms:
            terms.append(term)

    for part in raw_parts:
        add(part)
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            for word in jieba.cut_for_search(part):
                add(word)
            if len(part) <= 12:
                for i in range(len(part) - 1):
                    add(part[i:i + 2])
        else:
            add(part.lower())

    return terms
