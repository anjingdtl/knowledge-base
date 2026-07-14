"""Public Ask behavior contract — Phase-1 maintainability freeze.

Freezes answer_mode / sources / claims / conflicts / fallbacks keys.
Uses VerifiedAnswerService with deterministic llm_answer / use_llm=False.
"""
from __future__ import annotations

from src.models.search_execution import SearchExecution
from src.services.verified_answer import (
    ANSWER_MODE_CONFLICT,
    ANSWER_MODE_HYBRID,
    ANSWER_MODE_NO_ANSWER,
    ANSWER_MODE_RAW,
    VerifiedAnswerService,
)
from tests.helpers.contract_normalize import assert_matches_snapshot, normalize_ask_contract

REQUIRED_ASK_KEYS = {
    "answer",
    "answer_mode",
    "sources",
    "claims_used",
    "raw_evidence_used",
    "conflicts",
    "fallbacks",
    "warnings",
    "trace_id",
    "route",
}


class _FixedSearch:
    """Search double that returns a fixed SearchExecution per call."""

    def __init__(self, execution: SearchExecution):
        self._execution = execution

    def execute(self, query: str, top_k: int = 5, query_spec=None) -> SearchExecution:
        return self._execution

    def search(self, query: str, top_k: int = 5, query_spec=None):
        return list(self._execution.results)


def _claim_row(cid: str, text: str, kid: str = "k1", bid: str = "b1") -> dict:
    return {
        "source": "verified_claim",
        "candidate_type": "claim",
        "claim_id": cid,
        "text": text,
        "evidence": [{
            "knowledge_id": kid,
            "block_id": bid,
            "stance": "supports",
            "excerpt": text[:40],
        }],
        "status": "active",
        "eligible": True,
    }


def _raw_row(kid: str = "k1", bid: str = "b1", text: str = "raw evidence") -> dict:
    return {
        "source": "knowledge",
        "knowledge_id": kid,
        "block_id": bid,
        "title": "Doc",
        "text": text,
        "score": 0.8,
        "citation": {"path": "/docs/a.md", "knowledge_id": kid, "block_id": bid},
    }


class TestPublicAskContract:
    def test_ask_hybrid_verified(self):
        ex = SearchExecution(
            results=(
                _claim_row("c1", "FTTR 定义是光纤到房间"),
                _raw_row(text="原始文档：光纤到房间"),
            ),
            trace={"mode": "hybrid_verified", "route": {"intent": "definition"}, "stages": {}},
        )
        svc = VerifiedAnswerService(_FixedSearch(ex), llm=None, config={})
        payload = svc.ask("什么是 FTTR", top_k=5, use_llm=False)
        for k in REQUIRED_ASK_KEYS:
            assert k in payload, f"missing ask key: {k}"
        assert payload["answer_mode"] == ANSWER_MODE_HYBRID
        assert payload["claims_used"]
        snap = normalize_ask_contract(payload)
        assert_matches_snapshot("ask_hybrid_verified.json", snap)

    def test_ask_raw_only(self):
        ex = SearchExecution(
            results=(_raw_row(text="仅原始块内容"),),
            trace={"mode": "legacy_raw", "stages": {}},
        )
        svc = VerifiedAnswerService(_FixedSearch(ex), llm=None, config={})
        payload = svc.ask("文档说了什么", top_k=5, use_llm=False)
        assert payload["answer_mode"] == ANSWER_MODE_RAW
        assert payload["raw_evidence_used"]
        assert not payload["claims_used"]
        assert_matches_snapshot("ask_raw_only.json", normalize_ask_contract(payload))

    def test_ask_conflict(self):
        ex = SearchExecution(
            results=(
                _claim_row("c1", "峰值 1Gbps", kid="k1", bid="b1"),
                _claim_row("c2", "峰值 100Mbps", kid="k2", bid="b2"),
            ),
            trace={"mode": "hybrid_verified", "stages": {}},
        )
        svc = VerifiedAnswerService(_FixedSearch(ex), llm=None, config={})
        payload = svc.ask("峰值是多少", top_k=5, use_llm=False)
        assert payload["answer_mode"] == ANSWER_MODE_CONFLICT
        assert payload["conflicts"]
        assert payload.get("conflict_disclosed") is True
        assert_matches_snapshot("ask_conflict.json", normalize_ask_contract(payload))

    def test_ask_no_answer(self):
        ex = SearchExecution(
            results=(),
            trace={"mode": "hybrid_verified", "stages": {}, "result_count": 0},
        )
        svc = VerifiedAnswerService(_FixedSearch(ex), llm=None, config={})
        payload = svc.ask("完全不存在的主题 xyz", top_k=5, use_llm=False)
        assert payload["answer_mode"] == ANSWER_MODE_NO_ANSWER
        assert "no_answer" in (payload.get("warnings") or [])
        assert_matches_snapshot("ask_no_answer.json", normalize_ask_contract(payload))

    def test_ask_timeout_generate_failed(self):
        """Simulate LLM generation failure → deterministic template + warning."""
        ex = SearchExecution(
            results=(_raw_row(text="有证据"),),
            trace={"mode": "legacy_raw", "stages": {}},
        )

        class BoomLLM:
            def chat(self, messages):
                raise TimeoutError("llm timeout")

            def chat_with_usage(self, messages):
                raise TimeoutError("llm timeout")

        svc = VerifiedAnswerService(_FixedSearch(ex), llm=BoomLLM(), config={})
        payload = svc.ask("有证据吗", top_k=5, use_llm=True)
        assert payload["answer_mode"] == ANSWER_MODE_RAW
        assert payload["answer"]
        warnings = payload.get("warnings") or []
        assert any("generate_failed" in str(w) for w in warnings)
        assert_matches_snapshot("ask_timeout.json", normalize_ask_contract(payload))

    def test_ask_consumes_execute_not_last_state(self):
        """Regression: VerifiedAnswer must not require last_* attributes."""
        calls = {"n": 0}

        class CountingSearch:
            def execute(self, query, top_k=5, query_spec=None):
                calls["n"] += 1
                return SearchExecution(
                    results=(_raw_row(text=f"hit-{query}"),),
                    trace={"mode": "legacy_raw", "query": query, "stages": {}},
                )

        svc = VerifiedAnswerService(CountingSearch(), llm=None, config={})
        p = svc.ask("q1", use_llm=False)
        assert calls["n"] == 1
        assert p["answer_mode"] == ANSWER_MODE_RAW
        assert not hasattr(CountingSearch(), "last_search_trace")
