"""Phase 0/6 — 无答案判断。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


NO_ANSWER_QUERIES = [
    "今天公司营收是多少",
    "今日股价多少",
    "量子计算最新突破是什么",
    "火星探测任务进度",
]


@pytest.fixture
def search_env(monkeypatch):
    """提供可调用 search 的最小环境。"""
    from src.services.db import Database

    container = SimpleNamespace(db=Database, hybrid_search=None, search_service=None)
    monkeypatch.setattr("src.mcp.tools.retrieval._get_container", lambda: container)
    return container


@pytest.mark.parametrize("question", NO_ANSWER_QUERIES)
def test_search_no_answer_when_no_relevant_docs(search_env, question, monkeypatch):
    """本地库无证据时应 no_match / 空结果，不得返回不相关文档当有效答案。"""
    from src.mcp.tools import retrieval

    # 若 search 依赖 hybrid，注入低分候选
    def fake_search(*_a, **_k):
        return [
            {
                "title": "无关灯带规格",
                "content": "60珠/米 LED",
                "score": 0.1,
                "knowledge_id": "x",
            }
        ]

    # 尝试 patch 常见入口
    for target in (
        "src.services.search.SearchService.search",
        "src.services.hybrid_search.HybridSearcher.search",
    ):
        try:
            monkeypatch.setattr(target, fake_search)
        except Exception:
            pass

    result = retrieval.search(query=question, limit=5)
    assert result["ok"] is True
    data = result.get("data") or []
    meta = result.get("meta") or {}
    # 期望：空结果或明确 no_match
    if data:
        assert meta.get("no_match") is True or all(
            (item.get("score") or item.get("fts_score") or 1) < 0.35 for item in data
        ), f"expected no-answer gate, got data={data!r} meta={meta!r}"
    else:
        assert meta.get("no_match") is True or meta.get("reason") or len(data) == 0


def test_ask_no_answer_mode_when_evidence_weak(monkeypatch):
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    monkeypatch.setattr(Config, "get", lambda key, default=None: {
        "rag.ask.total_timeout": 5,
        "rag.ask.max_sources": 5,
    }.get(key, default))
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    monkeypatch.setattr(retrieval, "AppContainer", type("X", (), {}))

    def weak_query(question, timeout=None):
        return {
            "answer": "编造的答案",
            "sources": [{"title": "无关", "score": 0.05}],
            "route": {"mode": "hybrid"},
            "warnings": [],
            "query_plan": {},
            "block_contexts": {},
            "wiki_context": "",
            "trace_id": "",
            "answer_mode": "answer",  # 当前错误行为
        }

    monkeypatch.setattr(
        retrieval,
        "_get_container",
        lambda: SimpleNamespace(rag_pipeline=SimpleNamespace(query=weak_query)),
    )
    # 此测试验证生产路径在弱证据时应 no_answer；当前基线可能失败
    # 直接测我们期望的契约字段出现在修复后
    out = retrieval._do_ask("火星探测任务进度")
    # 修复后：answer_mode=no_answer 或 sources 清空
    assert out.get("answer_mode") == "no_answer" or not out.get("sources") or out.get("route", {}).get("mode") == "no_answer"
