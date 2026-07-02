"""blend 档 RRF 融合 —— wiki 候选 × 检索候选(第二阶段 W1 Task 1.3)。

规模自适应 blend 档需把 WikiReadStage 的 wiki 候选与 VectorSearchStage 的 hybrid
检索候选融合成统一候选列表。现有 ``hybrid_search._blend_search`` 的 RRF 内联在
向量×关键词两路融合里,且候选 id 体系不同,无法直接复用;本模块提供独立的
wiki×search 两路 RRF 融合。

RRF 公式(复用 hybrid_search 常数 k=40):``score = w / (k + rank + 1)``。
两路各自按其在各自列表中的 rank 计分,同 id 累加,``match_channels`` 取并集。
"""
from __future__ import annotations

from typing import Any

_DEFAULT_K = 40


def blend_fusion(
    wiki_candidates: list[dict],
    search_candidates: list[dict],
    *,
    w_wiki: float = 0.5,
    w_search: float = 0.5,
    k: int = _DEFAULT_K,
    top_n: int | None = None,
) -> list[dict]:
    """RRF 融合 wiki 候选与检索候选。

    Args:
        wiki_candidates: WikiReadStage 产出的 wiki 页候选(id 形如
            ``wiki:<page_type>:<slug>``)。
        search_candidates: hybrid_search 产出的检索候选(id 形如
            ``page_id:block_id``)。两路 id 体系不冲突,直接用候选 ``id`` 字段。
        w_wiki / w_search: 两路 RRF 权重(默认各 0.5)。
        k: RRF 常数(默认 40,与 ``hybrid_search._blend_search`` 一致)。
        top_n: 截断数;``None`` 不截断。

    Returns:
        统一 schema 候选列表(按 ``rrf_score`` 降序),每项含
        ``id / text / metadata / match_channels / rrf_score / final_score``。
    """
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    channels: dict[str, set[str]] = {}

    _accumulate(wiki_candidates, w_wiki, k, scores, items, channels)
    _accumulate(search_candidates, w_search, k, scores, items, channels)

    for cid, item in items.items():
        score = scores[cid]
        item["rrf_score"] = round(score, 6)
        item["final_score"] = score
        item["match_channels"] = sorted(channels[cid])

    ranked = sorted(items.values(), key=lambda x: x["rrf_score"], reverse=True)
    if top_n is not None:
        ranked = ranked[:top_n]
    return ranked


def _accumulate(
    candidates: list[dict],
    weight: float,
    k: int,
    scores: dict[str, float],
    items: dict[str, dict],
    channels: dict[str, set[str]],
) -> None:
    """单路候选按 rank 累加 RRF 分,登记候选与 match_channels。"""
    for rank, cand in enumerate(candidates):
        cid = str(cand.get("id", ""))
        if not cid:
            continue
        scores[cid] = scores.get(cid, 0.0) + weight / (k + rank + 1)
        if cid not in items:
            items[cid] = _clone(cand, cid)
        channels.setdefault(cid, set()).update(cand.get("match_channels") or [])


def _clone(cand: dict, cid: str) -> dict:
    """浅拷贝候选并补齐统一 schema 必需字段(不污染原候选)。"""
    cloned: dict[str, Any] = dict(cand)
    cloned["id"] = cid
    cloned.setdefault("text", "")
    cloned.setdefault("metadata", {})
    return cloned
