"""中文分词工具 — 基于 jieba"""
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
