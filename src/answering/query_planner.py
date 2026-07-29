"""Query intent planner for FactCandidate extraction (SPEC v5 §2.3)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Condition labels shared with numeric / table binding.
_CONDITION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"III\s*类|Ⅲ\s*类|三类"), "III类"),
    (re.compile(r"(?<![IⅠ])II\s*类|Ⅱ\s*类|二类"), "II类"),
    (re.compile(r"(?<![IⅠ二三])I\s*类|(?<![IⅠ二三])Ⅰ\s*类|一类"), "I类"),
    (re.compile(r"涉诈|诈骗|防诈"), "涉诈"),
    (re.compile(r"涉骚扰|骚扰"), "涉骚扰"),
    (re.compile(r"区外"), "区外"),
    (re.compile(r"区内"), "区内"),
    (re.compile(r"团体"), "团体"),
    (re.compile(r"个人"), "个人"),
    (re.compile(r"初审|审核初审"), "初审"),
    (re.compile(r"产品评估"), "产品评估"),
]


@dataclass
class QueryPlan:
    """Intent + slot coverage plan derived only from the user question."""

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_conditions(text: str) -> list[str]:
    found: list[str] = []
    for pat, label in _CONDITION_PATTERNS:
        if pat.search(text or "") and label not in found:
            found.append(label)
    return found


def plan_query(question: str) -> QueryPlan:
    """Identify intents and required slots. Never reads Golden or case ids."""
    q = question or ""
    conditions = extract_conditions(q)
    intents: list[str] = []
    slots: list[str] = []

    wants_numeric = bool(
        re.search(r"限额|金额|处罚|罚|扣分|占比|比例|标准|多少|元|%|％|奖金", q)
    )
    wants_deadline = bool(re.search(r"时限|工作日|期限|几天|多少天", q))
    wants_version = bool(re.search(r"最新|修订版|版本|哪一年|哪年|现行", q))
    wants_responsibility = bool(
        re.search(r"负责|职责|归口|主管部门|谁负责|哪个部门|首席|顾问", q)
    )
    wants_scope = bool(re.search(r"适用范围|适用于|适用对象|覆盖|范围", q))
    wants_relationship = bool(
        re.search(r"关系|对应|区别|对比|之间|与.*的|是否一致|联动", q)
    )
    # Policy/prohibition text when question is about rules/制度 but not pure numeric.
    wants_policy = bool(
        re.search(
            r"办法|规定|制度|禁止|不得|应当|必须|原则|两条线|主体|制作|"
            r"合规|管理|通知|细则|要求|取消|分级",
            q,
        )
    ) or (not wants_numeric and not wants_deadline and not wants_version)

    if wants_numeric:
        intents.append("numeric")
        slots.append("value")
        slots.append("unit")
        if conditions:
            slots.append("condition")
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

    # Multi-condition numeric: each condition is its own coverage slot.
    for c in conditions:
        if c not in slots:
            slots.append(c)

    entities = [
        t
        for t in re.findall(r"[\u4e00-\u9fff]{2,12}", q)
        if t
        not in {
            "什么",
            "多少",
            "如何",
            "怎么",
            "是否",
            "哪个",
            "哪些",
            "以及",
            "或者",
            "一个",
            "公司",
            "取消",
            "最新",
            "修订",
            "版本",
            "中国",
            "电信",
            "广西",
            "集团",
            "总部",
            "北京",
            "关于",
            "印发",
            "通知",
        }
    ][:12]

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
    # De-dupe preserve order
    seen: set[str] = set()
    allow_u = []
    for a in allow:
        if a not in seen:
            seen.add(a)
            allow_u.append(a)

    return QueryPlan(
        raw=q,
        intents=intents,
        conditions=conditions,
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
    )
