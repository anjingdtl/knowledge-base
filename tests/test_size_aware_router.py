"""SizeAwareRouter 规则层单元测试(第二阶段 Task 1.1, spec S1)。

用 _FakeLocator 隔离路由逻辑与真实 wiki 扫描,精确控制 wiki_hits,
只验证档位判定规则与阈值可配置性。
"""
from __future__ import annotations

from src.services.size_aware_router import SizeAwareRouter


class _FakeLocator:
    """可控 hits 的 locator 替身。"""

    def __init__(self, hits: int) -> None:
        self._hits = hits

    def locate(self, query: str, top_n: int = 10):
        return [], self._hits


def test_small_query_routes_to_wiki_read():
    router = SizeAwareRouter(_FakeLocator(hits=2))
    # "FTTR是什么" → jieba [FTTR, 是, 什么] = 3 tokens ≤ 12; hits 2 ≤ 3
    result = router.route("FTTR是什么")
    assert result["scale"] == "wiki_read"


def test_intent_word_routes_to_full_search():
    router = SizeAwareRouter(_FakeLocator(hits=2))
    result = router.route("有哪些 FTTR 相关文档")
    assert result["scale"] == "full_search"


def test_no_wiki_hits_routes_to_full_search():
    router = SizeAwareRouter(_FakeLocator(hits=0))
    result = router.route("FTTR是什么")
    assert result["scale"] == "full_search"


def test_medium_routes_to_blend():
    router = SizeAwareRouter(_FakeLocator(hits=2))
    # 13 个独立英文 token > 12; hits 2 ≤ 3; 无意图词 → blend
    long_query = "FTTR GPON OLT ONU BSS OSS CRM ARPU APP WAP CP PPT IT"
    result = router.route(long_query)
    assert result["scale"] == "blend"


def test_thresholds_from_config():
    # 注入更小阈值:同一短查询(3 tokens)从 wiki_read 变 blend
    router_default = SizeAwareRouter(_FakeLocator(hits=2))
    router_strict = SizeAwareRouter(
        _FakeLocator(hits=2), small_query_max_tokens=2
    )
    assert router_default.route("FTTR是什么")["scale"] == "wiki_read"
    assert router_strict.route("FTTR是什么")["scale"] == "blend"


def test_route_includes_reason_and_wiki_hits():
    router = SizeAwareRouter(_FakeLocator(hits=2))
    result = router.route("FTTR是什么")
    assert isinstance(result.get("reason"), str) and result["reason"]
    assert isinstance(result.get("wiki_hits"), int)
