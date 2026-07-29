"""SearchUseCase / AskUseCase behaviour preservation tests."""
from __future__ import annotations

from unittest.mock import Mock

from src.application.ask_use_case import AskRequest, AskUseCase
from src.application.search_use_case import SearchRequest, SearchUseCase
from src.models.search_execution import SearchExecution
from src.retrieval.candidate_pool import CandidatePoolPolicy


def test_search_use_case_delegates_and_caps_public_top_k():
    svc = Mock()
    policy = CandidatePoolPolicy.from_request(5)
    svc.execute.return_value = SearchExecution(
        results=tuple(
            {"knowledge_id": f"k{i}", "score": 1.0 - i * 0.01}
            for i in range(policy.public_top_k)
        ),
        trace={"mode": "legacy_raw"},
        disclose_claims=(),
        conflicts=(),
        fallbacks=(),
        warnings=[],
    )
    uc = SearchUseCase(svc)
    execution = uc.execute(SearchRequest(query="q", top_k=5))
    svc.execute.assert_called_once_with("q", top_k=5, query_spec=None)
    assert len(execution.results) <= 5


def test_ask_use_case_forwards_snapshot_id():
    answer = Mock()
    answer.ask.return_value = {
        "answer": "",
        "answer_mode": "no_answer",
        "sources": [],
        "warnings": ["no_answer"],
    }
    uc = AskUseCase(answer)
    payload = uc.execute(
        AskRequest(question="q?", top_k=3, evidence_snapshot_id="snap-1")
    )
    answer.ask.assert_called_once()
    kwargs = answer.ask.call_args.kwargs
    assert kwargs["evidence_snapshot_id"] == "snap-1"
    assert kwargs["question"] == "q?"
    assert payload["answer_mode"] == "no_answer"


def test_ask_use_case_tolerates_legacy_ask_signature():
    class Legacy:
        def ask(self, question, top_k=5):
            return {"answer": "x", "answer_mode": "raw_only", "sources": []}

    uc = AskUseCase(Legacy())
    payload = uc.execute(
        AskRequest(question="q", evidence_snapshot_id="ignored-by-legacy")
    )
    assert payload["answer_mode"] == "raw_only"
