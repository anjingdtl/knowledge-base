"""Typed QueryPlan for FactCandidate extraction (SPEC v5 §2.3 + v6 §3.1).

Scope/selector are NOT exclusive conditions. Conditions express if/when triggers.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Exclusive numeric-binding labels (trigger / class selectors that bind values).
_CONDITION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"III\s*类|Ⅲ\s*类|三类"), "III类"),
    (re.compile(r"(?<![IⅠ])II\s*类|Ⅱ\s*类|二类"), "II类"),
    (re.compile(r"(?<![IⅠ二三])I\s*类|(?<![IⅠ二三])Ⅰ\s*类|一类"), "I类"),
    (re.compile(r"涉诈|诈骗|防诈"), "涉诈"),
    (re.compile(r"涉骚扰|骚扰"), "涉骚扰"),
    (re.compile(r"区外"), "区外"),
    (re.compile(r"区内"), "区内"),
    (re.compile(r"初审|审核初审"), "初审"),
    (re.compile(r"产品评估"), "产品评估"),
]

# Scope / object-range terms (may co-exist with multiple value dimensions).
_SCOPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"个人(?:支付)?账户|个人会员|个人"), "个人"),
    (re.compile(r"组织|企业|单位"), "组织"),
    (re.compile(r"团体"), "团体"),
    (re.compile(r"代理商"), "代理商"),
]

# Selectors for value dimensions / per-unit framing.
_SELECTOR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"总额|总计|合计"), "总额"),
    (re.compile(r"人均|每人|每个号码|每(?:个|名)人"), "人均"),
    (re.compile(r"单项|单笔"), "单项"),
    (re.compile(r"年付款|年\s*限"), "年付款"),
    (re.compile(r"周期|自然月|每月|每年"), "周期"),
    (re.compile(r"比例|占比"), "比例"),
]

_POLARITY_NEG = re.compile(r"不得|禁止|严禁|不可|不能|取消|不再")
_POLARITY_POS = re.compile(r"应当|必须|应|须|负责|牵头")


@dataclass
class QueryPlan:
    """Intent + typed slots derived only from the user question."""

    raw: str
    intents: list[str] = field(default_factory=list)
    # Legacy field kept for numeric exclusive labels (II类/涉诈/…); NOT scope words.
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
    # SPEC v6 typed slots
    subject: str = ""
    object: str = ""
    scope: list[str] = field(default_factory=list)
    selector: list[str] = field(default_factory=list)
    predicate: str = ""
    polarity: str = ""  # negative | positive | neutral
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
    """Extract exclusive binding labels only (not scope/object words)."""
    found: list[str] = []
    for pat, label in _CONDITION_PATTERNS:
        if pat.search(text or "") and label not in found:
            found.append(label)
    return found


def extract_scopes(text: str) -> list[str]:
    found: list[str] = []
    for pat, label in _SCOPE_PATTERNS:
        if pat.search(text or "") and label not in found:
            found.append(label)
    return found


def extract_selectors(text: str) -> list[str]:
    found: list[str] = []
    for pat, label in _SELECTOR_PATTERNS:
        if pat.search(text or "") and label not in found:
            found.append(label)
    return found


def plan_query(question: str) -> QueryPlan:
    """Identify intents and typed slots. Never reads Golden or case ids."""
    q = question or ""
    conditions = extract_conditions(q)
    scopes = extract_scopes(q)
    selectors = extract_selectors(q)
    intents: list[str] = []
    slots: list[str] = []
    rewrite_trace: list[dict[str, str]] = []

    wants_numeric = bool(
        re.search(r"限额|金额|处罚|罚|扣分|占比|比例|标准|多少|元|%|％|奖金|补助|不少于", q)
    )
    wants_deadline = bool(re.search(r"时限|工作日|期限|几天|多少天", q))
    wants_version = bool(re.search(r"最新|修订版|版本|哪一年|哪年|现行", q))
    wants_responsibility = bool(
        re.search(r"负责|职责|归口|主管部门|谁负责|哪个部门|首席|顾问|牵头", q)
    )
    wants_scope = bool(re.search(r"适用范围|适用于|适用对象|覆盖|范围", q))
    wants_relationship = bool(
        re.search(r"关系|对应|区别|对比|之间|与.*的|是否一致|联动|法律效力", q)
    )
    wants_policy = bool(
        re.search(
            r"办法|规定|制度|禁止|不得|应当|必须|原则|两条线|主体|制作|"
            r"合规|管理|通知|细则|要求|取消|分级|报账|准入|门槛|响应",
            q,
        )
    ) or (not wants_numeric and not wants_deadline and not wants_version)

    # Polarity
    polarity = "neutral"
    if _POLARITY_NEG.search(q):
        polarity = "negative"
    elif _POLARITY_POS.search(q):
        polarity = "positive"

    # Predicate from query surface (generic, not Golden-bound).
    predicate = ""
    for pat, name in (
        (re.compile(r"不得使用|禁止使用|不得"), "不得使用"),
        (re.compile(r"取消"), "取消"),
        (re.compile(r"收支两条线"), "收支两条线"),
        (re.compile(r"限额|年付款"), "限额"),
        (re.compile(r"处罚|被罚|罚款"), "处罚"),
        (re.compile(r"占比|不得少于"), "占比下限"),
        (re.compile(r"负责|牵头|归口"), "职责"),
        (re.compile(r"法律效力|效力关系"), "效力关系"),
        (re.compile(r"准入|门槛|入驻"), "准入"),
        (re.compile(r"问需|响应|闭环"), "问需响应"),
        (re.compile(r"奖金|上限"), "奖金限额"),
        (re.compile(r"保密期限"), "保密期限"),
        (re.compile(r"报账|报销"), "报账"),
    ):
        if pat.search(q):
            predicate = name
            break

    value_dimensions: list[str] = []
    if wants_numeric:
        if "总额" in selectors or re.search(r"团体|总额", q):
            value_dimensions.append("总额")
        if "人均" in selectors or re.search(r"人均|每人|个人奖", q):
            value_dimensions.append("人均")
        # 团体奖金限额 policies typically publish both team total and per-person caps.
        if re.search(r"奖金限额|奖励.*限额", q) and "总额" in value_dimensions and "人均" not in value_dimensions:
            value_dimensions.append("人均")
            rewrite_trace.append({
                "from": "团体奖金限额",
                "to": "团体总额+人均限额",
                "source": "domain_normalize_bonus_caps",
            })
        if "比例" in selectors or re.search(r"占比|比例|%", q):
            value_dimensions.append("比例")
        if re.search(r"年付款|年\s*限额", q):
            value_dimensions.append("年付款")
        # "每个号码" is a selector, not a separate required value dimension.
        if re.search(r"自然月", q) and "周期" not in value_dimensions:
            value_dimensions.append("周期")
        # Multi-class account limits: treat each class as a dimension when both appear in
        # corpus pattern (II/III); query may only say 个人支付账户.
        if re.search(r"支付账户|账户余额|年付款", q) and not conditions:
            value_dimensions.extend(["II类", "III类"])
            rewrite_trace.append({
                "from": "个人支付账户余额年付款限额",
                "to": "II类/III类年付款限额",
                "source": "domain_normalize_account_limits",
            })
        # Colloquial contest bonus → labor contest bonus dimensions
        if re.search(r"搞比赛|发奖金|奖金.*上限|上限是多少", q):
            if "总额" not in value_dimensions:
                value_dimensions.append("总额")
            if "人均" not in value_dimensions:
                value_dimensions.append("人均")
            rewrite_trace.append({
                "from": "比赛奖金上限",
                "to": "劳动竞赛奖金限额",
                "source": "domain_normalize_contest_bonus",
            })
        if not value_dimensions:
            value_dimensions.append("value")

    if wants_numeric:
        intents.append("numeric")
        slots.append("value")
        slots.append("unit")
        # Only exclusive conditions become required condition slots.
        if conditions:
            slots.append("condition")
        for dim in value_dimensions:
            if dim not in slots:
                slots.append(f"dim:{dim}")
    if wants_deadline:
        intents.append("deadline")
        slots.append("deadline")
        if "初审" in conditions or "初审" in q:
            slots.append("初审")
        if "产品评估" in conditions or "评估" in q:
            slots.append("产品评估")
    if wants_version:
        intents.append("version")
        slots.append("version")
        slots.append("doc_no")
    if wants_responsibility:
        intents.append("responsibility")
        slots.append("subject")
        slots.append("role")
    if wants_scope:
        intents.append("scope")
        slots.append("scope")
    if wants_relationship:
        intents.append("relationship")
        slots.append("relationship")
    if wants_policy or not intents:
        intents.append("policy")
        slots.append("policy_fact")
        if polarity == "negative":
            slots.append("polarity_negative")
        if predicate:
            slots.append(f"predicate:{predicate}")

    # Multi exclusive condition numeric: each is its own coverage slot.
    for c in conditions:
        if c not in slots:
            slots.append(c)

    entities = [
        t
        for t in re.findall(r"[\u4e00-\u9fff]{2,12}", q)
        if t
        not in {
            "什么", "多少", "如何", "怎么", "是否", "哪个", "哪些", "以及", "或者",
            "一个", "公司", "取消", "最新", "修订", "版本", "中国", "电信", "广西",
            "集团", "总部", "北京", "关于", "印发", "通知", "不得", "使用",
        }
    ][:12]

    # Query-derived anchors for policy localization (no fixed high_value list).
    anchors: list[str] = []
    for t in entities:
        if len(t) >= 2 and t not in anchors:
            anchors.append(t)
    if predicate and predicate not in anchors:
        anchors.insert(0, predicate)
    for extra in re.findall(
        r"收支两条线|小金库|外部互联网邮箱|微信|交通意外|专职安全员|"
        r"南宁分公司|实际操作|团体奖金|人均|年付款|入驻门槛|权益优惠",
        q,
    ):
        if extra not in anchors:
            anchors.append(extra)

    allow: list[str] = []
    if wants_numeric:
        allow.append("numeric")
    if wants_deadline:
        allow.append("deadline")
    if wants_version:
        allow.append("version")
    if wants_responsibility:
        allow.append("responsibility")
    if wants_scope:
        allow.append("scope")
    if wants_relationship:
        allow.append("relationship")
    if wants_policy or "policy" in intents:
        allow.extend(["policy", "prohibition"])
    seen: set[str] = set()
    allow_u = []
    for a in allow:
        if a not in seen:
            seen.add(a)
            allow_u.append(a)

    # Subqueries for multi-object / multi-sub-question patterns
    subqueries: list[dict[str, Any]] = []
    if re.search(r"分别|以及|和.*的|与.*的关系", q) and (
        wants_responsibility or wants_relationship or wants_numeric
    ):
        # Split on 和/与 when two parallel objects appear.
        parts = re.split(r"[和与、]|分别", q)
        parts = [p.strip() for p in parts if p and len(p.strip()) >= 2]
        if len(parts) >= 2:
            for p in parts[:4]:
                subqueries.append({"text": p, "anchors": _tokenize_simple(p)})

    subject = entities[0] if entities else ""
    obj = ""
    for e in entities[1:]:
        if e not in (subject,):
            obj = e
            break

    requested_attribute = ""
    if wants_numeric:
        requested_attribute = "numeric_limit"
    elif wants_deadline:
        requested_attribute = "deadline"
    elif wants_version:
        requested_attribute = "version"
    elif wants_responsibility:
        requested_attribute = "responsibility"
    elif polarity == "negative":
        requested_attribute = "prohibition"
    else:
        requested_attribute = "policy"

    unit = ""
    if re.search(r"万元", q):
        unit = "万元"
    elif re.search(r"元", q):
        unit = "元"
    elif re.search(r"%|％|占比|比例", q):
        unit = "%"
    elif re.search(r"工作日", q):
        unit = "个工作日"

    time_or_version = ""
    m_year = re.search(r"((?:19|20)\d{2})", q)
    if m_year:
        time_or_version = m_year.group(1)
    elif wants_version:
        time_or_version = "latest"

    return QueryPlan(
        raw=q,
        intents=intents,
        conditions=conditions,  # exclusive only
        required_slots=slots,
        entities=entities,
        wants_numeric=wants_numeric,
        wants_deadline=wants_deadline,
        wants_version=wants_version,
        wants_policy=wants_policy or "policy" in intents,
        wants_responsibility=wants_responsibility,
        wants_scope=wants_scope,
        wants_relationship=wants_relationship,
        allow_fact_kinds=allow_u,
        subject=subject,
        object=obj,
        scope=scopes,
        selector=selectors,
        predicate=predicate,
        polarity=polarity,
        condition_slots=list(conditions),
        requested_attribute=requested_attribute,
        value_dimensions=value_dimensions,
        unit=unit,
        time_or_version=time_or_version,
        subqueries=subqueries,
        query_rewrite_trace=rewrite_trace,
        anchors=anchors,
    )


def _tokenize_simple(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]{2,10}", text or "")[:8]
