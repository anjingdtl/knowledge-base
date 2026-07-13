"""Phase 7 hybrid eval harness tests."""
from __future__ import annotations

from evals.hybrid_eval.cases import build_hybrid_cases, category_counts
from evals.hybrid_eval.scoring import score_case, summarize_report
from evals.run_hybrid_eval import run


def test_case_count_at_least_150():
    cases = build_hybrid_cases()
    assert len(cases) >= 150
    counts = category_counts(cases)
    # Spec §14.2 core buckets present
    for key in (
        "single_fact",
        "zh_abbreviation",
        "cross_document",
        "concept_summary",
        "numeric_unit",
        "scope_condition",
        "conflict",
        "freshness_stale",
        "no_answer",
        "location_media",
    ):
        assert counts.get(key, 0) >= 10, key


def test_telecom_coverage():
    cases = build_hybrid_cases()
    assert sum(1 for c in cases if c.get("telecom")) >= 30


def test_conflict_case_discloses():
    cases = {c["id"]: c for c in build_hybrid_cases()}
    sc = score_case(cases["conflict_001"])
    assert sc.hybrid.conflict_disclosed
    assert sc.hybrid.answer_mode == "conflict_disclosure"


def test_stale_not_served_on_freshness_query():
    cases = {c["id"]: c for c in build_hybrid_cases()}
    sc = score_case(cases["stale_001"])
    assert not sc.hybrid.stale_served
    assert sc.hybrid.answer_mode in ("raw_only", "no_answer", "hybrid_verified")


def test_no_answer():
    cases = {c["id"]: c for c in build_hybrid_cases()}
    sc = score_case(cases["na_001"])
    assert sc.hybrid.answer_mode == "no_answer"


def test_full_eval_gates():
    report = run()
    assert report["total"] >= 150
    assert report["stale_serving_rate"] == 0.0
    assert report["unsupported_serving_rate"] == 0.0
    assert report["hybrid_correct"] + 1e-9 >= report["raw_correct"]
    assert report["citation_correctness"] >= 0.95
    assert report["conflict_detection_recall"] >= 0.90
    assert report["overall_pass"] is True


def test_summarize_tracks_failures():
    cases = build_hybrid_cases()[:5]
    scores = [score_case(c) for c in cases]
    rep = summarize_report(scores)
    assert rep.total == 5
    assert isinstance(rep.gates, dict)
