"""Deterministic 60-case release fixture with fixed evidence and claims."""
from __future__ import annotations

_CATEGORIES = (
    ("claim_benefit", 20), ("raw_preferred", 15), ("conflict_freshness", 10),
    ("fallback_guard", 10), ("no_answer", 5),
)


def build_cases() -> list[dict]:
    cases: list[dict] = []
    index = 1
    for category, count in _CATEGORIES:
        for _ in range(count):
            telecom = index <= 30
            fact = f"通信事实 {index}" if telecom else f"知识事实 {index}"
            block_text = f"原始证据：{fact}。"
            if category == "claim_benefit":
                block_text = f"原始证据片段 {index}：该通信能力需要跨文档综合。"
            cases.append({
                "id": f"release_{index:03d}", "category": category, "telecom": telecom,
                "question": f"{fact} 是什么？", "expected": fact,
                "knowledge_id": f"release-k{index}", "block_id": f"release-b{index}",
                "block_text": block_text, "claim_statement": f"{fact} 已由证据验证。",
                "preferred_mode": "raw" if category == "raw_preferred" else "hybrid",
            })
            index += 1
    return cases


def validate_cases(cases: list[dict]) -> list[str]:
    errors: list[str] = []
    if len(cases) < 60:
        errors.append("dataset_under_60")
    if len({case.get("id") for case in cases}) != len(cases):
        errors.append("duplicate_id")
    if sum(1 for case in cases if case.get("telecom")) < 30:
        errors.append("telecom_under_30")
    for category, minimum in _CATEGORIES:
        if sum(1 for case in cases if case.get("category") == category) < minimum:
            errors.append(f"category_under_min:{category}")
    for case in cases:
        if not case.get("expected") or not case.get("block_text") or not case.get("claim_statement"):
            errors.append(f"missing_fixture:{case.get('id')}")
    return errors
