"""Retrieval internal contract tests."""
from dataclasses import FrozenInstanceError

import pytest

from src.retrieval.execution import SearchExecution
from src.retrieval.models import RawRetrievalResult, VerifiedServingResult


def test_raw_result_frozen():
    r = RawRetrievalResult(candidates=({"id": "a"},), trace={"mode": "raw"})
    with pytest.raises(FrozenInstanceError):
        r.candidates = ()  # type: ignore[misc]


def test_verified_result_defaults():
    v = VerifiedServingResult(eligible_claims=(), disclose_claims=())
    assert v.conflicts == ()
    assert v.fallback_reason is None
    assert v.warnings == ()
    assert v.claim_pairs == ()


def test_search_execution_reexport_is_same_type():
    from src.models.search_execution import SearchExecution as Canonical

    assert SearchExecution is Canonical


def test_build_execution_assembles_tuples():
    from src.retrieval.execution import build_execution

    ex = build_execution(
        [{"source": "knowledge"}],
        trace={"mode": "t"},
        warnings=["w"],
    )
    assert isinstance(ex.results, tuple)
    assert ex.trace["mode"] == "t"
    assert ex.warnings == ("w",)
