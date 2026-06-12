"""测试 RAG 评测指标计算"""
import pytest
from evals.metrics import (
    SingleResult, EvalMetrics,
    compute_recall_at_k, compute_mrr, compute_citation_accuracy,
    compute_faithfulness, compute_no_answer_accuracy,
    compute_latency_stats, aggregate_metrics,
)


# ---- 测试数据 ----

SAMPLE_RESULTS = [
    SingleResult(
        question="Q1",
        answer="基于来源回答",
        sources=[
            {"knowledge_id": "k1", "title": "doc1", "score": 0.9},
            {"knowledge_id": "k2", "title": "doc2", "score": 0.8},
        ],
        relevant_knowledge_ids=["k1"],
        latency=1.2,
    ),
    SingleResult(
        question="Q2",
        answer="另一个回答",
        sources=[
            {"knowledge_id": "k3", "title": "doc3", "score": 0.7},
        ],
        relevant_knowledge_ids=["k3"],
        latency=2.5,
    ),
    SingleResult(
        question="Q3",
        answer="未找到相关信息",
        sources=[],
        relevant_knowledge_ids=["k4"],
        latency=0.8,
    ),
]


class TestRecallAtK:
    def test_perfect_recall(self):
        r = SingleResult(
            question="Q", answer="A",
            sources=[{"knowledge_id": "k1"}],
            relevant_knowledge_ids=["k1"],
        )
        assert compute_recall_at_k([r], k=5) == 1.0

    def test_miss(self):
        r = SingleResult(
            question="Q", answer="A",
            sources=[{"knowledge_id": "k2"}],
            relevant_knowledge_ids=["k1"],
        )
        assert compute_recall_at_k([r], k=5) == 0.0

    def test_empty_relevant_ids_skipped(self):
        r = SingleResult(
            question="Q", answer="A",
            sources=[{"knowledge_id": "k1"}],
            relevant_knowledge_ids=[],
        )
        assert compute_recall_at_k([r], k=5) == 0.0

    def test_mixed(self):
        assert compute_recall_at_k(SAMPLE_RESULTS, k=5) == pytest.approx(2/3, abs=0.01)

    def test_recall_at_1(self):
        """Recall@1: 只有第一条的第一个来源被考虑"""
        r = SingleResult(
            question="Q", answer="A",
            sources=[{"knowledge_id": "k2"}, {"knowledge_id": "k1"}],
            relevant_knowledge_ids=["k1"],
        )
        assert compute_recall_at_k([r], k=1) == 0.0

    def test_empty_results(self):
        assert compute_recall_at_k([], k=5) == 0.0


class TestMRR:
    def test_perfect_rank(self):
        r = SingleResult(
            question="Q", answer="A",
            sources=[{"knowledge_id": "k1"}],
            relevant_knowledge_ids=["k1"],
        )
        assert compute_mrr([r]) == 1.0

    def test_second_rank(self):
        r = SingleResult(
            question="Q", answer="A",
            sources=[{"knowledge_id": "k2"}, {"knowledge_id": "k1"}],
            relevant_knowledge_ids=["k1"],
        )
        assert compute_mrr([r]) == pytest.approx(0.5)

    def test_not_found(self):
        r = SingleResult(
            question="Q", answer="A",
            sources=[{"knowledge_id": "k2"}],
            relevant_knowledge_ids=["k1"],
        )
        assert compute_mrr([r]) == 0.0

    def test_empty_relevant_skipped(self):
        r = SingleResult(
            question="Q", answer="A",
            sources=[{"knowledge_id": "k1"}],
            relevant_knowledge_ids=[],
        )
        assert compute_mrr([r]) == 0.0


class TestCitationAccuracy:
    def test_with_citations(self):
        r = SingleResult(
            question="Q", answer="回答",
            sources=[{"knowledge_id": "k1"}],
            relevant_knowledge_ids=["k1"],
        )
        assert compute_citation_accuracy([r]) == 1.0

    def test_no_sources(self):
        r = SingleResult(
            question="Q", answer="回答",
            sources=[],
        )
        assert compute_citation_accuracy([r]) == 0.0

    def test_refused_not_counted(self):
        r = SingleResult(
            question="Q", answer="抱歉，未找到相关信息",
            sources=[],
        )
        assert compute_citation_accuracy([r]) == 0.0


class TestFaithfulness:
    def test_has_sources(self):
        r = SingleResult(
            question="Q", answer="回答",
            sources=[{"knowledge_id": "k1"}],
        )
        assert compute_faithfulness([r]) == 1.0

    def test_no_sources(self):
        r = SingleResult(
            question="Q", answer="回答",
            sources=[],
        )
        assert compute_faithfulness([r]) == 0.0


class TestNoAnswerAccuracy:
    def test_correct_refusal(self):
        r = SingleResult(
            question="Q", answer="抱歉，知识库中未找到相关信息",
        )
        assert compute_no_answer_accuracy([r], expect_no_answer=True) == 1.0

    def test_incorrect_answer(self):
        r = SingleResult(
            question="Q", answer="答案是42",
        )
        assert compute_no_answer_accuracy([r], expect_no_answer=True) == 0.0

    def test_normal_answer_when_expected(self):
        r = SingleResult(
            question="Q", answer="答案是42",
        )
        assert compute_no_answer_accuracy([r], expect_no_answer=False) == 1.0


class TestLatencyStats:
    def test_basic(self):
        p50, p95, mean = compute_latency_stats(SAMPLE_RESULTS)
        assert p50 > 0
        assert p95 >= p50
        assert mean > 0

    def test_empty(self):
        p50, p95, mean = compute_latency_stats([])
        assert p50 == 0.0
        assert p95 == 0.0
        assert mean == 0.0

    def test_single(self):
        r = [SingleResult(question="Q", latency=3.0)]
        p50, p95, mean = compute_latency_stats(r)
        assert p50 == 3.0
        assert p95 == 3.0
        assert mean == 3.0


class TestSingleResultProperties:
    def test_is_refused(self):
        r = SingleResult(question="Q", answer="抱歉，知识库中未找到相关信息")
        assert r.is_refused is True

    def test_is_not_refused(self):
        r = SingleResult(question="Q", answer="这是正确答案")
        assert r.is_refused is False

    def test_source_ids(self):
        r = SingleResult(
            question="Q",
            sources=[
                {"knowledge_id": "k1"},
                {"knowledge_id": "k2"},
            ],
        )
        assert r.source_ids == {"k1", "k2"}


class TestAggregateMetrics:
    def test_basic_dataset(self):
        metrics = aggregate_metrics(SAMPLE_RESULTS, dataset_type="basic")
        assert metrics.total_questions == 3
        assert metrics.total_answered == 2
        assert metrics.total_refused == 1
        assert metrics.recall_at_5 > 0
        assert metrics.mrr > 0

    def test_no_answer_dataset(self):
        results = [
            SingleResult(question="Q1", answer="抱歉，未找到"),
            SingleResult(question="Q2", answer="答案是42"),
        ]
        metrics = aggregate_metrics(results, dataset_type="no_answer")
        assert metrics.total_questions == 2
        assert metrics.total_refused == 1
        assert metrics.no_answer_accuracy == pytest.approx(0.5)
