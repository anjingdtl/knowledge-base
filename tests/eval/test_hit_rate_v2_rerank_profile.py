"""Rerank profile honesty tests (Phase 0 Task 0.4)."""

from __future__ import annotations

import pytest

from evals.hit_rate_v2.rerank_profiles import (
    redacted_probe_log,
    resolve_rerank_profile,
)


def test_deterministic_baseline_never_claims_provider_equivalence():
    st = resolve_rerank_profile("deterministic-baseline")
    assert st.effective_profile == "deterministic-baseline"
    assert st.blocked is False
    assert any("does not prove" in n for n in st.notes)


def test_provider_enhanced_unavailable_is_blocked_not_normal():
    st = resolve_rerank_profile(
        "provider-enhanced",
        provider_available=False,
        probe_error="timeout",
    )
    assert st.blocked is True
    assert st.track_status == "blocked"
    assert st.effective_profile == "blocked"
    assert st.effective_profile != "deterministic-baseline"
    assert "normal" not in st.effective_profile


def test_invalid_profile_rejected():
    with pytest.raises(ValueError):
        resolve_rerank_profile("normal")


def test_probe_log_redacts_secrets():
    msg = "Authorization: Bearer sk-abcdefghijklmnopqrst Authorization: secret"
    out = redacted_probe_log(msg)
    assert "sk-abcdefghijklmnopqrst" not in out
    assert "[REDACTED]" in out
