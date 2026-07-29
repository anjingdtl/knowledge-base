"""Document-level retrieval hit deduplication and light re-ranking."""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

_CJK = re.compile(r"[\u4e00-\u9fff]{2,}")
_LATIN = re.compile(r"[A-Za-z0-9]{2,}")


def _score_of(item: dict[str, Any], score_keys: Sequence[str]) -> float:
    for key in score_keys:
        raw = item.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


def dedupe_by_knowledge_id(
    items: Iterable[dict[str, Any]],
    *,
    score_keys: Sequence[str] = ("score", "fts_score", "similarity"),
) -> list[dict[str, Any]]:
    """Keep one hit per non-empty knowledge_id (highest score).

    Hits without knowledge_id are kept individually (cannot merge safely).
    Winner order follows first occurrence of each knowledge_id in the input.

    Prefer :func:`dedupe_retrieval_hits` when passages are the retrieval unit.
    """
    rows = list(items)
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    orphan: list[dict[str, Any]] = []

    for item in rows:
        kid = str(item.get("knowledge_id") or item.get("page_id") or "").strip()
        if not kid:
            orphan.append(item)
            continue
        score = _score_of(item, score_keys)
        prev = best.get(kid)
        if prev is None:
            best[kid] = item
            order.append(kid)
            continue
        if score > _score_of(prev, score_keys):
            best[kid] = item

    return [best[k] for k in order] + orphan


def dedupe_retrieval_hits(
    items: Iterable[dict[str, Any]],
    *,
    score_keys: Sequence[str] = (
        "final_relevance_score",
        "ranking_score",
        "score",
        "fts_score",
        "similarity",
    ),
    max_passages_per_knowledge: int = 3,
) -> list[dict[str, Any]]:
    """Dedupe preserving multi-passage diversity within a document (SPEC v5).

    - Passage hits (non-empty ``passage_id``): unique by passage_id; keep up to
      ``max_passages_per_knowledge`` highest-scoring passages per knowledge_id.
    - Block-only hits: fall back to one hit per knowledge_id.
    """
    rows = [dict(x) if isinstance(x, dict) else x for x in items]
    passage_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("passage_id") or "").strip()
        unit = str(item.get("retrieval_unit") or "").strip()
        ctype = str(item.get("candidate_type") or "").strip()
        if pid or unit == "passage" or ctype == "passage":
            passage_rows.append(item)
        else:
            block_rows.append(item)

    # Passage path: unique by passage_id, then cap per knowledge_id.
    by_pid: dict[str, dict[str, Any]] = {}
    pid_order: list[str] = []
    no_pid: list[dict[str, Any]] = []
    for item in passage_rows:
        pid = str(item.get("passage_id") or item.get("id") or "").strip()
        if not pid:
            no_pid.append(item)
            continue
        score = _score_of(item, score_keys)
        prev = by_pid.get(pid)
        if prev is None:
            by_pid[pid] = item
            pid_order.append(pid)
        elif score > _score_of(prev, score_keys):
            by_pid[pid] = item

    # Group by knowledge_id, keep top-N per doc by score.
    by_kid: dict[str, list[dict[str, Any]]] = {}
    kid_order: list[str] = []
    for pid in pid_order:
        item = by_pid[pid]
        kid = str(item.get("knowledge_id") or item.get("page_id") or "").strip() or f"__pid__{pid}"
        if kid not in by_kid:
            by_kid[kid] = []
            kid_order.append(kid)
        by_kid[kid].append(item)

    selected: list[dict[str, Any]] = []
    for kid in kid_order:
        group = sorted(
            by_kid[kid],
            key=lambda x: _score_of(x, score_keys),
            reverse=True,
        )
        selected.extend(group[: max(1, int(max_passages_per_knowledge))])

    # Global re-sort by score while roughly preserving doc diversity order.
    selected.sort(key=lambda x: _score_of(x, score_keys), reverse=True)

    # Block fallback still knowledge-deduped.
    blocks = dedupe_by_knowledge_id(block_rows, score_keys=score_keys)
    # Prefer passages first.
    out = selected + no_pid + blocks
    # Final unique by (passage_id or knowledge_id+block_id)
    seen: set[str] = set()
    final: list[dict[str, Any]] = []
    for item in out:
        pid = str(item.get("passage_id") or "").strip()
        kid = str(item.get("knowledge_id") or "").strip()
        bid = str(item.get("block_id") or "").strip()
        key = pid or f"{kid}:{bid}" or str(id(item))
        if key in seen:
            continue
        seen.add(key)
        final.append(item)
    return final


def _query_terms(query: str) -> set[str]:
    terms: set[str] = set()
    for run in _CJK.findall(query or ""):
        if len(run) <= 4:
            terms.add(run)
        else:
            for n in (4, 3, 2):
                for i in range(len(run) - n + 1):
                    terms.add(run[i : i + n])
    terms |= {t.lower() for t in _LATIN.findall(query or "")}
    stop = {"什么", "哪些", "怎么", "如何", "所有", "全部", "主题", "内容", "关于"}
    return {t for t in terms if t not in stop and len(t) >= 2}


def boost_title_term_overlap(
    query: str,
    items: list[dict[str, Any]],
    *,
    weight: float = 0.12,
) -> list[dict[str, Any]]:
    """Boost items whose title shares multi-char query terms (Precision aid)."""
    terms = _query_terms(query)
    if not terms or not items:
        return items
    for item in items:
        title = str(item.get("title") or "")
        if not title:
            continue
        hits = sum(1 for t in terms if t in title)
        if hits <= 0:
            continue
        base = _score_of(item, ("score", "fts_score", "similarity"))
        item["score"] = min(1.0, base + weight * min(hits, 4))
        if "fts_score" in item:
            item["fts_score"] = item["score"]
    items.sort(
        key=lambda x: _score_of(x, ("score", "fts_score", "similarity")),
        reverse=True,
    )
    return items
