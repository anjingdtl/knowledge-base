"""Split isolation: development/regression vs holdout."""

from __future__ import annotations

from evals.hit_rate_v2.validation import (
    LEGACY_V1_EXPOSED_CASE_IDS,
    assert_split_isolation,
)


def test_v1_exposed_cannot_enter_holdout():
    rows = [
        {"case_id": "KB-001", "split": "holdout"},
        {"case_id": "NEW-001", "split": "holdout"},
    ]
    errors = assert_split_isolation(rows)
    assert any("holdout_contains_v1_exposed:KB-001" in e for e in errors)


def test_development_and_holdout_must_not_overlap():
    rows = [
        {"case_id": "X-1", "split": "development"},
        {"case_id": "X-1", "split": "holdout"},
    ]
    errors = assert_split_isolation(rows)
    assert any("development_holdout_overlap:X-1" in e for e in errors)


def test_v1_exposed_ids_cover_kb_001_to_037():
    assert "KB-001" in LEGACY_V1_EXPOSED_CASE_IDS
    assert "KB-037" in LEGACY_V1_EXPOSED_CASE_IDS
    assert "KB-038" not in LEGACY_V1_EXPOSED_CASE_IDS


def test_clean_splits_pass():
    rows = [
        {"case_id": "KB-001", "split": "development"},
        {"case_id": "KB-032", "split": "regression"},
        {"case_id": "HOLD-001", "split": "holdout"},
    ]
    assert assert_split_isolation(rows) == []
