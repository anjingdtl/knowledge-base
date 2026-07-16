"""Phase 4 — weak evidence must block answer generation (no fabricated answer)."""
from __future__ import annotations

from types import SimpleNamespace

from src.mcp.tools import retrieval
from src.utils.config import Config


def test_ask_clears_fabricated_answer_on_weak_evidence(monkeypatch):
    monkeypatch.setattr(
        Config,
        "get",
        lambda key, default=None: {
            "rag.ask.total_timeout": 5,
            "rag.ask.max_sources": 5,
            "rag.ask.no_answer_threshold": 0.35,
        }.get(key, default),
    )
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(retrieval, "AppContainer", type("X", (), {}))

    def weak_query(question, timeout=None):
        return {
            "answer": "编造：广西电信2025年营收999亿",
            "sources": [
                {
                    "title": "营收资金管理办法",
                    "text": "规范营收资金管理",
                    "score": 0.2,
                    "fts_score": 0.7,
                }
            ],
            "route": {"mode": "hybrid"},
            "warnings": [],
            "query_plan": {},
            "block_contexts": {},
            "wiki_context": "",
            "trace_id": "",
            "answer_mode": "answer",
        }

    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=SimpleNamespace(query=weak_query)),
    )
    out = retrieval._do_ask("广西电信2025年营收多少亿")
    assert out.get("answer_mode") == "no_answer"
    assert out.get("answer") in ("", None)
    assert out.get("sources") in ([], None)
    assert out.get("reason") in (
        "insufficient_relevant_evidence",
        "no_candidates",
        None,
    ) or "insufficient" in str(out.get("reason") or out.get("route"))
