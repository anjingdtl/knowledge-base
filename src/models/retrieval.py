"""统一检索候选模型 — 归一化向量、FTS、RRF、Reranker 分数"""
from __future__ import annotations

from typing import TypedDict


class RetrievalCandidate(TypedDict, total=False):
    block_id: str
    knowledge_id: str
    text: str
    metadata: dict
    vector_score: float | None
    keyword_score: float | None
    rrf_score: float | None
    rerank_score: float | None
    final_score: float
    match_channels: list[str]
    warnings: list[str]


# ---- 分数归一化辅助函数 ----

def normalize_vector_score(distance: float) -> float:
    """将余弦距离转换为 0-1 相似度。

    sqlite-vec 返回 L2 风格的距离: 0=完全相同, 2=完全相反。
    公式: max(0, 1 - distance / 2)
    """
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, 1.0 - d / 2.0)


def normalize_fts_score(rank: float) -> float:
    """将 FTS5 rank（负 BM25）转换为 0-1 分数。

    FTS5 rank 为负数（越小越相关）。
    公式: abs(rank) / (abs(rank) + 10)  （rank < 0 时）
    """
    try:
        r = float(rank)
    except (TypeError, ValueError):
        return 0.0
    if r < 0:
        strength = abs(r)
        return strength / (strength + 10.0)
    return 0.0


def compute_final_score(candidate: dict) -> float:
    """计算最终分数。优先级: rerank_score > rrf_score > max(vector_score, keyword_score)"""
    rerank = candidate.get("rerank_score")
    if rerank is not None:
        return float(rerank)

    rrf = candidate.get("rrf_score")
    if rrf is not None:
        return float(rrf)

    vec = candidate.get("vector_score")
    kw = candidate.get("keyword_score")
    scores = [float(s) for s in (vec, kw) if s is not None]
    if scores:
        return max(scores)

    # Fallback: distance (legacy)
    distance = candidate.get("distance")
    if distance is not None:
        return float(distance)

    return 0.0


def build_match_channels(candidate: dict) -> list[str]:
    """根据非 None/非零分数构建 match_channels 列表。"""
    channels: list[str] = []
    vec = candidate.get("vector_score")
    if vec is not None and vec > 0:
        channels.append("semantic")
    # Also check distance (legacy vector score indicator)
    if "semantic" not in channels:
        dist = candidate.get("distance")
        if dist is not None and float(dist) > 0:
            channels.append("semantic")

    kw = candidate.get("keyword_score")
    if kw is not None and kw > 0:
        channels.append("keyword")
    # Also check fts_rank (legacy keyword score indicator)
    if "keyword" not in channels:
        fts = candidate.get("fts_rank")
        if fts is not None and float(fts) != 0:
            channels.append("keyword")

    return channels


def build_match_reason(candidate: dict, reranked: bool = False, expanded: bool = False) -> str:
    """构建人类可读的匹配原因描述。

    例如: "semantic + keyword match; reranked"
    """
    channels = candidate.get("match_channels") or build_match_channels(candidate)
    parts = []
    if "semantic" in channels:
        parts.append("semantic")
    if "keyword" in channels:
        parts.append("keyword")

    if parts:
        reason = " + ".join(parts) + " match"
    else:
        reason = "match"

    suffixes = []
    if reranked:
        suffixes.append("reranked")
    if expanded:
        suffixes.append("context expanded")
    if suffixes:
        reason += "; " + ", ".join(suffixes)

    return reason
