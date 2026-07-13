"""Rule-first Query Router for Verified Hybrid retrieval (Phase 3).

Spec §7.2: interpretable, degradable. Router failure → parallel defaults.
No LLM required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RouteDecision:
    intent: str
    wiki_weight: float
    raw_weight: float
    reasons: list[str] = field(default_factory=list)
    freshness_sensitive: bool = False

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "wiki_weight": self.wiki_weight,
            "raw_weight": self.raw_weight,
            "reasons": list(self.reasons),
            "freshness_sensitive": self.freshness_sensitive,
        }


# Intent → (wiki_w, raw_w) — Spec §7.2 table (normalized later with config)
_INTENT_WEIGHTS: dict[str, tuple[float, float]] = {
    "definition": (0.70, 0.40),
    "entity_summary": (0.70, 0.40),
    "relationship": (0.65, 0.45),
    "comparison": (0.55, 0.65),
    "exact_lookup": (0.25, 0.80),
    "document_location": (0.20, 0.85),
    "recent_or_current": (0.25, 0.80),
    "multi_source_synthesis": (0.55, 0.70),
    "unanswerable_check": (0.40, 0.70),
    "general": (0.45, 0.55),
}

_DEF_PAT = re.compile(
    r"(什么是|是什么|定义|含义|概念|简介|概述|what\s+is|define|definition)",
    re.I,
)
_REL_PAT = re.compile(
    r"(关系|关联|依赖|影响|对比|区别|差异|vs\.?|versus|compare|comparison|之间)",
    re.I,
)
_EXACT_PAT = re.compile(
    r"(多少|几页|第\s*\d+|页码|条款|条款号|具体数值|精确|exact|page\s*\d+|clause)",
    re.I,
)
_LOC_PAT = re.compile(
    r"(在哪|哪一页|哪一章|哪一节|文件位置|locate|where\s+is|which\s+page)",
    re.I,
)
_FRESH_PAT = re.compile(
    r"(当前|最新|现行|现在|截至|目前|最近|latest|current|now|as\s+of|uptodate)",
    re.I,
)
_SYN_PAT = re.compile(
    r"(综合|汇总|梳理|总结|跨文档|多来源|synthesize|summary\s+of)",
    re.I,
)
_UNANS_PAT = re.compile(
    r"(有没有|是否存在|能否找到|有无记录|is\s+there|any\s+evidence)",
    re.I,
)
_ENTITY_PAT = re.compile(
    r"(简介|概况|档案|实体|谁是|介绍一下)",
    re.I,
)


def route_query(query: str) -> RouteDecision:
    """Classify query intent with rule priority; always returns a decision."""
    q = (query or "").strip()
    if not q:
        return RouteDecision(
            intent="general",
            wiki_weight=0.45,
            raw_weight=0.55,
            reasons=["empty_query_default"],
        )

    reasons: list[str] = []
    freshness = bool(_FRESH_PAT.search(q))

    if _FRESH_PAT.search(q) and (_EXACT_PAT.search(q) or len(q) < 40):
        intent = "recent_or_current"
        reasons.append("freshness_keywords")
    elif _LOC_PAT.search(q):
        intent = "document_location"
        reasons.append("location_keywords")
    elif _EXACT_PAT.search(q):
        intent = "exact_lookup"
        reasons.append("exact_lookup_keywords")
    elif _REL_PAT.search(q):
        intent = "comparison" if re.search(r"对比|区别|差异|vs|compare", q, re.I) else "relationship"
        reasons.append("relation_keywords")
    elif _DEF_PAT.search(q):
        intent = "definition"
        reasons.append("definition_keywords")
    elif _ENTITY_PAT.search(q):
        intent = "entity_summary"
        reasons.append("entity_keywords")
    elif _SYN_PAT.search(q):
        intent = "multi_source_synthesis"
        reasons.append("synthesis_keywords")
    elif _UNANS_PAT.search(q):
        intent = "unanswerable_check"
        reasons.append("existence_keywords")
    else:
        intent = "general"
        reasons.append("default_general")

    if freshness and intent not in ("recent_or_current", "exact_lookup", "document_location"):
        # Spec §7.8: lower wiki priority for temporal queries
        intent = "recent_or_current"
        reasons.append("freshness_override")

    wiki_w, raw_w = _INTENT_WEIGHTS.get(intent, (0.45, 0.55))
    if freshness:
        wiki_w = min(wiki_w, 0.30)
        raw_w = max(raw_w, 0.75)
        reasons.append("freshness_weight_adjust")

    return RouteDecision(
        intent=intent,
        wiki_weight=wiki_w,
        raw_weight=raw_w,
        reasons=reasons,
        freshness_sensitive=freshness,
    )


def merge_route_with_config(
    decision: RouteDecision,
    *,
    config_wiki_weight: float | None = None,
    config_raw_weight: float | None = None,
) -> RouteDecision:
    """Blend router weights with rag.verified_knowledge.* config defaults."""
    wiki_w = decision.wiki_weight
    raw_w = decision.raw_weight
    if config_wiki_weight is not None and config_raw_weight is not None:
        # 50/50 blend of router intent and static config
        wiki_w = 0.5 * decision.wiki_weight + 0.5 * float(config_wiki_weight)
        raw_w = 0.5 * decision.raw_weight + 0.5 * float(config_raw_weight)
    # Normalize so they sum to ~1 for interpretability (RRF uses relative weights)
    total = wiki_w + raw_w
    if total > 0:
        wiki_w, raw_w = wiki_w / total, raw_w / total
    return RouteDecision(
        intent=decision.intent,
        wiki_weight=wiki_w,
        raw_weight=raw_w,
        reasons=list(decision.reasons) + ["config_blend"],
        freshness_sensitive=decision.freshness_sensitive,
    )
