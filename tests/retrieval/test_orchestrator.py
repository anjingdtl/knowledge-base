"""RetrievalOrchestrator mode selection tests (WP5: unified only)."""
from unittest.mock import Mock, patch

from src.models.search_execution import SearchExecution
from src.retrieval.orchestrator import RetrievalOrchestrator, resolve_orchestrator_mode
from src.services.search_service import SearchService


def test_resolve_mode_default_unified():
    assert resolve_orchestrator_mode(None) == "unified"
    assert resolve_orchestrator_mode({}) == "unified"
    assert resolve_orchestrator_mode({"retrieval": {"orchestrator": "unified"}}) == "unified"
    # deprecated aliases map to unified
    assert resolve_orchestrator_mode({"retrieval": {"orchestrator": "SHADOW"}}) == "unified"
    assert resolve_orchestrator_mode({"retrieval": {"orchestrator": "legacy"}}) == "unified"
    assert resolve_orchestrator_mode({"retrieval": {"orchestrator": "nope"}}) == "unified"


def test_legacy_config_uses_unified_path():
    config = {"retrieval": {"orchestrator": "legacy"}}
    svc = SearchService(config, Mock(), Mock(), Mock(), Mock())
    expected = SearchExecution(results=({"source": "knowledge"},), trace={"mode": "x"})
    with patch.object(svc, "_should_use_verified_hybrid", return_value=False), \
         patch(
             "src.retrieval.orchestrator.EvidenceOnlyPolicy.execute",
             return_value=expected,
         ) as m:
        out = RetrievalOrchestrator(svc, config).search("q", top_k=2)
    m.assert_called_once()
    assert out is expected


def test_unified_mode_routes_evidence_only():
    config = {"retrieval": {"orchestrator": "unified"}}
    svc = SearchService(config, Mock(), Mock(), Mock(), Mock())
    expected = SearchExecution(results=(), trace={"mode": "legacy_raw"})
    with patch.object(svc, "_should_use_verified_hybrid", return_value=False), \
         patch(
             "src.retrieval.orchestrator.EvidenceOnlyPolicy.execute",
             return_value=expected,
         ) as m:
        out = RetrievalOrchestrator(svc, config).search("q")
    m.assert_called_once()
    assert out is expected


def test_unified_mode_routes_verified():
    config = {"retrieval": {"orchestrator": "unified"}}
    svc = SearchService(config, Mock(), Mock(), Mock(), Mock())
    expected = SearchExecution(results=(), trace={"mode": "hybrid_verified"})
    with patch.object(svc, "_should_use_verified_hybrid", return_value=True), \
         patch(
             "src.retrieval.orchestrator.VerifiedPolicy.execute",
             return_value=expected,
         ) as m:
        out = RetrievalOrchestrator(svc, config).search("q")
    m.assert_called_once()
    assert out is expected


def test_shadow_config_also_uses_unified():
    config = {"retrieval": {"orchestrator": "shadow"}}
    svc = SearchService(config, Mock(), Mock(), Mock(), Mock())
    expected = SearchExecution(results=({"source": "knowledge", "knowledge_id": "k1"},), trace={})
    with patch.object(svc, "_should_use_verified_hybrid", return_value=False), \
         patch(
             "src.retrieval.orchestrator.EvidenceOnlyPolicy.execute",
             return_value=expected,
         ) as m:
        out = RetrievalOrchestrator(svc, config).search("q")
    m.assert_called_once()
    assert out is expected


def test_search_service_execute_uses_orchestrator():
    config = {"retrieval": {"orchestrator": "unified"}}
    svc = SearchService(config, Mock(), Mock(), Mock(), Mock())
    expected = SearchExecution(results=(), trace={})
    with patch.object(
        RetrievalOrchestrator, "search", return_value=expected,
    ) as m:
        out = svc.execute("hello")
    m.assert_called_once()
    assert out is expected
