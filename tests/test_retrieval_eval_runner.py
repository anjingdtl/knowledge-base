"""Tests for the offline retrieval eval runner (evals/run_retrieval_eval.py).

Tests the metric computation functions and the eval runner logic
with mocked/offline search results.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from evals.run_retrieval_eval import (
    OfflineIndex,
    RetrievalMetrics,
    aggregate_retrieval_metrics,
    build_index,
    compare_with_baseline,
    compute_mrr,
    compute_ndcg,
    compute_recall,
    run_single_query,
)

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

SAMPLE_RESULTS_HIT = [
    {"source_path": "architecture.md", "title": "architecture.md", "score": 0.9,
     "metadata": {"source_path": "architecture.md"}, "citation": {"path": "architecture.md"}},
    {"source_path": "api_guide.md", "title": "api_guide.md", "score": 0.7,
     "metadata": {"source_path": "api_guide.md"}, "citation": {"path": "api_guide.md"}},
    {"source_path": "troubleshooting.md", "title": "troubleshooting.md", "score": 0.5,
     "metadata": {"source_path": "troubleshooting.md"}, "citation": {"path": "troubleshooting.md"}},
]

SAMPLE_RESULTS_MISS = [
    {"source_path": "distractor.md", "title": "distractor.md", "score": 0.9,
     "metadata": {"source_path": "distractor.md"}, "citation": {"path": "distractor.md"}},
    {"source_path": "config_reference.md", "title": "config_reference.md", "score": 0.5,
     "metadata": {"source_path": "config_reference.md"}, "citation": {"path": "config_reference.md"}},
]


class TestComputeRecall:
    def test_perfect_recall(self):
        recall = compute_recall(SAMPLE_RESULTS_HIT, ["architecture.md"])
        assert recall == 1.0

    def test_miss(self):
        recall = compute_recall(SAMPLE_RESULTS_MISS, ["architecture.md"])
        assert recall == 0.0

    def test_multiple_expected(self):
        recall = compute_recall(
            SAMPLE_RESULTS_HIT,
            ["architecture.md", "api_guide.md"],
        )
        assert recall == 1.0

    def test_partial_match(self):
        recall = compute_recall(
            SAMPLE_RESULTS_HIT,
            ["architecture.md", "nonexistent.md"],
        )
        assert recall == 0.5

    def test_empty_expected(self):
        recall = compute_recall(SAMPLE_RESULTS_HIT, [])
        assert recall == 1.0

    def test_empty_results(self):
        recall = compute_recall([], ["architecture.md"])
        assert recall == 0.0


class TestComputeMRR:
    def test_first_rank(self):
        mrr = compute_mrr(SAMPLE_RESULTS_HIT, ["architecture.md"])
        assert mrr == 1.0

    def test_second_rank(self):
        mrr = compute_mrr(SAMPLE_RESULTS_HIT, ["api_guide.md"])
        assert mrr == pytest.approx(0.5)

    def test_third_rank(self):
        mrr = compute_mrr(SAMPLE_RESULTS_HIT, ["troubleshooting.md"])
        assert mrr == pytest.approx(1.0 / 3)

    def test_not_found(self):
        mrr = compute_mrr(SAMPLE_RESULTS_HIT, ["nonexistent.md"])
        assert mrr == 0.0

    def test_empty_expected(self):
        mrr = compute_mrr(SAMPLE_RESULTS_HIT, [])
        assert mrr == 1.0


class TestComputeNDCG:
    def test_perfect_ndcg(self):
        ndcg = compute_ndcg(SAMPLE_RESULTS_HIT, ["architecture.md"])
        assert ndcg == 1.0

    def test_miss_ndcg(self):
        ndcg = compute_ndcg(SAMPLE_RESULTS_MISS, ["architecture.md"])
        assert ndcg == 0.0

    def test_empty_expected(self):
        ndcg = compute_ndcg(SAMPLE_RESULTS_HIT, [])
        assert ndcg == 1.0

    def test_multiple_relevant(self):
        ndcg = compute_ndcg(
            SAMPLE_RESULTS_HIT,
            ["architecture.md", "api_guide.md"],
        )
        assert ndcg > 0.5


class TestOfflineIndex:
    def test_index_and_search(self, tmp_path):
        # Create a simple fixture
        fixture = tmp_path / "test.md"
        fixture.write_text(
            "# Test Document\n\n## Section A\n\nThis is about SQLite databases.\n\n"
            "## Section B\n\nThis is about vector embeddings.\n",
            encoding="utf-8",
        )

        index = OfflineIndex()
        index.index_fixture(fixture, fixture.read_text(encoding="utf-8"))

        assert len(index.documents) >= 2

        results = index.search("SQLite databases", top_k=5)
        assert len(results) > 0
        assert "SQLite" in results[0]["text"]

    def test_search_returns_scored_results(self, tmp_path):
        fixture = tmp_path / "doc.md"
        fixture.write_text(
            "# Doc\n\n## Heading\n\nBM25 scoring for relevance ranking.\n",
            encoding="utf-8",
        )

        index = OfflineIndex()
        index.index_fixture(fixture, fixture.read_text(encoding="utf-8"))

        results = index.search("BM25 scoring")
        assert len(results) > 0
        assert results[0]["score"] > 0

    def test_python_fixture_splitting(self, tmp_path):
        fixture = tmp_path / "code.py"
        fixture.write_text(
            'def hello():\n    """Say hello."""\n    print("hello")\n\n'
            'class World:\n    """The world."""\n    pass\n',
            encoding="utf-8",
        )

        index = OfflineIndex()
        index.index_fixture(fixture, fixture.read_text(encoding="utf-8"))

        # Should have at least 2 chunks (function + class)
        assert len(index.documents) >= 2


class TestBuildIndex:
    def test_build_index_from_fixtures(self):
        """Build index from the actual fixtures directory."""
        index = build_index(use_fake_embedding=True)
        assert len(index.documents) > 0
        # We have 6 fixture files
        sources = set(d["source_path"] for d in index.documents)
        assert len(sources) >= 6


class TestAggregateRetrievalMetrics:
    def test_basic_aggregation(self):
        query_results = [
            {"query": "q1", "category": "keyword", "recall": 1.0, "mrr": 1.0,
             "ndcg": 1.0, "latency_ms": 10.0, "must_not_violated": False,
             "no_answer_correct": True, "results_count": 5},
            {"query": "q2", "category": "keyword", "recall": 0.5, "mrr": 0.5,
             "ndcg": 0.7, "latency_ms": 20.0, "must_not_violated": False,
             "no_answer_correct": True, "results_count": 5},
        ]
        m = aggregate_retrieval_metrics(query_results)
        assert m.total_queries == 2
        assert m.recall_at_5 == pytest.approx(0.75)
        assert m.mrr == pytest.approx(0.75)

    def test_no_answer_metrics(self):
        query_results = [
            {"query": "q1", "category": "no_answer", "recall": 1.0, "mrr": 1.0,
             "ndcg": 1.0, "latency_ms": 5.0, "must_not_violated": False,
             "no_answer_correct": True, "results_count": 0},
            {"query": "q2", "category": "no_answer", "recall": 1.0, "mrr": 1.0,
             "ndcg": 1.0, "latency_ms": 3.0, "must_not_violated": False,
             "no_answer_correct": False, "results_count": 3},
        ]
        m = aggregate_retrieval_metrics(query_results)
        assert m.total_queries == 2
        assert m.no_answer_accuracy == pytest.approx(0.5)

    def test_empty_results(self):
        m = aggregate_retrieval_metrics([])
        assert m.total_queries == 0
        assert m.recall_at_5 == 0.0


class TestCompareWithBaseline:
    def test_no_baseline_file(self, tmp_path):
        m = RetrievalMetrics(recall_at_5=0.8, mrr=0.7)
        passed, warnings = compare_with_baseline(
            m, str(tmp_path / "nonexistent.json")
        )
        assert passed is True
        assert len(warnings) == 1

    def test_baseline_pass(self, tmp_path):
        baseline = {
            "metrics": {
                "recall_at_5": 0.7,
                "mrr": 0.6,
                "ndcg_at_10": 0.65,
                "no_answer_accuracy": 0.8,
            }
        }
        bp = tmp_path / "baseline.json"
        bp.write_text(json.dumps(baseline), encoding="utf-8")

        m = RetrievalMetrics(recall_at_5=0.75, mrr=0.65, ndcg_at_10=0.7, no_answer_accuracy=0.85)
        passed, warnings = compare_with_baseline(m, str(bp))
        assert passed is True

    def test_baseline_regression(self, tmp_path):
        baseline = {
            "metrics": {
                "recall_at_5": 0.9,
                "mrr": 0.8,
                "ndcg_at_10": 0.85,
                "no_answer_accuracy": 1.0,
            }
        }
        bp = tmp_path / "baseline.json"
        bp.write_text(json.dumps(baseline), encoding="utf-8")

        m = RetrievalMetrics(recall_at_5=0.5, mrr=0.3, ndcg_at_10=0.4, no_answer_accuracy=0.5)
        passed, warnings = compare_with_baseline(m, str(bp), max_regression=0.02)
        assert passed is False
        assert any("REGRESSION" in w for w in warnings)

    def test_baseline_zero_values(self, tmp_path):
        """When baseline has all zeros, no regression should be flagged."""
        baseline = {
            "metrics": {
                "recall_at_5": 0.0,
                "mrr": 0.0,
                "ndcg_at_10": 0.0,
                "no_answer_accuracy": 0.0,
            }
        }
        bp = tmp_path / "baseline.json"
        bp.write_text(json.dumps(baseline), encoding="utf-8")

        m = RetrievalMetrics(recall_at_5=0.0, mrr=0.0)
        passed, warnings = compare_with_baseline(m, str(bp))
        assert passed is True


class TestRunSingleQuery:
    def test_run_query_against_index(self):
        """Run a real query against the fixture index."""
        index = build_index(use_fake_embedding=True)

        item = {
            "query": "SQLite database",
            "expected_sources": [{"path": "fixtures/architecture.md", "block_contains": "SQLite"}],
            "must_not_match": [],
            "category": "keyword",
        }
        result = run_single_query(index, item)
        assert result["query"] == "SQLite database"
        assert result["category"] == "keyword"
        assert result["latency_ms"] >= 0
        assert "recall" in result
