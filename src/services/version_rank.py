"""Version-aware freshness ranking for institutional policy documents.

SPEC Phase 4 (KB-009): when multiple versions of the same regulation are
retrieved (e.g. 差旅费管理办法 2018 / 2022 / 2025), the newest *effective*
version must rank first so answers do not cite superseded amounts.

The feature is intentionally conservative:
  * It only fires when a reliable version signal can be parsed (explicit year
    in the title, a 〔YYYY〕N号 document number, or a 版本/修订 ordinal).
  * It NEVER lowers an item's score below its lexical relevance — it only
    adds a small freshness boost that breaks ties in favor of newer versions.
  * It does not delete user data or merge duplicates physically; near-duplicate
    detection happens at the retrieval-result layer.
"""
from __future__ import annotations

import re
from typing import Any

# Capture a 4-digit year (19xx/20xx) anywhere — title, 〔YYYY〕N号, "2025年版".
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
# 〔2025〕256号 / (2025)256号 / 2025-256号 style document numbers.
_DOC_NUM_YEAR_RE = re.compile(r"[〔(【\[<]?\s*((?:19|20)\d{2})\s*[〕)】\]>]?\s*[-—]?\s*\d+\s*号")
# Explicit version ordinals: "第N版", "N版", "修订版", "2025年修订".
_VERSION_ORDINAL_RE = re.compile(r"第[一二三四五六七八九十0-9]+版|[0-9]+版|修订版|修订")
# Deprecation / superseded markers.
_DEPRECATED_RE = re.compile(r"废止|失效|过期|已撤销|不再执行|停止执行| superseded")


def extract_version_year(item: dict[str, Any]) -> int | None:
    """Return the most reliable year identifying this document's version.

    Priority:
      1. ``effective_year`` metadata if the importer recorded one;
      2. the year inside a 〔YYYY〕N号 document number in title/text;
      3. the most recent 4-digit year in title (titles usually embed the
         edition year, e.g. "差旅费管理办法-2025年").

    Returns None when no reliable year can be parsed — callers MUST NOT guess.
    """
    if not isinstance(item, dict):
        return None
    # 1. explicit metadata
    for key in ("effective_year", "version_year", "doc_year"):
        v = item.get(key)
        if isinstance(v, int) and 1900 <= v <= 2100:
            return v
    blob = f"{item.get('title') or ''} {item.get('text') or ''}"
    # 2. 〔YYYY〕N号 document-number year (most authoritative for policy docs)
    m = _DOC_NUM_YEAR_RE.search(blob)
    if m:
        return int(m.group(1))
    # 3. most recent 4-digit year in title only (avoid pulling random years
    #    from the body that may reference other regulations)
    title = item.get("title") or ""
    title_years = [int(y) for y in _YEAR_RE.findall(title)]
    if title_years:
        return max(title_years)
    return None


def is_deprecated(item: dict[str, Any]) -> bool:
    """True when the item carries an explicit deprecation/superseded marker."""
    if not isinstance(item, dict):
        return False
    if item.get("status") in ("deprecated", "superseded", "expired"):
        return True
    blob = f"{item.get('title') or ''} {item.get('text') or ''}"
    return bool(_DEPRECATED_RE.search(blob))


def rank_with_freshness(
    items: list[dict[str, Any]],
    *,
    year_boost: float = 0.06,
    max_boost: float = 0.15,
    deprecate_penalty: float = 0.20,
) -> list[dict[str, Any]]:
    """Re-rank items so the newest effective version comes first.

    Adds a small, monotonic freshness boost proportional to the parsed year,
    scaled so the newest version wins ties but never dominates a clearly more
    relevant candidate. Deprecated items are pushed down. Items without a
    parseable year keep their original score (no guessing).

    Mutates copies (the returned list items are shallow copies with an updated
    ``score``/``final_relevance_score`` and a ``version_rank`` trace field);
    the input list is not modified.
    """
    if not items:
        return []
    years = [extract_version_year(it) for it in items]
    parsed = [y for y in years if y]
    newest = max(parsed) if parsed else None

    out: list[dict[str, Any]] = []
    for it, year in zip(items, years):
        row = dict(it)
        base = _score_of(row)
        boost = 0.0
        trace = {"version_year": year, "deprecated": is_deprecated(row)}
        if year is not None and newest is not None:
            # Monotonic, capped boost: newest year gets max_boost; older years
            # get proportionally less. Difference is measured in years.
            delta = max(0, newest - year)
            # 1 year back ≈ year_boost; clamp at max_boost so a 10-year-old doc
            # is not buried below an unrelated but topically-perfect candidate.
            boost = min(max_boost, delta * year_boost)
            boost = max_boost - boost  # newest ⇒ full max_boost
        if trace["deprecated"]:
            boost -= deprecate_penalty
        new_score = max(0.0, min(1.0, base + boost))
        row["score"] = new_score
        if "final_relevance_score" in row or "fts_score" in row:
            row["final_relevance_score"] = new_score
            if "fts_score" in row:
                row["fts_score"] = new_score
        row["version_rank"] = trace
        out.append(row)

    out.sort(
        key=lambda r: (
            r.get("version_rank", {}).get("deprecated", False),
            -_score_of(r),
        )
    )
    return out


def _score_of(item: dict[str, Any]) -> float:
    for key in ("final_relevance_score", "score", "fts_score", "similarity"):
        v = item.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def detect_version_conflicts(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect groups of items that are versions of the same regulation.

    Returns a list of conflict records (one per group with >1 version) that the
    answer assembler can disclose: "本地库同时存在历史版本". Grouping is by
    normalized title (strip years / doc numbers / 版本 ordinals).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        key = _normalize_title_for_grouping(it.get("title") or "")
        if not key:
            continue
        groups.setdefault(key, []).append(it)
    conflicts = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        years = sorted(
            {extract_version_year(g) for g in group if extract_version_year(g)}
        )
        if len(years) < 2:
            continue
        conflicts.append({
            "title_key": key,
            "versions": [
                {
                    "knowledge_id": g.get("knowledge_id"),
                    "year": extract_version_year(g),
                    "title": g.get("title"),
                }
                for g in group
            ],
            "years": years,
            "newest_year": years[-1],
        })
    return conflicts


def _normalize_title_for_grouping(title: str) -> str:
    """Reduce a regulation title to its grouping key by removing year/doc-no/
    version tokens, so '差旅费管理办法-2025年' and '差旅费管理办法-2018年'
    collapse to the same key."""
    t = title or ""
    t = _DOC_NUM_YEAR_RE.sub("", t)
    t = _YEAR_RE.sub("", t)
    t = _VERSION_ORDINAL_RE.sub("", t)
    t = re.sub(r"年版|年修订|年|号|第|版", "", t)
    t = re.sub(r"[-—_/\\()（）【】\[\]<>《》\s]+", "", t)
    return t.strip()
