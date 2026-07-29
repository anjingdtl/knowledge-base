"""V7 guardrails: production retrieval/answering must not contain Golden adapters."""
from __future__ import annotations

import re
from pathlib import Path

from src.answering.direct_slot_gate import evaluate_direct_slot_evidence
from src.answering.fact_candidates import FactCandidate, select_fact_candidates
from src.answering.query_planner import plan_query
from src.retrieval.raw_retriever import build_deterministic_query_variants


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_FILES = (
    list((ROOT / "src" / "answering").glob("*.py"))
    + list((ROOT / "src" / "retrieval").glob("*.py"))
    + list((ROOT / "src" / "mcp").rglob("*.py"))
    + [ROOT / "src" / "services" / "query_rewrite.py"]
)


def _production_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in PRODUCTION_FILES)


def test_production_has_no_eval_or_case_dependencies():
    text = _production_text()
    assert not re.search(r"\bKB-\d{3}\b", text)
    assert "golden_set_hit_rate" not in text
    assert "evals/" not in text
    assert "artifacts/" not in text


def test_production_has_no_domain_question_rewrite_table():
    text = _production_text()
    assert "domain_normalize" not in text
    assert "high_value" not in text
    # Query expansion must keep user terms; it cannot own a hand-written map
    # from colloquial question text to a document title.
    raw = (ROOT / "src" / "retrieval" / "raw_retriever.py").read_text(encoding="utf-8")
    assert "norms:" not in raw
    assert "domain_synonym" not in raw


def test_query_plan_does_not_invent_unasked_numeric_dimensions():
    plan = plan_query("星河采购制度 团体奖励上限是多少")
    assert plan.wants_numeric
    assert "value" in plan.value_dimensions
    assert "per_unit" not in plan.value_dimensions
    assert "total" not in plan.value_dimensions


def test_generic_eligibility_question_requests_evidence_backed_numeric_conditions():
    plan = plan_query("星河供应商准入资格条件")
    assert plan.wants_numeric
    assert "numeric" in plan.allow_fact_kinds


def test_query_variants_preserve_user_vocabulary():
    query = "星河采购制度 供应商违规后怎么处理"
    variants = build_deterministic_query_variants(query)
    assert variants[0] == {"query": query, "source": "original"}
    query_chars = set(query.replace(" ", ""))
    for item in variants:
        assert set(item["query"].replace(" ", "")) <= query_chars


def test_unseen_entity_and_predicate_can_pass_direct_gate():
    question = "星云档案制度 不得向外部云盘上传机密材料"
    decision = evaluate_direct_slot_evidence(question, [{
        "passage_id": "p-unseen",
        "knowledge_id": "k-unseen",
        "title": "星云档案制度",
        "text": "星云档案制度规定，不得向外部云盘上传机密材料。",
        "score": 0.1,
    }])
    assert decision["direct_slot_evidence"] is True
    assert decision["passage_id"] == "p-unseen"


def test_direct_gate_does_not_promote_entity_overlap_without_requested_predicate():
    decision = evaluate_direct_slot_evidence("星云项目是否需要审核", [{
        "passage_id": "p-near-miss",
        "knowledge_id": "k-near-miss",
        "title": "星云项目管理办法",
        "text": "星云项目应当纳入年度管理范围。",
        "score": 0.1,
    }])
    assert decision["direct_slot_evidence"] is False


def test_similar_queries_keep_distinct_conditions():
    left = plan_query("北斗项目一类设备处罚金额")
    right = plan_query("北斗项目二类设备处罚金额")
    assert left.conditions != right.conditions


def test_money_question_does_not_select_a_duration_as_the_numeric_answer():
    plan = plan_query("星河项目被罚多少钱")
    duration = FactCandidate(candidate_id="duration", record_id="r1", passage_id="p1", knowledge_id="k1", fact_kind="numeric", value="12", unit="个月", exact_text="暂停12个月")
    money = FactCandidate(candidate_id="money", record_id="r2", passage_id="p2", knowledge_id="k1", fact_kind="numeric", value="2000", unit="元", exact_text="处罚2000元")
    selected, _audit = select_fact_candidates([duration, money], plan=plan)
    assert [candidate.candidate_id for candidate in selected] == ["money"]
