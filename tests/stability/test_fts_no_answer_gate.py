"""Phase 4 — FTS fallback must honor no-answer relevance gate."""
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


def test_search_fulltext_fallback_no_match(monkeypatch):
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: 0.35 if "threshold" in key else default,
    )

    def weak_ft(query, limit=10, offset=0):
        return {
            "ok": True,
            "data": [
                {
                    "title": "营收资金管理办法",
                    "text": "营收资金管理办法适用于财务部门。",
                    "fts_score": 0.9,
                    "knowledge_id": "FINAL_CLOSURE_TEST_x",
                }
            ],
            "meta": {"top_score": 0.9},
        }

    monkeypatch.setattr(retrieval, "search_fulltext", weak_ft)
    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(search_service=None, hybrid_search=None, db=None),
    )

    # Force semantic path empty/weak
    class Boom:
        def semantic_search(self, *a, **k):
            return []

    import src.application.retrieval_commands as rc

    monkeypatch.setattr(rc, "RetrievalCommands", lambda container: Boom())

    res = retrieval.search(query="广西电信2025年营收多少亿", limit=5)
    assert res["ok"] is True
    assert res["data"] == []
    assert res["meta"].get("no_match") is True


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
