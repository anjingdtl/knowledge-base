"""CandidatePoolPolicy unit tests (ADR §5)."""
from __future__ import annotations

import pytest

from src.retrieval.candidate_pool import CandidatePoolPolicy


def test_from_request_default_floor():
    policy = CandidatePoolPolicy.from_request(5)
    assert policy.public_top_k == 5
    assert policy.fetch_k == 20  # max(5*4, 20)
    assert policy.rerank_top_k == 5
    assert policy.final_top_k == 5
    assert policy.max_per_document == 3


def test_from_request_large_top_k_uses_multiplier():
    policy = CandidatePoolPolicy.from_request(10)
    assert policy.public_top_k == 10
    assert policy.fetch_k == 40  # max(10*4, 20)
    assert policy.rerank_top_k == 10
    assert policy.final_top_k == 10


def test_from_request_clamps_zero_to_one():
    policy = CandidatePoolPolicy.from_request(0)
    assert policy.public_top_k == 1
    assert policy.fetch_k >= 1


def test_fetch_k_floor_config_raises_only():
    policy = CandidatePoolPolicy.from_request(
        5,
        config={"rag": {"retrieval": {"fetch_k_floor": 50}}},
    )
    assert policy.fetch_k == 50  # floor raises above default 20

    policy_low = CandidatePoolPolicy.from_request(
        5,
        config={"rag": {"retrieval": {"fetch_k_floor": 5}}},
    )
    # Floor below multiplier/default must not shrink the pool.
    assert policy_low.fetch_k == 20


def test_fetch_k_floor_via_config_get():
    class Cfg:
        def get(self, key, default=None):
            if key == "rag.retrieval.fetch_k_floor":
                return 30
            return default

    policy = CandidatePoolPolicy.from_request(5, config=Cfg())
    assert policy.fetch_k == 30


def test_invalid_construction_rejected():
    with pytest.raises(ValueError):
        CandidatePoolPolicy(
            public_top_k=0,
            fetch_k=20,
            rerank_top_k=5,
            final_top_k=5,
        )
    with pytest.raises(ValueError):
        CandidatePoolPolicy(
            public_top_k=5,
            fetch_k=3,  # < public_top_k
            rerank_top_k=5,
            final_top_k=5,
        )


def test_to_trace_is_deterministic():
    policy = CandidatePoolPolicy.from_request(5)
    trace = policy.to_trace()
    assert trace == {
        "public_top_k": 5,
        "fetch_k": 20,
        "rerank_top_k": 5,
        "final_top_k": 5,
        "max_per_document": 3,
    }
    assert "ms" not in trace
