"""RetrievalOrchestrator mode selection tests."""
from unittest.mock import Mock, patch

from src.models.search_execution import SearchExecution
from src.retrieval.orchestrator import RetrievalOrchestrator, resolve_orchestrator_mode
from src.services.search_service import SearchService


def test_resolve_mode_default_legacy():
    assert resolve_orchestrator_mode(None) == "legacy"
    assert resolve_orchestrator_mode({}) == "legacy"
    assert resolve_orchestrator_mode({"retrieval": {"orchestrator": "unified"}}) == "unified"
    assert resolve_orchestrator_mode({"retrieval": {"orchestrator": "SHADOW"}}) == "shadow"
    assert resolve_orchestrator_mode({"retrieval": {"orchestrator": "nope"}}) == "legacy"


def test_legacy_mode_uses_primary_legacy():
    config = {"retrieval": {"orchestrator": "legacy"}}
    svc = SearchService(config, Mock(), Mock(), Mock(), Mock())
    expected = SearchExecution(results=({"source": "knowledge"},), trace={"mode": "x"})
    with patch.object(svc, "execute_primary_legacy", return_value=expected) as m:
        out = RetrievalOrchestrator(svc, config).search("q", top_k=2)
    m.assert_called_once()
    assert out is expected


def test_unified_mode_routes_evidence_only():
    config = {"retrieval": {"orchestrator": "unified"}}
    svc = SearchService(config, Mock(), Mock(), Mock(), Mock())
    expected = SearchExecution(results=(), trace={"mode": "legacy_raw"})
    with patch.object(svc, "_should_use_verified_hybrid", return_value=False), \
         patch.object(svc, "execute_evidence_only", return_value=expected) as m:
        out = RetrievalOrchestrator(svc, config).search("q")
    m.assert_called_once()
    assert out is expected


def test_unified_mode_routes_verified():
    config = {"retrieval": {"orchestrator": "unified"}}
    svc = SearchService(config, Mock(), Mock(), Mock(), Mock())
    expected = SearchExecution(results=(), trace={"mode": "hybrid_verified"})
    with patch.object(svc, "_should_use_verified_hybrid", return_value=True), \
         patch.object(svc, "execute_verified", return_value=expected) as m:
        out = RetrievalOrchestrator(svc, config).search("q")
    m.assert_called_once()
    assert out is expected


def test_shadow_returns_legacy_even_if_unified_fails():
    config = {"retrieval": {"orchestrator": "shadow"}}
    svc = SearchService(config, Mock(), Mock(), Mock(), Mock())
    primary = SearchExecution(results=({"source": "knowledge", "knowledge_id": "k1"},), trace={})
    with patch.object(svc, "execute_primary_legacy", return_value=primary), \
         patch.object(
             RetrievalOrchestrator,
             "_execute_unified",
             side_effect=RuntimeError("boom"),
         ):
        out = RetrievalOrchestrator(svc, config).search("q")
    assert out is primary


def test_search_service_execute_uses_orchestrator():
    config = {"retrieval": {"orchestrator": "legacy"}}
    svc = SearchService(config, Mock(), Mock(), Mock(), Mock())
    expected = SearchExecution(results=(), trace={})
    with patch.object(svc, "execute_primary_legacy", return_value=expected) as m:
        out = svc.execute("hello")
    m.assert_called_once()
    assert out is expected
