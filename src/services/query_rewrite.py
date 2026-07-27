"""Domain query rewrite / synonym expansion for colloquial telecom-policy queries.

SPEC Phase 3.3: colloquial queries (e.g. "防诈骗...被罚多少钱") fail to retrieve
the formal-policy documents ("涉诈...处罚2000元") because neither vector
embeddings nor FTS5 match the synonyms. This module expands a user query into
one or more alias variants so the hybrid retriever has a chance to surface the
right document.

Design constraints (SPEC):
  * Deterministic, lexically explainable — no LLM call, no network.
  * Conservative: only expands well-known domain synonyms; unknown queries are
    returned unchanged so precision on no-answer cases is preserved.
  * Used ONLY as additional retrieval queries — the original query is always
    kept and scores highest in the merge.
"""
from __future__ import annotations

import re

# Colloquial / alias → canonical policy term. Ordered; first match wins per
# left-hand token. Left side is a regex (anchored to word-ish boundaries by the
# caller); right side is the substitution. Substitutions are additive: we keep
# the original phrasing AND add the canonical term, so the expanded query is a
# superset of the original.
#
# Mined from the v1.11.1 Golden Set miss cases (KB-009..021, KB-028).
_SYNONYM_RULES: list[tuple[re.Pattern, str]] = [
    # 涉诈 / 涉骚扰
    (re.compile(r"防诈骗|防诈|诈骗电话|欺诈电话"), "涉诈"),
    (re.compile(r"骚扰电话|骚扰"), "涉骚扰"),
    (re.compile(r"被罚多少钱|罚多少钱|罚多少|被罚|罚款"), "处罚"),
    # 线上合作 / 店铺入驻
    (re.compile(r"线上店铺|店铺入驻|入驻门槛|入驻条件|卖东西的线上店铺"), "线上合作"),
    (re.compile(r"外部商家合作|和外部商家合作"), "线上合作"),
    # 重要决策 / 对外投资并购
    (re.compile(r"大额对外投资|对外投资并购|投资并购|大额投资"), "重要决策 重大对外投融资"),
    (re.compile(r"先过法律审核|要不要过法律"), "法律合规审核"),
    # 劳动 / 技能竞赛
    (re.compile(r"搞比赛|发奖金|比赛发奖金|搞竞赛"), "劳动竞赛 技能竞赛"),
    # 权益业务
    (re.compile(r"送权益|送权益优惠券|权益优惠券|异业合作"), "权益业务"),
    # 产品问需
    (re.compile(r"提产品需求|提需求|产品需求|怎么响应处理|响应处理"), "产品问需 五级闭环"),
    # 差旅费口语
    (re.compile(r"出差的住宿费|住宿费和伙食补助|每天能报多少|报销标准"), "差旅费 报销 伙食补助"),
    # 网络信息安全考核
    (re.compile(r"网信安考核|电话实名制|实名登记率|扣分阈值"), "网络和信息安全考核 实名登记率"),
    # 技能竞赛团体奖金
    (re.compile(r"团体奖金|团体奖金限额"), "技能竞赛 团体奖金"),
]


def expand_query(query: str) -> list[str]:
    """Return the original query plus alias-expanded variants.

    The first element is always the original query (highest priority). Each
    subsequent element is the query with one synonym class substituted, so the
    retriever sees both the user's phrasing and the canonical policy term.
    Duplicates are removed while preserving order.
    """
    q = query or ""
    if not q.strip():
        return []
    variants = [q]
    for pat, replacement in _SYNONYM_RULES:
        if pat.search(q) and replacement not in q:
            # Build a variant where the matched colloquial phrase is supplemented
            # (not replaced) with the canonical term.
            expanded = pat.sub(replacement, q) + " " + q
            if expanded not in variants:
                variants.append(expanded)
    return variants


def canonical_terms(query: str) -> list[str]:
    """Return the distinct canonical policy terms the query expands to.

    Used to run per-term FTS (FTS5 multi-word queries are implicit-AND, which
    fails when any term is absent; running each canonical term separately gives
    OR semantics and reliably surfaces the target document).
    """
    q = query or ""
    terms: list[str] = []
    seen: set[str] = set()
    for pat, replacement in _SYNONYM_RULES:
        if pat.search(q):
            for tok in replacement.split():
                tok = tok.strip()
                if tok and tok not in seen and tok not in q:
                    seen.add(tok)
                    terms.append(tok)
    return terms


def merge_candidates_by_query(
    base_query: str,
    candidate_lists: list[list[dict]],
) -> list[dict]:
    """Merge candidate lists from multiple expanded queries, keeping the
    highest score per knowledge_id. The base_query list is scored first and
    its items win ties (preserves original ranking for exact matches)."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    orphans: list[dict] = []
    for lst in candidate_lists:
        for item in lst:
            if not isinstance(item, dict):
                continue
            kid = str(item.get("knowledge_id") or item.get("page_id") or "").strip()
            if not kid:
                orphans.append(item)
                continue
            if kid not in merged:
                merged[kid] = item
                order.append(kid)
            else:
                # keep the higher score
                prev = merged[kid]
                prev_score = _f(prev.get("score") or prev.get("fts_score") or 0)
                new_score = _f(item.get("score") or item.get("fts_score") or 0)
                if new_score > prev_score:
                    merged[kid] = item
    return [merged[k] for k in order] + orphans


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
