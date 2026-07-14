"""VerifiedProvider adapter tests."""
from unittest.mock import MagicMock

from src.retrieval.verified_provider import VerifiedProvider


def test_no_repo_returns_fallback():
    p = VerifiedProvider(wiki_repository=None, wiki_serving_gate=None)
    r = p.serve("q", limit=5)
    assert r.eligible_claims == ()
    assert r.claim_pairs == ()
    assert r.fallback_reason == "no_wiki_repository"


def test_gate_exception_does_not_raise():
    repo = MagicMock()
    repo.list_claims.side_effect = RuntimeError("boom")
    p = VerifiedProvider(wiki_repository=repo, wiki_serving_gate=MagicMock())
    r = p.serve("q", limit=5)
    assert r.eligible_claims == ()
    assert r.fallback_reason is not None
    assert "boom" in (r.fallback_reason or "")


def test_filters_via_gate_not_raw_list():
    """Provider must call gate.filter_servable."""
    claim = MagicMock()
    claim.claim_id = "c1"
    claim.statement = "alpha beta"
    claim.normalized_statement = "alpha beta"
    claim.status = MagicMock(value="active")

    decision = MagicMock()
    decision.eligible = True
    decision.disclose_only = False
    decision.reason_codes = []
    decision.claim_id = "c1"

    gate = MagicMock()
    gate.filter_servable.return_value = [(claim, decision)]
    repo = MagicMock()
    repo.list_claims.return_value = [claim]

    p = VerifiedProvider(wiki_repository=repo, wiki_serving_gate=gate)
    r = p.serve("alpha", limit=5)

    gate.filter_servable.assert_called_once()
    kwargs = gate.filter_servable.call_args.kwargs
    assert kwargs.get("include_disclose") is True
    assert len(r.claim_pairs) == 1
    assert r.eligible_claims[0]["claim_id"] == "c1"
    assert r.fallback_reason is None


def test_disclose_only_split():
    claim = MagicMock()
    claim.claim_id = "c-d"
    claim.statement = "topic x"
    claim.normalized_statement = "topic x"
    claim.status = MagicMock(value="active")

    decision = MagicMock()
    decision.eligible = False
    decision.disclose_only = True
    decision.reason_codes = ["review_required"]
    decision.claim_id = "c-d"

    gate = MagicMock()
    gate.filter_servable.return_value = [(claim, decision)]
    repo = MagicMock()
    repo.list_claims.return_value = [claim]

    p = VerifiedProvider(wiki_repository=repo, wiki_serving_gate=gate)
    r = p.serve("topic", limit=5)
    assert r.eligible_claims == ()
    assert len(r.disclose_claims) == 1
    assert r.disclose_claims[0]["disclose_only"] is True
