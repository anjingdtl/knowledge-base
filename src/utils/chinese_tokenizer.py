"""中文分词工具 — 基于 jieba"""
import logging
import re
from pathlib import Path

import jieba
import jieba.posseg as pseg

logger = logging.getLogger(__name__)

# 模块级 flag：jieba 词典只加载一次（进程级全局副作用，幂等）
_lexical_dict_loaded = False


def _ensure_lexical_dict() -> None:
    """一次性加载自定义专名词典进 jieba（查询+索引两路径都受益）。

    进程级全局副作用，flag 保证只加载一次。加载失败仅 warning 不阻塞检索
    （与 wiki hook 同策略）。Config 未初始化（纯算法测试）时静默跳过。
    legacy（rag.lexical_zh.enabled 缺省 false）完全 no-op。
    """
    global _lexical_dict_loaded
    if _lexical_dict_loaded:
        return
    _lexical_dict_loaded = True  # 先设 flag，避免异常时重复尝试
    try:
        from src.utils.config import Config
        if not Config.get("rag.lexical_zh.enabled", False):
            return
        dict_path = Config.get("rag.lexical_zh.dict_path", "")
        if not dict_path:
            return
        path = Path(dict_path)
        if not path.is_file():
            return  # 可空，静默
        import jieba
        jieba.load_userdict(str(path))
        logger.info("Loaded lexical zh dict: %s", path)
    except Exception as e:
        logger.warning("lexical dict load failed (non-fatal): %s", e)


def detect_query_language(text: str) -> str:
    """返回 'zh' 或 'en'。query 含任意 CJK 基本区字符即判 zh。

    短 query（如 'FTTR 是什么'）仅少量汉字也应判 zh，不做比例阈值。
    """
    if not text:
        return "en"
    return "zh" if re.search(r"[一-鿿]", text) else "en"


def tokenize_chinese(text: str) -> str:
    """用 jieba 搜索引擎模式分词，返回空格分隔的词组字符串"""
    words = jieba.cut_for_search(text)
    return " ".join(w.strip() for w in words if w.strip())


def tokenize_chinese_full(text: str) -> str:
    """jieba 全模式分词（MaxKB 风格），返回空格分隔词组。
    全模式会产出所有可能的词组组合，适合 FTS 索引。"""
    _ensure_lexical_dict()  # W3: 首次分词前确保专名词典已加载（幂等）
    if not text.strip():
        return ""
    words = jieba.lcut(text, cut_all=True)
    return " ".join(w.strip() for w in words if w.strip())


_IMPORT_JIEBA_POSSEG = True  # module-level flag


_PROPER_NOUN_POS = {"nr", "ns", "nt", "nz", "NR", "NS", "NT", "NZ"}
_PROPER_NOUN_MIN_LEN = 2  # 专有名词至少2字，过滤单字噪声


def detect_proper_nouns(query: str) -> list[str]:
    """检测查询中的专有名词（人名/地名/机构名/其他专名）。

    使用 jieba POS 标注（nr=人名, ns=地名, nt=机构名, nz=其他专名），
    过滤掉单字噪声（单字人名/地名误判率高），返回去重列表。

    用途：专有名词在 RRF 融合中增强 keyword 通道权重，
    因为专有名词在向量搜索中容易被淹没，但在 FTS 中精确匹配价值极高。
    """
    if not query or not query.strip():
        return []
    proper_nouns = []
    seen = set()
    for word, flag in pseg.cut(query):
        if flag in _PROPER_NOUN_POS and len(word) >= _PROPER_NOUN_MIN_LEN and word not in seen:
            proper_nouns.append(word)
            seen.add(word)
    # 补充：连续大写英文缩写（AI, MCP, RAG）
    for m in re.finditer(r"\b[A-Z]{2,}\b", query):
        w = m.group()
        if w not in seen:
            proper_nouns.append(w)
            seen.add(w)
    # 补充：中英混合术语（"AI介入率"）
    for m in re.finditer(r"[A-Za-z]+[\u4e00-\u9fff]+|[\u4e00-\u9fff]+[A-Za-z]+", query):
        w = m.group()
        if len(w) >= 2 and w not in seen:
            proper_nouns.append(w)
            seen.add(w)
    return proper_nouns


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
    """Return stable FTS tokens for CJK + ASCII mixed business terms.

    BUG-6 fix: 对 CJK+ASCII 混合术语（如 "AI介入率"）同时保留原始短语
    和分词结果，确保在 jieba 预分词的 FTS 索引中也能命中短语匹配。
    """
    if not text or not text.strip():
        return []

    raw_parts = re.findall(r"[A-Za-z0-9+]+|[\u4e00-\u9fff]+", text)
    terms: list[str] = []

    def add(term: str):
        term = term.strip()
        if not term:
            return
        if re.fullmatch(r"[\u4e00-\u9fff]", term):
            return
        if term not in terms:
            terms.append(term)

    # BUG-6: 保留原始混合短语（如 "AI介入率"）作为整体搜索词
    # 去掉纯空白后的原始文本，适合 jieba 预分词索引中的短语匹配
    original_phrase = re.sub(r"\s+", "", text).strip()
    if original_phrase and len(original_phrase) >= 2:
        add(original_phrase)

    for part in raw_parts:
        add(part)
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            for word in jieba.cut_for_search(part):
                add(word)
            # bigrams
            if len(part) <= 12:
                for i in range(len(part) - 1):
                    add(part[i:i + 2])
            # BUG-6: 添加 trigrams 提升 3+ 字中文短语的召回
            if len(part) >= 3 and len(part) <= 15:
                for i in range(len(part) - 2):
                    add(part[i:i + 3])
        else:
            add(part.lower())

    # BUG-6: 对跨 CJK+ASCII 边界的连续片段，保留原始混合子短语
    # 例如 "AI介入率" → 保留 "AI介入" 和 "介入率" 等跨越边界的子短语
    normalized = re.sub(r"\s+", "", text)
    if len(normalized) >= 3:
        # 提取跨越 ASCII/CJK 边界的连续子串（长度 2-8）
        for window in range(min(8, len(normalized)), 1, -1):
            for i in range(len(normalized) - window + 1):
                sub = normalized[i:i + window]
                has_ascii = bool(re.search(r"[A-Za-z0-9]", sub))
                has_cjk = bool(re.search(r"[\u4e00-\u9fff]", sub))
                if has_ascii and has_cjk:
                    add(sub)

    return terms
