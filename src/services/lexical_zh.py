"""中文 lexical 强化 —— 同义词扩展（第二阶段 W3）。

纯文本词典驱动（零 LLM、永远开），与默认 disabled 的 QueryRewriteStage
（LLM 改写）不同路。挂在 hybrid_search._keyword_search 的 query 预处理：
扩展后的 query 传给 db.search_blocks_fts，FTS5 自动 OR 并集。

加载失败/文件缺失/enabled=false → expand_query 原样返回（零回归）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


_CJK_RE = re.compile(r"[一-鿿]")
# Latin 词边界正则缓存(词典通常很小,复用编译结果)
_LATIN_BOUNDARY_CACHE: dict[str, "re.Pattern[str]"] = {}


def _query_contains_word(word: str, query: str) -> bool:
    """判断 ``word`` 是否作为词出现在 ``query`` 中。

    - CJK 词(含汉字):子串匹配(CJK 无词边界,「创智杯」应匹配「创智杯通知」)。
    - Latin/数字词:用「非 ASCII 字母数字邻接」边界,避免「AI」匹配进「available」
      这类假阳性污染 FTS 召回(原 ``word in query`` 子串匹配的 bug)。
    """
    if not word:
        return False
    if _CJK_RE.search(word):
        return word in query
    pat = _LATIN_BOUNDARY_CACHE.get(word)
    if pat is None:
        pat = re.compile(
            r"(?<![a-zA-Z0-9])" + re.escape(word) + r"(?![a-zA-Z0-9])",
            re.IGNORECASE,
        )
        _LATIN_BOUNDARY_CACHE[word] = pat
    return bool(pat.search(query))


class LexicalZh:
    """同义词扩展器（纯文本词典驱动）。"""

    def __init__(self, config=None):
        self._config = config
        self._synonyms: dict[str, list[str]] | None = None  # 实例级缓存，None=未加载

    def _get_config(self, key: str, default=None):
        if self._config is not None:
            if isinstance(self._config, dict):
                obj: Any = self._config
                for p in key.split("."):
                    if isinstance(obj, dict):
                        obj = obj.get(p)
                    else:
                        return default
                return obj if obj is not None else default
            return self._config.get(key, default)
        try:
            from src.utils.config import Config
            return Config.get(key, default)
        except Exception:
            return default

    def _load_synonyms(self) -> dict[str, list[str]]:
        """读 synonym_path → {词: [同义词...]}，实例级缓存，失败返回 {} + warning。"""
        if self._synonyms is not None:
            return self._synonyms
        self._synonyms = {}
        if not self._get_config("rag.lexical_zh.enabled", False):
            return self._synonyms  # disabled
        path = self._get_config("rag.lexical_zh.synonym_path", "")
        if not path:
            return self._synonyms
        try:
            from pathlib import Path
            p = Path(path)
            if not p.is_file():
                return self._synonyms  # 可空，静默
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue  # 单词行无同义词，跳过
                word, syns = parts[0], parts[1:]
                self._synonyms[word] = syns
        except Exception as e:
            logger.warning("lexical synonym load failed (non-fatal): %s", e)
        return self._synonyms

    def expand_query(self, query: str) -> str:
        """扩展 query：追加命中的同义词。无命中/容错时返回原 query（零回归）。"""
        if not query:
            return query
        synonyms = self._load_synonyms()
        if not synonyms:
            return query
        extras: list[str] = []
        for word, syns in synonyms.items():
            if _query_contains_word(word, query):
                extras.extend(syns)
        if not extras:
            return query
        return query + " " + " ".join(extras)


def expand_query_with_synonyms(query: str, config=None) -> str:
    """便捷函数：扩展 query 同义词。"""
    return LexicalZh(config=config).expand_query(query)
