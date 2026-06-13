"""RAG 评测指标计算

指标体系:
- recall_at_k: 检索前 K 个结果中包含正确来源的比例
- mrr: 正确来源排名倒数均值 (Mean Reciprocal Rank)
- citation_accuracy: 引用是否正确指向来源
- faithfulness: 回答是否基于知识库内容（非编造）
- no_answer_accuracy: 证据不足时能否正确拒答
- latency 统计: P50 / P95 响应延迟
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class EvalMetrics:
    """单次评测的汇总指标"""

    # 召回率
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0

    # 排名质量
    mrr: float = 0.0
    ndcg_at_10: float = 0.0

    # 答案质量
    citation_accuracy: float = 0.0
    faithfulness: float = 0.0

    # 拒答准确率
    no_answer_accuracy: float = 0.0

    # 延迟（秒）
    latency_p50: float = 0.0
    latency_p95: float = 0.0
    latency_mean: float = 0.0

    # 统计
    total_questions: int = 0
    total_answered: int = 0
    total_refused: int = 0


@dataclass
class SingleResult:
    """单条评测结果"""

    question: str
    answer: str = ""
    sources: list[dict] = field(default_factory=list)
    relevant_knowledge_ids: list[str] = field(default_factory=list)
    latency: float = 0.0
    route: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def is_refused(self) -> bool:
        """是否为拒答"""
        refusal_keywords = [
            "未找到", "没有找到", "无法回答", "证据不足", "知识库中未",
            "未收录", "无法提供", "抱歉", "不知道", "不确定",
            "not found", "no relevant", "cannot answer",
        ]
        text = self.answer.lower()
        return any(kw in text for kw in refusal_keywords)

    @property
    def source_ids(self) -> set[str]:
        """提取来源中的所有 knowledge_id"""
        ids = set()
        for s in self.sources:
            kid = s.get("knowledge_id", "")
            if kid:
                ids.add(kid)
        return ids


def compute_recall_at_k(
    results: Sequence[SingleResult],
    k: int,
) -> float:
    """计算 Recall@K

    对于每条结果，检查前 K 个来源中是否包含预期知识 ID。
    如果 expected_ids 为空则跳过该条（不计入分母）。
    """
    hits = 0
    total = 0
    for r in results:
        if not r.relevant_knowledge_ids:
            continue
        total += 1
        top_k_ids = set()
        for s in r.sources[:k]:
            kid = s.get("knowledge_id", "")
            if kid:
                top_k_ids.add(kid)
        if top_k_ids & set(r.relevant_knowledge_ids):
            hits += 1
    return hits / total if total > 0 else 0.0


def compute_mrr(results: Sequence[SingleResult]) -> float:
    """计算 MRR (Mean Reciprocal Rank)

    对于每条结果，找到第一个正确来源的排名倒数。
    """
    reciprocal_ranks = []
    for r in results:
        if not r.relevant_knowledge_ids:
            continue
        expected = set(r.relevant_knowledge_ids)
        for rank, s in enumerate(r.sources, start=1):
            kid = s.get("knowledge_id", "")
            if kid in expected:
                reciprocal_ranks.append(1.0 / rank)
                break
        else:
            reciprocal_ranks.append(0.0)
    return statistics.mean(reciprocal_ranks) if reciprocal_ranks else 0.0


def compute_citation_accuracy(results: Sequence[SingleResult]) -> float:
    """评估引用准确率 — 回答中提到的来源是否确实在 sources 列表中

    简化版：检查 sources 是否非空且 answer 非空。
    如果有 relevant_knowledge_ids，则检查是否命中。
    """
    if not results:
        return 0.0
    scored = 0
    for r in results:
        if not r.answer or r.is_refused:
            continue
        if not r.relevant_knowledge_ids:
            # 无预期 ID 时，只要有来源就算合理
            scored += 1 if r.sources else 0
            continue
        # 有预期 ID 时，检查是否命中
        scored += 1 if r.source_ids & set(r.relevant_knowledge_ids) else 0
    total = sum(1 for r in results if r.answer and not r.is_refused)
    return scored / total if total > 0 else 0.0


def compute_faithfulness(results: Sequence[SingleResult]) -> float:
    """评估忠实度 — 回答是否基于检索到的来源

    简化版：有来源的回答 / 所有非空回答。
    完整版需要 LLM 评判（可选）。
    """
    if not results:
        return 0.0
    total = 0
    faithful = 0
    for r in results:
        if not r.answer or r.is_refused:
            continue
        total += 1
        # 有来源且有回答 → 基本可信
        if r.sources:
            faithful += 1
    return faithful / total if total > 0 else 0.0


def compute_no_answer_accuracy(
    results: Sequence[SingleResult],
    expect_no_answer: bool = True,
) -> float:
    """评估拒答准确率

    对于 no_answer 数据集，预期系统应拒答。
    accuracy = 正确拒答数 / 总问题数
    """
    if not results:
        return 0.0
    correct = 0
    for r in results:
        if expect_no_answer:
            correct += 1 if r.is_refused else 0
        else:
            correct += 1 if not r.is_refused else 0
    return correct / len(results)


def compute_ndcg_at_k(
    results: Sequence[SingleResult],
    k: int = 10,
) -> float:
    """计算 nDCG@K (normalized Discounted Cumulative Gain)

    对每条结果，计算前 K 个来源的二元相关性 (binary relevance):
    命中预期 knowledge_id 则为 1，否则为 0。
    """
    ndcg_scores = []
    for r in results:
        if not r.relevant_knowledge_ids:
            continue
        expected = set(r.relevant_knowledge_ids)
        relevances = []
        for s in r.sources[:k]:
            kid = s.get("knowledge_id", "")
            relevances.append(1.0 if kid in expected else 0.0)

        dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances))
        n_rel = min(len(expected), k)
        ideal_rels = [1.0] * n_rel + [0.0] * (k - n_rel)
        idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal_rels[:k]))
        ndcg_scores.append(dcg / idcg if idcg > 0 else 0.0)

    return statistics.mean(ndcg_scores) if ndcg_scores else 0.0


def compute_recall_at_5(results: Sequence[SingleResult]) -> float:
    """Convenience wrapper: compute Recall@5."""
    return compute_recall_at_k(results, k=5)


def compute_latency_stats(results: Sequence[SingleResult]) -> tuple[float, float, float]:
    """计算延迟统计: (P50, P95, Mean)"""
    latencies = sorted(r.latency for r in results if r.latency > 0)
    if not latencies:
        return 0.0, 0.0, 0.0

    n = len(latencies)
    p50_idx = max(0, min(n - 1, int(math.ceil(n * 0.5)) - 1))
    p95_idx = max(0, min(n - 1, int(math.ceil(n * 0.95)) - 1))

    return latencies[p50_idx], latencies[p95_idx], statistics.mean(latencies)


def aggregate_metrics(results: Sequence[SingleResult], dataset_type: str = "basic") -> EvalMetrics:
    """从单条结果聚合计算全部指标

    Args:
        results: 评测结果列表
        dataset_type: 数据集类型 (basic | table | graph | no_answer)
    """
    metrics = EvalMetrics()
    metrics.total_questions = len(results)
    metrics.total_answered = sum(1 for r in results if r.answer and not r.is_refused)
    metrics.total_refused = sum(1 for r in results if r.is_refused)

    # 通用指标
    metrics.recall_at_5 = compute_recall_at_k(results, 5)
    metrics.recall_at_10 = compute_recall_at_k(results, 10)
    metrics.mrr = compute_mrr(results)
    metrics.ndcg_at_10 = compute_ndcg_at_k(results, k=10)
    metrics.citation_accuracy = compute_citation_accuracy(results)
    metrics.faithfulness = compute_faithfulness(results)

    # 延迟
    p50, p95, mean = compute_latency_stats(results)
    metrics.latency_p50 = p50
    metrics.latency_p95 = p95
    metrics.latency_mean = mean

    # no_answer 专用指标
    if dataset_type == "no_answer":
        metrics.no_answer_accuracy = compute_no_answer_accuracy(results, expect_no_answer=True)

    return metrics
