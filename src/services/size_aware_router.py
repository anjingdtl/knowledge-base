"""SizeAwareRouter —— 按查询规模三档分流(第二阶段 W1 Task 1.1, spec §4.1 / S1)。

补 Karpathy「小规模用 index / 大规模用搜索」原则:小查询只读 wiki(零向量),
大查询走现有 hybrid 搜索,中间档 blend 融合两路。规则层先行(零 LLM 成本),
``llm_fallback`` 默认关闭。

档位判定(优先级从高到低):
  1. 含意图词(哪些/所有/对比/全部/列举) → ``full_search``
  2. wiki 命中数 == 0(无所读) → ``full_search``
  3. query token ≤ ``small_query_max_tokens`` 且 wiki 命中 ≤
     ``small_wiki_page_threshold`` → ``wiki_read``
  4. 其余 → ``blend``

返回 dict 的 ``scale`` 字段透传到检索管线 ``ctx.metadata["scale"]``,驱动
``VectorSearchStage`` 分流(见 Task 1.2/1.4)。
"""
from __future__ import annotations

import logging
import re

import jieba

from src.services.wiki_page_locator import WikiPageLocator
from src.utils.config import Config

logger = logging.getLogger(__name__)

_PUNCT_RE = re.compile(r"^[\s\W]+$")

_DEFAULT_INTENT_WORDS = ["哪些", "所有", "对比", "全部", "列举"]


class SizeAwareRouter:
    """规模自适应路由器(规则层)。

    Args:
        locator: ``WikiPageLocator`` 实例,或任意带 ``locate(query, top_n) -> (list, int)``
            的 duck-typed 对象(测试可注入替身)。
        small_query_max_tokens / small_wiki_page_threshold / intent_words_large /
            llm_fallback: 阈值与意图词覆盖。``None`` 时从 ``rag.size_aware.*`` 读取
            (默认 12 / 3 / ["哪些","所有","对比","全部","列举"] / False)。
    """

    def __init__(
        self,
        locator: WikiPageLocator,
        *,
        small_query_max_tokens: int | None = None,
        small_wiki_page_threshold: int | None = None,
        intent_words_large: list[str] | None = None,
        llm_fallback: bool | None = None,
    ) -> None:
        self._locator = locator
        self._small_query_max_tokens = (
            small_query_max_tokens
            if small_query_max_tokens is not None
            else int(Config.get("rag.size_aware.small_query_max_tokens", 12))
        )
        self._small_wiki_page_threshold = (
            small_wiki_page_threshold
            if small_wiki_page_threshold is not None
            else int(Config.get("rag.size_aware.small_wiki_page_threshold", 3))
        )
        iw = (
            intent_words_large
            if intent_words_large is not None
            else Config.get("rag.size_aware.intent_words_large", _DEFAULT_INTENT_WORDS)
        )
        self._intent_words = [str(w) for w in (iw or _DEFAULT_INTENT_WORDS) if str(w).strip()]
        self._llm_fallback = (
            llm_fallback
            if llm_fallback is not None
            else bool(Config.get("rag.size_aware.llm_fallback", False))
        )

    @property
    def llm_fallback(self) -> bool:
        return self._llm_fallback

    def route(self, question: str) -> dict:
        """判定查询规模档。

        Returns:
            ``{"scale": "wiki_read"|"full_search"|"blend", "reason": str,
            "wiki_hits": int, "token_count": int}``
        """
        question = question or ""
        _, wiki_hits = self._locator.locate(question)
        token_count = _count_tokens(question)

        intent_hit = [w for w in self._intent_words if w and w in question]
        if intent_hit:
            return {
                "scale": "full_search",
                "reason": f"intent word matched: {intent_hit}",
                "wiki_hits": wiki_hits,
                "token_count": token_count,
            }
        if wiki_hits == 0:
            return {
                "scale": "full_search",
                "reason": "no wiki page hit",
                "wiki_hits": 0,
                "token_count": token_count,
            }
        if (
            token_count <= self._small_query_max_tokens
            and wiki_hits <= self._small_wiki_page_threshold
        ):
            return {
                "scale": "wiki_read",
                "reason": (
                    f"small query: tokens={token_count}≤{self._small_query_max_tokens}, "
                    f"wiki_hits={wiki_hits}≤{self._small_wiki_page_threshold}"
                ),
                "wiki_hits": wiki_hits,
                "token_count": token_count,
            }
        return {
            "scale": "blend",
            "reason": f"medium query: tokens={token_count}, wiki_hits={wiki_hits}",
            "wiki_hits": wiki_hits,
            "token_count": token_count,
        }


def _count_tokens(question: str) -> int:
    """jieba 分词后非空非标点 token 数(查询规模度量,不过滤停用词)。"""
    return sum(1 for t in jieba.cut(question) if t.strip() and not _PUNCT_RE.match(t))
