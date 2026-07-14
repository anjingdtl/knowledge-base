"""Shadow cutover gate thresholds (Spec §8) — unit-level."""
from src.models.search_execution import SearchExecution
from src.retrieval.shadow_comparator import compare_executions, meets_cutover_gates
from src.retrieval.orchestrator import resolve_orchestrator_mode


def _ex(kid: str, claim_id: str | None = None, conflict: bool = False, fb: str | None = None):
    row = {
        "source": "verified_claim" if claim_id else "knowledge",
        "knowledge_id": kid,
        "claim_id": claim_id,
        "citation": {"id": f"cit-{kid}", "knowledge_id": kid},
    }
    return SearchExecution(
        results=(row,),
        conflicts=({"conflict_id": "cf1"},) if conflict else (),
        fallbacks=(
            ({"from": "verified_wiki", "to": "raw_retrieval", "reason": fb},)
            if fb
            else ()
        ),
        disclose_claims=(),
        trace={},
    )


def test_cutover_requires_full_parity():
    a = _ex("k1", "c1", conflict=True, fb="empty_wiki_to_raw")
    b = _ex("k1", "c1", conflict=True, fb="empty_wiki_to_raw")
    assert meets_cutover_gates(compare_executions(a, b))


def test_cutover_fails_on_claim_mismatch():
    a = _ex("k1", "c1")
    b = _ex("k1", "c2")
    assert not meets_cutover_gates(compare_executions(a, b))


def test_cutover_fails_on_conflict_mismatch():
    a = _ex("k1", "c1", conflict=True)
    b = _ex("k1", "c1", conflict=False)
    assert not meets_cutover_gates(compare_executions(a, b))


def test_cutover_fails_on_fallback_mismatch():
    a = _ex("k1", fb="empty_wiki_to_raw")
    b = _ex("k1", fb="wiki_claim_timeout")
    assert not meets_cutover_gates(compare_executions(a, b))


def test_default_mode_remains_legacy_for_rollback():
    assert resolve_orchestrator_mode(None) == "legacy"
    assert resolve_orchestrator_mode({"retrieval": {}}) == "legacy"
