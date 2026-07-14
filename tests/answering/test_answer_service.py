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
    assert resolve_answer_orchestrator_mode(None) == "unified"
    assert resolve_answer_orchestrator_mode({"answer": {"orchestrator": "shadow"}}) == "shadow"
    assert resolve_answer_orchestrator_mode({"answer": {"orchestrator": "legacy"}}) == "legacy"


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
