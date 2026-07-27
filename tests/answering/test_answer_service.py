"""AnswerService unit tests."""
from src.answering.models import AnswerExecution
from src.answering.service import AnswerService, resolve_answer_orchestrator_mode
from src.answering.shadow import compare_answers, meets_answer_cutover_gates
from src.models.search_execution import SearchExecution


class _FixedSearch:
    def __init__(self, execution: SearchExecution):
        self._execution = execution

    def execute(self, query: str, top_k: int = 5, query_spec=None) -> SearchExecution:
        return self._execution


def _claim(cid: str = "c1", text: str = "claim text"):
    return {
        "source": "verified_claim",
        "candidate_type": "claim",
        "claim_id": cid,
        "text": text,
        "evidence": [{
            "knowledge_id": "k1",
            "block_id": "b1",
            "stance": "supports",
            "excerpt": text[:40],
        }],
        "status": "active",
        "eligible": True,
    }


def test_resolve_mode_default_unified():
    """WP2-T2: legacy/shadow names collapse to unified (no separate path)."""
    assert resolve_answer_orchestrator_mode(None) == "unified"
    assert resolve_answer_orchestrator_mode({"answer": {"orchestrator": "shadow"}}) == "unified"
    assert resolve_answer_orchestrator_mode({"answer": {"orchestrator": "legacy"}}) == "unified"
    assert resolve_answer_orchestrator_mode({"answer": {"orchestrator": "unified"}}) == "unified"


def test_execute_returns_answer_execution():
    ex = SearchExecution(
        results=(_claim(),),
        trace={"mode": "hybrid_verified"},
    )
    svc = AnswerService(_FixedSearch(ex), config={"answer": {"orchestrator": "unified"}})
    out = svc.execute("什么是 FTTR", top_k=3, use_llm=False)
    assert isinstance(out, AnswerExecution)
    assert out.answer_mode == "hybrid_verified"
    assert out.claims_used
    payload = out.to_ask_payload()
    assert "route" in payload
    assert payload["answer_mode"] == "hybrid_verified"


def test_no_answer_mode():
    ex = SearchExecution(results=(), trace={})
    svc = AnswerService(_FixedSearch(ex), config={"answer": {"orchestrator": "unified"}})
    out = svc.execute("无结果问题", use_llm=False)
    assert out.answer_mode == "no_answer"


def test_legacy_and_unified_structural_parity():
    ex = SearchExecution(
        results=(_claim("c9", "alpha beta"),),
        trace={"mode": "hybrid_verified", "fallbacks": []},
    )
    search = _FixedSearch(ex)
    legacy = AnswerService(
        search, config={"answer": {"orchestrator": "legacy"}},
    ).execute("q", use_llm=False)
    unified = AnswerService(
        search, config={"answer": {"orchestrator": "unified"}},
    ).execute("q", use_llm=False)
    diff = compare_answers(legacy, unified)
    assert meets_answer_cutover_gates(diff), diff.notes


def test_ask_dict_has_required_keys():
    ex = SearchExecution(results=(_claim(),), trace={})
    payload = AnswerService(
        _FixedSearch(ex), config={"answer": {"orchestrator": "unified"}},
    ).ask("q", use_llm=False)
    for key in (
        "answer", "answer_mode", "sources", "claims_used",
        "raw_evidence_used", "conflicts", "fallbacks", "warnings", "trace_id", "route",
    ):
        assert key in payload


def test_numeric_fact_guard_strips_unanchored_ii_class_value():
    """KB-019: an LLM answer that cites the II类 value (10万元) when the
    evidence only contains the III类 value (20万元) must have the unanchored
    value stripped by the fact guard."""
    from src.answering.assembler import assemble_answer_payload

    # Evidence: only the III类 clause reached the context (truncation).
    raw_rows = [{
        "source": "knowledge",
        "knowledge_id": "27922ca4",
        "block_id": "b1",
        "title": "翼支付业务管理办法",
        "text": "III类支付账户，其余额年付款限额为20万元（不含提现）。",
    }]
    # LLM produced an answer that incorrectly asserts the II类 value 10万元.
    answer = "翼支付III类支付账户的余额年付款限额为10万元。"
    payload = assemble_answer_payload(
        "翼支付III类支付账户 年付款限额",
        raw_rows,
        llm_answer=answer,
        search_trace={},
    )
    # 10万元 must be stripped (not in evidence); 20万元 is preserved.
    assert "10万元" not in payload["answer"], payload["answer"]
    assert "numeric_fact_guard_stripped_unanchored_value" in payload["warnings"]


def test_numeric_fact_guard_keeps_anchored_value():
    """When the LLM answer matches the evidence, the guard leaves it intact."""
    from src.answering.assembler import assemble_answer_payload

    raw_rows = [{
        "source": "knowledge",
        "knowledge_id": "27922ca4",
        "block_id": "b1",
        "title": "翼支付业务管理办法",
        "text": "III类支付账户，其余额年付款限额为20万元（不含提现）。",
    }]
    answer = "翼支付III类支付账户的余额年付款限额为20万元。"
    payload = assemble_answer_payload(
        "翼支付III类支付账户 年付款限额",
        raw_rows,
        llm_answer=answer,
        search_trace={},
    )
    assert "20万元" in payload["answer"]
    assert "numeric_fact_guard_stripped_unanchored_value" not in payload["warnings"]
