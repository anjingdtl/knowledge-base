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


# --- SPEC Phase 3.4: strict no-answer when evidence gate rejected -----------

def test_strict_no_answer_when_snapshot_gate_rejected() -> None:
    """When the evidence_snapshot's gate rejected (accept=False), the
    AnswerService must return a strict no_answer WITHOUT attempting
    generation. The payload must carry the gate reason for auditability."""
    # A snapshot that the gate rejected (no accepted items).
    rejected_snapshot = {
        "query": "今天的实时股价行情",
        "accept": False,
        "reason": "requires_current_external_data",
        "top_score": 0.0,
        "threshold": 0.35,
        "intent": "live_external",
        "accepted_items": [],
        "generation_items": [],
        "accepted_knowledge_ids": [],
        "accepted_block_ids": [],
        "accepted_passage_ids": [],
        "adjacent_allowlist": [],
        "stages": {"gate": {"accept": False, "reason": "requires_current_external_data"}},
        "snapshot_fingerprint": "abc123def456",
        "ranking_reasons": [],
    }
    # Use a search service that would produce results if called — but the
    # snapshot guard must short-circuit before _run_search is invoked.
    ex = SearchExecution(
        results=(_claim("trap", "this must not be used"),),
        trace={"mode": "trap"},
    )
    svc = AnswerService(_FixedSearch(ex), config={"answer": {"orchestrator": "unified"}})
    payload = svc.ask(
        "今天的实时股价行情",
        use_llm=False,
        evidence_snapshot=rejected_snapshot,
    )
    assert payload["answer_mode"] == "no_answer"
    assert payload["answer"] == ""
    assert payload["sources"] == []
    assert payload["raw_evidence_used"] == []
    assert payload["claims_used"] == []
    assert payload["reason"] == "requires_current_external_data"
    assert payload["answer_validation_decision"] == "requires_current_external_data"
    # The snapshot fingerprint must be carried for auditability.
    snap_meta = payload.get("_evidence_snapshot") or {}
    assert snap_meta.get("snapshot_fingerprint") == "abc123def456"
    # The route must record that the gate rejected.
    route = payload.get("route") or {}
    assert route.get("mode") == "no_answer"
    assert "evidence gate rejected" in str(route.get("explanation") or "")


def test_strict_no_answer_carries_ranking_reasons_for_audit() -> None:
    """Even when the gate rejected, the no-answer payload must carry the
    ranking_reasons from the snapshot so audits can trace which candidates
    were considered and why they were rejected."""
    rejected_snapshot = {
        "query": "火星探测任务最新进展",
        "accept": False,
        "reason": "requires_current_external_data",
        "top_score": 0.0,
        "threshold": 0.35,
        "intent": "live_external",
        "accepted_items": [],
        "generation_items": [],
        "accepted_knowledge_ids": [],
        "accepted_block_ids": [],
        "accepted_passage_ids": [],
        "adjacent_allowlist": [],
        "stages": {},
        "snapshot_fingerprint": "ranked_rej_fp",
        "ranking_reasons": [
            {"knowledge_id": "k1", "primary_signal": "base_blend", "boosts": [], "penalties": ["live_external_cap"]},
        ],
    }
    ex = SearchExecution(results=(), trace={})
    svc = AnswerService(_FixedSearch(ex), config={"answer": {"orchestrator": "unified"}})
    payload = svc.ask("火星探测任务最新进展", use_llm=False, evidence_snapshot=rejected_snapshot)
    snap_meta = payload.get("_evidence_snapshot") or {}
    reasons = snap_meta.get("ranking_reasons") or []
    assert len(reasons) == 1
    assert reasons[0]["primary_signal"] == "base_blend"
    assert "live_external_cap" in reasons[0]["penalties"]


def test_strict_no_answer_skips_llm_generation() -> None:
    """The strict no-answer guard must fire BEFORE any LLM call. A rejected
    snapshot with use_llm=True must still not invoke the LLM."""

    class _RecordingLLM:
        def __init__(self) -> None:
            self.called = False

        def generate(self, prompt: str) -> str:
            self.called = True
            return '{"claims":[]}'

    rejected_snapshot = {
        "query": "推荐好吃的火锅",
        "accept": False,
        "reason": "out_of_domain:consumer_recommendation",
        "top_score": 0.0,
        "threshold": 0.35,
        "intent": "out_of_domain",
        "accepted_items": [],
        "generation_items": [],
        "accepted_knowledge_ids": [],
        "accepted_block_ids": [],
        "accepted_passage_ids": [],
        "adjacent_allowlist": [],
        "stages": {},
        "snapshot_fingerprint": "skip_llm_fp",
        "ranking_reasons": [],
    }
    llm = _RecordingLLM()
    ex = SearchExecution(results=(), trace={})
    svc = AnswerService(_FixedSearch(ex), llm=llm, config={"answer": {"orchestrator": "unified"}})
    payload = svc.ask("推荐好吃的火锅", use_llm=True, evidence_snapshot=rejected_snapshot)
    assert payload["answer_mode"] == "no_answer"
    assert llm.called is False, "LLM must not be called when gate rejected"


def test_per_claim_citation_enforced_in_structured_path() -> None:
    """Claims without evidence_passage_ids must be rejected by the
    structured_answer_from_evidence path (per-claim citation contract).

    Every accepted claim MUST have non-empty evidence_passage_ids pointing
    at a passage in the evidence snapshot. An LLM-provided claim that omits
    evidence_passage_ids must NOT appear in claims_used."""
    from src.answering.claim_protocol import structured_answer_from_evidence

    # Evidence row that does exist.
    evidence_rows = [
        {
            "knowledge_id": "k1",
            "passage_id": "p1",
            "title": "差旅费管理办法",
            "text": "差旅费标准为每人每天200元。",
        }
    ]
    # LLM JSON with a claim that has NO evidence_passage_ids — must be rejected.
    llm_json = '{"claims":[{"text":"差旅费标准为200元","evidence_passage_ids":[],"fact_type":"numeric"}]}'
    structured = structured_answer_from_evidence(
        question="差旅费标准",
        evidence_rows=evidence_rows,
        llm_json=llm_json,
        require_passage=True,
    )
    # Every accepted claim MUST have non-empty evidence_passage_ids.
    claims = structured.get("claims_used") or []
    for c in claims:
        claim_dict = c if isinstance(c, dict) else getattr(c, "__dict__", {})
        assert claim_dict.get("evidence_passage_ids"), (
            f"claim without citation accepted: {claim_dict}"
        )
    # The claim_audit must NOT mark any claim with empty evidence_passage_ids
    # as 'kept'. Every 'kept' claim must have non-empty evidence_passage_ids.
    claim_audit = structured.get("claim_audit") or []
    for entry in claim_audit:
        if entry.get("reason") == "kept":
            claim = entry.get("claim") or {}
            assert claim.get("evidence_passage_ids"), (
                f"kept claim has no evidence_passage_ids: {entry}"
            )
