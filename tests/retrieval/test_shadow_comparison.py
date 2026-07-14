"""Shadow comparator + cutover gate tests."""
from src.models.search_execution import SearchExecution
from src.retrieval.shadow_comparator import (
    compare_executions,
    meets_cutover_gates,
)


def test_identical_executions_meet_gates():
    row = {
        "source": "verified_claim",
        "claim_id": "c1",
        "knowledge_id": "k1",
        "citation": {"id": "cit-1", "block_id": "b1"},
    }
    a = SearchExecution(
        results=(row,),
        disclose_claims=(),
        conflicts=({"conflict_id": "x"},),
        fallbacks=({"from": "verified_wiki", "to": "raw_retrieval", "reason": "empty"},),
        trace={"mode": "hybrid_verified"},
    )
    b = SearchExecution(
        results=(dict(row),),
        disclose_claims=(),
        conflicts=({"conflict_id": "x"},),
        fallbacks=({"from": "verified_wiki", "to": "raw_retrieval", "reason": "empty"},),
        trace={"mode": "hybrid_verified"},
    )
    diff = compare_executions(a, b, top_k=5)
    assert diff.source_id_overlap_top_k == 1.0
    assert diff.claim_ids_match
    assert diff.conflicts_match
    assert diff.fallbacks_match
    assert diff.citation_keys_match
    assert meets_cutover_gates(diff)


def test_source_mismatch_fails_cutover():
    a = SearchExecution(results=({"source": "knowledge", "knowledge_id": "k1"},))
    b = SearchExecution(results=({"source": "knowledge", "knowledge_id": "k2"},))
    diff = compare_executions(a, b, top_k=5)
    assert diff.source_id_overlap_top_k < 0.95
    assert not meets_cutover_gates(diff)


def test_exception_types_fail_cutover():
    a = SearchExecution(results=())
    b = SearchExecution(results=())
    diff = compare_executions(a, b, exception_types=("TimeoutError",))
    assert not meets_cutover_gates(diff)


def test_compare_does_not_require_text_fields():
    """Comparator only uses ids — full text optional."""
    a = SearchExecution(results=({"source": "knowledge", "knowledge_id": "k1", "text": "SECRET"},))
    b = SearchExecution(results=({"source": "knowledge", "knowledge_id": "k1", "text": "OTHER"},))
    diff = compare_executions(a, b)
    assert diff.source_id_overlap_top_k == 1.0
    # notes should not embed SECRET
    joined = " ".join(diff.notes)
    assert "SECRET" not in joined
