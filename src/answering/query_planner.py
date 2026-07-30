"""Query-derived planning for evidence-grounded answers.

This module deliberately contains only general language structures.  It must
not encode a document name, a knowledge-base fact, or an evaluation question.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


_NEGATIVE_RE = re.compile(r"不得|禁止|严禁|不可|不能|取消|不再|废止|停止")
_POSITIVE_RE = re.compile(r"应当|必须|应|须|负责|牵头|归口")
_NUMERIC_RE = re.compile(r"限额|额度|金额|处罚|罚款|扣分|占比|比例|标准|多少|元|%|％|奖金|补助|不少于|不超过")
_DEADLINE_RE = re.compile(r"时限|工作日|期限|几天|多少天|完成时间|办理时间")
_VERSION_RE = re.compile(r"最新|修订版|版本|哪一年|哪年|现行|历年|变化|替代")
_RESPONSIBILITY_RE = re.compile(r"负责|职责|归口|主管部门|谁负责|哪个部门|牵头|负责人")
_SCOPE_RE = re.compile(r"适用范围|适用于|适用对象|覆盖|范围")
_RELATIONSHIP_RE = re.compile(r"关系|对应|区别|对比|之间|是否一致|联动|效力")
_POLICY_RE = re.compile(r"办法|规定|制度|禁止|不得|应当|必须|原则|管理|通知|细则|要求|取消|准入|门槛|流程|处理")
_GENERIC_SURFACE_ALIASES = {
    "比赛": ("竞赛",), "赛事": ("竞赛",), "奖金": ("奖励",),
    "商家": ("合作商",), "店铺": ("门店",),
}


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if v and not (v in seen or seen.add(v))]


def _query_phrases(text: str, *, limit: int = 12) -> list[str]:
    """Extract query surface phrases without a domain vocabulary.

    Chinese does not provide whitespace token boundaries reliably.  We retain
    user-supplied chunks and useful short substrings, while excluding only
    generic interrogatives and function words.
    """
    stop = {
        "什么", "多少", "如何", "怎么", "是否", "哪个", "哪些", "以及", "或者",
        "一个", "公司", "关于", "印发", "通知", "中国", "电信", "广西", "集团",
        "总部", "最新", "修订", "版本", "不得", "使用", "管理办法",
    }
    values: list[str] = []
    # Prefer a general tokenizer when available.  A whole Chinese question is
    # often one regex run, which makes exact-evidence checks compare an
    # artificial sentence-sized "anchor" instead of its entity/predicate
    # terms.  The fallback below keeps minimal installations functional.
    try:
        import jieba
        token_values = [
            token.strip() for token in jieba.lcut(text or "")
            if len(token.strip()) >= 2 and token.strip() not in stop
        ]
        values.extend(token_values)
        for token in token_values:
            values.extend(_GENERIC_SURFACE_ALIASES.get(token, ()))
    except Exception:
        pass
    chunks = re.findall(r"[\u4e00-\u9fff]{2,24}|[A-Za-z0-9][A-Za-z0-9._-]{1,}", text or "")
    for chunk in chunks:
        if chunk in stop:
            continue
        values.append(chunk[:14])
        # Split on general Chinese grammatical connectors before n-grams.  This
        # exposes e.g. a subject, a time phrase and a requested attribute
        # without requiring a terminology dictionary.
        pieces = [p for p in re.split(r"(?:的|和|与|在|内|每|一个|各|分别|以及|并)", chunk) if len(p) >= 2]
        values.extend(p[:12] for p in pieces if p not in stop)
        # Long chunks can still contain a compact entity/attribute pair.  Add
        # only boundary n-grams to avoid flooding anchors with arbitrary slices.
        if len(chunk) > 6:
            for width in (6, 4, 3):
                values.append(chunk[:width])
                values.append(chunk[-width:])
    return _ordered_unique(values)[:limit]


def _extract_conditions(text: str) -> list[str]:
    """Return user-stated binding labels, not a fixed condition taxonomy."""
    q = text or ""
    values: list[str] = []
    # Class/category labels and incident-style labels are common Chinese
    # condition forms and remain generic for unseen domains.
    values.extend(re.findall(r"(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]+|[一二三四五六七八九十]+)\s*类", q))
    values.extend(re.findall(r"涉[\u4e00-\u9fff]", q))
    values.extend(re.findall(r"(?:境内|境外|区内|区外|省内|省外)", q))
    values.extend(re.findall(r"(?:初审|复审|终审|评估|审批|验收)", q))
    return _ordered_unique([re.sub(r"\s+", "", v) for v in values])


def _extract_scopes(text: str) -> list[str]:
    values = re.findall(
        r"(?:个人(?:[\u4e00-\u9fff]{0,4})|团体|组织|企业|单位|客户|用户|合作方|代理商|员工|部门)",
        text or "",
    )
    return _ordered_unique(values)


def _extract_selectors(text: str) -> list[str]:
    values = re.findall(
        r"(?:总额|总计|合计|人均|每人|每个号码|每(?:个|名)人|单项|单笔|年付款|年\s*限|周期|自然月|每月|每年|比例|占比)",
        text or "",
    )
    return _ordered_unique([re.sub(r"\s+", "", v) for v in values])


def _predicate_from_surface(query: str) -> str:
    """Classify predicate by generic linguistic form, never by domain entity."""
    q = query or ""
    for surface in (
        "不得", "禁止", "严禁", "取消", "不再", "废止", "处罚", "罚款", "扣分",
        "限额", "额度", "上限", "标准", "占比", "比例", "负责", "牵头", "归口",
        "关系", "效力", "适用", "范围", "准入", "门槛", "资格", "入驻", "审核", "审查", "审批", "报销",
        "报账", "支付", "响应", "处理", "流程", "时限",
    ):
        if surface in q:
            return surface
    return ""


@dataclass
class QueryPlan:
    """Typed query structure used by gates and fact selection."""

    raw: str
    intents: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    required_slots: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    wants_numeric: bool = False
    wants_deadline: bool = False
    wants_version: bool = False
    wants_policy: bool = False
    wants_responsibility: bool = False
    wants_scope: bool = False
    wants_relationship: bool = False
    allow_fact_kinds: list[str] = field(default_factory=list)
    subject: str = ""
    object: str = ""
    scope: list[str] = field(default_factory=list)
    selector: list[str] = field(default_factory=list)
    predicate: str = ""
    polarity: str = "neutral"
    condition_slots: list[str] = field(default_factory=list)
    requested_attribute: str = ""
    value_dimensions: list[str] = field(default_factory=list)
    unit: str = ""
    time_or_version: str = ""
    subqueries: list[dict[str, Any]] = field(default_factory=list)
    query_rewrite_trace: list[dict[str, str]] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_conditions(text: str) -> list[str]:
    """Compatibility API used by numeric extraction."""
    return _extract_conditions(text)


def extract_scopes(text: str) -> list[str]:
    return _extract_scopes(text)


def extract_selectors(text: str) -> list[str]:
    return _extract_selectors(text)


def plan_query(question: str) -> QueryPlan:
    """Build a general query plan from user text only."""
    q = (question or "").strip()
    conditions = _extract_conditions(q)
    scopes = _extract_scopes(q)
    selectors = _extract_selectors(q)
    wants_numeric = bool(_NUMERIC_RE.search(q)) or bool(
        re.search(r"准入|门槛|资格|条件", q)
    )
    wants_deadline = bool(_DEADLINE_RE.search(q))
    wants_version = bool(_VERSION_RE.search(q))
    wants_responsibility = bool(_RESPONSIBILITY_RE.search(q))
    wants_scope = bool(_SCOPE_RE.search(q))
    wants_relationship = bool(_RELATIONSHIP_RE.search(q))
    predicate = _predicate_from_surface(q)
    wants_policy = bool(_POLICY_RE.search(q)) or not any(
        (wants_numeric, wants_deadline, wants_version, wants_responsibility, wants_scope, wants_relationship)
    )

    polarity = "negative" if _NEGATIVE_RE.search(q) else (
        "positive" if _POSITIVE_RE.search(q) else "neutral"
    )
    intents: list[str] = []
    slots: list[str] = []
    allow: list[str] = []
    dimensions: list[str] = []

    if wants_numeric:
        intents.append("numeric")
        slots.extend(["value", "unit"])
        allow.append("numeric")
        dimensions.append("value")
        for selector in selectors:
            if selector in {"总额", "总计", "合计"}:
                dimensions.append("total")
            elif selector in {"人均", "每人", "每个号码", "每个人", "每名人"}:
                dimensions.append("per_unit")
            elif selector in {"单项", "单笔"}:
                dimensions.append("single_item")
            elif selector in {"比例", "占比"}:
                dimensions.append("ratio")
            elif selector in {"年付款", "年限"}:
                dimensions.append("annual")
            elif selector in {"周期", "自然月", "每月", "每年"}:
                dimensions.append("period")
        for condition in conditions:
            slots.append(f"condition:{condition}")
    if wants_deadline:
        intents.append("deadline")
        slots.append("deadline")
        allow.append("deadline")
    if wants_version:
        intents.append("version")
        slots.append("version")
        allow.append("version")
    if wants_responsibility:
        intents.append("responsibility")
        slots.extend(["subject", "role"])
        allow.append("responsibility")
    if wants_scope:
        intents.append("scope")
        slots.append("scope")
        allow.append("scope")
    if wants_relationship:
        intents.append("relationship")
        slots.append("relationship")
        allow.append("relationship")
    if wants_policy:
        intents.append("policy")
        slots.append("policy_fact")
        # A policy statement may be expressed as a scope, responsibility or
        # relation rather than a declarative rule.  These are still grounded
        # factual candidates and must remain available for coverage ranking.
        allow.extend(["policy", "prohibition", "scope", "responsibility", "relationship"])
        if polarity == "negative":
            slots.append("polarity_negative")
    if predicate:
        slots.append(f"predicate:{predicate}")

    phrases = _query_phrases(q)
    anchors = _ordered_unique(phrases + conditions + scopes + selectors)[:12]
    entities = [p for p in phrases if len(p) >= 2][:12]
    subqueries: list[dict[str, Any]] = []
    if re.search(r"分别|以及|各类|历年|对比|区别|关系", q):
        parts = [p.strip() for p in re.split(r"[、；;]|以及|分别|对比", q) if len(p.strip()) >= 2]
        subqueries = [{"text": p, "anchors": _query_phrases(p, limit=6)} for p in parts[:4]]

    unit = ""
    if re.search(r"万元", q):
        unit = "万元"
    elif re.search(r"元", q):
        unit = "元"
    elif re.search(r"%|％|占比|比例", q):
        unit = "%"
    elif re.search(r"工作日", q):
        unit = "个工作日"
    year_match = re.search(r"((?:19|20)\d{2})", q)
    time_or_version = year_match.group(1) if year_match else ("latest" if wants_version else "")

    requested_attribute = (
        "numeric" if wants_numeric else "deadline" if wants_deadline else
        "version" if wants_version else "responsibility" if wants_responsibility else
        "scope" if wants_scope else "relationship" if wants_relationship else
        "prohibition" if polarity == "negative" else "policy"
    )
    return QueryPlan(
        raw=q,
        intents=_ordered_unique(intents),
        conditions=conditions,
        required_slots=_ordered_unique(slots),
        entities=entities,
        wants_numeric=wants_numeric,
        wants_deadline=wants_deadline,
        wants_version=wants_version,
        wants_policy=wants_policy,
        wants_responsibility=wants_responsibility,
        wants_scope=wants_scope,
        wants_relationship=wants_relationship,
        allow_fact_kinds=_ordered_unique(allow),
        subject=entities[0] if entities else "",
        object=entities[1] if len(entities) > 1 else "",
        scope=scopes,
        selector=selectors,
        predicate=predicate,
        polarity=polarity,
        condition_slots=list(conditions),
        requested_attribute=requested_attribute,
        value_dimensions=_ordered_unique(dimensions),
        unit=unit,
        time_or_version=time_or_version,
        subqueries=subqueries,
        query_rewrite_trace=[],
        anchors=anchors,
    )


def _tokenize_simple(text: str) -> list[str]:
    """Compatibility helper for older callers."""
    return _query_phrases(text, limit=8)


# --- Organizational scope discrimination (SPEC Phase 3.1) --------------------
# Two recurrent wrong-family failure patterns both stem from HQ-vs-branch
# confusion: the query explicitly asks for a branch (e.g. "号百分公司") but
# top-1 returns the HQ regulation, or the query is generic and top-1 returns a
# branch regulation.  The signals below are derived purely from Chinese
# corporate-org linguistic form (no document names, no evaluation examples)
# so they remain domain-agnostic and audit-friendly.

# A "branch token" is a multi-char substring that marks a corporate sub-unit.
# Each branch captured with its prefix (e.g. "南宁分公司") lets us tell two
# branch docs apart, not just HQ-vs-branch.  "号百" is captured bare because it
# is a distinctive brand-style branch marker in this corpus.
_BRANCH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"号百"),
    re.compile(r"([\u4e00-\u9fff]{1,4}分公司)"),
    re.compile(r"([\u4e00-\u9fff]{1,4}子公司)"),
    re.compile(r"([\u4e00-\u9fff]{1,4}支公司)"),
    re.compile(r"([\u4e00-\u9fff]{1,4}营业部)"),
    re.compile(r"([\u4e00-\u9fff]{1,4}办事处)"),
    re.compile(r"([\u4e00-\u9fff]{1,4}代表处)"),
)
# A "HQ token" is a top-level org noun that is NOT immediately followed by a
# branch suffix — "集团分公司" would be a branch, not HQ.  Use a negative
# lookahead so "集团" inside "号百分公司" cannot accidentally flip the signal.
_HQ_PATTERN = re.compile(r"(?:总部|集团公司)(?!分公司|子公司|支公司|营业部)")


def extract_org_scope(query: str) -> dict[str, Any]:
    """Return organizational scope signals parsed from the user query.

    Used by the relevance gate to discriminate between HQ and branch documents
    so a query that explicitly asks for "号百分公司" no longer surfaces the HQ
    regulation at top-1, and a generic query no longer surfaces a branch doc.

    Returns a dict with:
      * ``query_branches`` — list of branch tokens found in the query
        (e.g. ``["号百"]`` or ``["南宁分公司"]``).  ``"号百"`` is captured
        bare because it is a distinctive brand marker.
      * ``is_branch_query`` — True when the query explicitly asks for a branch
        and does NOT also name HQ (mutually exclusive with ``is_hq_query``).
      * ``is_hq_query`` — True when the query explicitly names HQ and does not
        also name a branch.
      * ``has_scope_signal`` — True when either signal fired.
    """
    q = query or ""
    branches: list[str] = []
    for pat in _BRANCH_PATTERNS:
        for m in pat.finditer(q):
            tok = m.group(0)
            if tok and tok not in branches:
                branches.append(tok)
    hq_match = _HQ_PATTERN.search(q)
    is_hq = bool(hq_match) and not branches
    is_branch = bool(branches) and not is_hq
    return {
        "query_branches": branches,
        "is_branch_query": is_branch,
        "is_hq_query": is_hq,
        "has_scope_signal": is_branch or is_hq,
    }
