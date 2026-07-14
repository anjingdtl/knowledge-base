"""Phase-2 Wiki serving gates (Spec §7) via VerifiedProvider + Gate."""
from unittest.mock import MagicMock

from src.retrieval.verified_provider import VerifiedProvider
from src.services.wiki_serving_gate import (
    REASON_CLAIM_RETRACTED,
    REASON_CLAIM_UNSUPPORTED,
    ServingDecision,
    WikiServingGate,
)


def _claim(claim_id: str, statement: str = "alpha fact", status: str = "active"):
    c = MagicMock()
    c.claim_id = claim_id
    c.statement = statement
    c.normalized_statement = statement
    c.status = MagicMock(value=status)
    return c


def test_active_claim_eligible_enhancement():
    claim = _claim("active-1")
    decision = ServingDecision(claim_id="active-1", eligible=True, disclose_only=False)
    gate = MagicMock(spec=WikiServingGate)
    gate.filter_servable.return_value = [(claim, decision)]
    repo = MagicMock()
    repo.list_claims.return_value = [claim]
    r = VerifiedProvider(repo, gate).serve("alpha", limit=5)
    assert any(x["claim_id"] == "active-1" for x in r.eligible_claims)
    assert r.fallback_reason is None


def test_stale_unsupported_retracted_filtered_by_gate():
    """Provider must not invent eligibility — gate filter decides."""
    gate = MagicMock(spec=WikiServingGate)
    # gate returns empty → all filtered
    gate.filter_servable.return_value = []
    repo = MagicMock()
    repo.list_claims.return_value = [
        _claim("stale-1"),
        _claim("unsup-1"),
        _claim("ret-1"),
    ]
    r = VerifiedProvider(repo, gate).serve("alpha", limit=10)
    assert r.eligible_claims == ()
    assert r.disclose_claims == ()
    gate.filter_servable.assert_called_once()


def test_repo_exception_raw_fallback_reason():
    repo = MagicMock()
    repo.list_claims.side_effect = RuntimeError("projection down")
    r = VerifiedProvider(repo, MagicMock()).serve("q", limit=5)
    assert r.fallback_reason is not None
    assert "projection down" in r.fallback_reason
    assert r.claim_pairs == ()


def test_no_repo_is_safe_fallback():
    r = VerifiedProvider(None, None).serve("q")
    assert r.fallback_reason == "no_wiki_repository"


def test_provider_never_bypasses_gate():
    """Even if repo returns claims, without gate.filter_servable they cannot serve."""
    claim = _claim("c-bypass")
    repo = MagicMock()
    repo.list_claims.return_value = [claim]
    gate = MagicMock()
    gate.filter_servable.return_value = []  # gate blocks all
    r = VerifiedProvider(repo, gate).serve("alpha", limit=5)
    assert r.eligible_claims == ()
    assert r.claim_pairs == ()


def test_reason_codes_constants_exist_for_gate_docs():
    # Ensure gate reason vocabulary still present (no accidental delete)
    assert REASON_CLAIM_RETRACTED
    assert REASON_CLAIM_UNSUPPORTED
