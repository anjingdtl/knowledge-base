"""Phase 4 — FTS fallback must honor no-answer relevance gate.

ADR §3.3 freezes three search no-match tiers:
1. accepted (canonical snapshot gate passes)
2. low-confidence (gate rejects but alias/surface FTS has hits — marked)
3. no match (empty data, no_match=true)
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.relevance_gate import evaluate_evidence, score_candidate_relevance


def test_keyword_only_revenue_doc_is_insufficient():
    query = "广西电信2025年营收多少亿"
    item = {
        "title": "营收资金管理办法",
        "text": "本制度规范营收资金归集与管理流程。",
        "fts_score": 0.8,
        "score": 0.2,
    }
    scores = score_candidate_relevance(query, item)
    assert scores["final_relevance_score"] < 0.35
    decision = evaluate_evidence(query, [item], threshold=0.35)
    assert decision["accept"] is False
    assert decision["no_match"] is True


def test_search_fulltext_fallback_low_confidence(monkeypatch):
    """Tier 2: gate rejects but surface FTS has hits → low_confidence results."""
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: 0.35 if "threshold" in key else default,
    )

    weak_hit = {
        "title": "营收资金管理办法",
        "text": "营收资金管理办法适用于财务部门。",
        "fts_score": 0.9,
        "knowledge_id": "FINAL_CLOSURE_TEST_x",
    }

    def weak_ft(query, limit=10, offset=0):
        return {
            "ok": True,
            "data": [dict(weak_hit)],
            "meta": {"top_score": 0.9},
        }

    monkeypatch.setattr(retrieval, "search_fulltext", weak_ft)
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(search_service=None, hybrid_search=None, db=None),
    )

    res = retrieval.search(query="广西电信2025年营收多少亿", limit=5)
    assert res["ok"] is True
    # Gate rejection is preserved: results are explicitly degraded, not accepted.
    assert res["meta"].get("no_match") is False
    assert res["meta"].get("low_confidence") is True
    assert res["meta"].get("source_path") == "fulltext_fallback_low_confidence"
    assert res["data"]
    for item in res["data"]:
        assert item.get("low_confidence") is True
        assert item.get("confidence_reason")


def test_search_fulltext_fallback_true_no_match(monkeypatch):
    """Tier 3: no surface hits at all → empty data + no_match."""
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: 0.35 if "threshold" in key else default,
    )

    def empty_ft(query, limit=10, offset=0):
        return {"ok": True, "data": [], "meta": {"top_score": 0.0}}

    monkeypatch.setattr(retrieval, "search_fulltext", empty_ft)
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(search_service=None, hybrid_search=None, db=None),
    )

    res = retrieval.search(query="广西电信2025年营收多少亿", limit=5)
    assert res["ok"] is True
    assert res["data"] == []
    assert res["meta"].get("no_match") is True
    assert res["meta"].get("source_path") in {
        "canonical_snapshot",
        "current_info_gate",
    }


@pytest.mark.parametrize(
    "query",
    [
        "60米",
        "60珠/米",
        "6个月无互动",
        "6个月试用期",
    ],
)
def test_numeric_unit_queries_score_distinct(query):
    candidates = [
        {"title": "光纤长度", "text": "标准长度为60米", "score": 0.4},
        {"title": "灯带规格", "text": "60珠/米 LED 灯带", "score": 0.4},
        {"title": "试用说明", "text": "6个月试用期", "score": 0.4},
        {"title": "沉默用户", "text": "6个月无互动判定流失", "score": 0.4},
    ]
    ranked = sorted(
        ((score_candidate_relevance(query, c)["final_relevance_score"], c["title"]) for c in candidates),
        reverse=True,
    )
    # Best title should relate to the unit phrase in the query
    assert ranked[0][0] >= ranked[-1][0]
