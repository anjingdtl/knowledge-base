"""Hit-rate V2 scoring authority unit tests (Phase 0 Task 0.3 / Phase 1 Task 1.5)."""

from __future__ import annotations

from evals.hit_rate_v2 import METRIC_CONTRACT_VERSION
from evals.hit_rate_v2.scoring import (
    aggregate_scores,
    evaluate_gate,
    score_answerable_case,
    score_case,
)


def _answerable_case(**overrides):
    base = {
        "case_id": "KB-001",
        "expected_knowledge_ids": ["doc-good"],
        "required_facts": ["处罚2000元"],
        "forbidden_facts": ["处罚5000元"],
        "expected_no_answer": False,
    }
    base.update(overrides)
    return base


def _result(
    *,
    cand_ids: list[str],
    answer: str,
    sources: list | None = None,
    accepted: list[str] | None = None,
    raw_pids: list[str] | None = None,
    mode: str = "verified",
):
    cands = [{"knowledge_id": cid, "passage_id": f"p-{cid}"} for cid in cand_ids]
    srcs = sources if sources is not None else [
        {"knowledge_id": "doc-good", "passage_id": "p-doc-good"}
    ]
    raw = [{"passage_id": pid} for pid in (raw_pids or [s["passage_id"] for s in srcs])]
    return {
        "candidates": cands,
        "ask": {
            "envelope": {
                "ok": True,
                "data": {
                    "answer": answer,
                    "answer_mode": mode,
                    "sources": srcs,
                    "raw_evidence_used": raw,
                    "evidence_snapshot": {
                        "accepted_passage_ids": accepted
                        or [s["passage_id"] for s in srcs],
                        "adjacent_passage_ids": [],
                    },
                    "warnings": [],
                },
            }
        },
    }


def test_metric_contract_version_is_2():
    sc = score_case(
        _answerable_case(),
        _result(cand_ids=["doc-good"], answer="对代理商处罚2000元。"),
    )
    assert sc.metric_contract_version == METRIC_CONTRACT_VERSION == "2.0"


def test_retrieval_miss_with_lucky_answer_fails_e2e():
    """Retrieval failure cannot be masked by answer text containing required facts."""
    sc = score_answerable_case(
        _answerable_case(),
        _result(
            cand_ids=["doc-wrong", "doc-other"],
            answer="对代理商处罚2000元。",
            sources=[{"knowledge_id": "doc-wrong", "passage_id": "p-w"}],
            accepted=["p-w"],
            raw_pids=["p-w"],
        ),
    )
    assert sc.ask_fact_correct is True
    assert sc.recall5 is False
    assert sc.e2e_pass is False
    assert sc.defect_category == "retrieval_recall"


def test_required_fact_ok_but_invalid_passage_lineage_fails_citation():
    sc = score_answerable_case(
        _answerable_case(),
        _result(
            cand_ids=["doc-good"],
            answer="对代理商处罚2000元。",
            sources=[{"knowledge_id": "doc-good", "passage_id": "orphan-pid"}],
            accepted=["p-doc-good"],
            raw_pids=["p-doc-good"],  # orphan-pid not in raw → rejected
        ),
    )
    assert sc.ask_fact_correct is True
    assert sc.ask_citation_valid is False
    assert sc.e2e_pass is False
    assert sc.defect_category == "citation_integrity"


def test_full_pass_answerable():
    sc = score_answerable_case(
        _answerable_case(),
        _result(cand_ids=["doc-good", "x"], answer="每个号码一个自然月内处罚2000元。"),
    )
    assert sc.top1_hit is True
    assert sc.recall5 is True
    assert sc.ask_fact_correct is True
    assert sc.ask_citation_valid is True
    assert sc.e2e_pass is True


def test_aggregate_hallucination_rate_is_not_zero_proxy():
    sc = score_answerable_case(
        _answerable_case(),
        _result(cand_ids=["doc-good"], answer="处罚2000元。"),
    )
    metrics = aggregate_scores([sc])
    report = metrics.to_report_dict()
    assert report["Hallucination Rate"] is None
    assert report["Hallucination Status"] == "not_fully_measurable"
    assert report["Forbidden Assertion Rate"] == 0.0
    assert report["metric_contract_version"] == "2.0"


def test_forbidden_assertion_rate_gate_uses_proxy_not_hallucination():
    sc = score_answerable_case(
        _answerable_case(),
        _result(
            cand_ids=["doc-good"],
            answer="处罚5000元，不是2000元。",  # missing required + has forbidden
        ),
    )
    metrics = aggregate_scores([sc])
    gate = evaluate_gate(metrics)
    assert "Forbidden Assertion Rate" in gate["gates"]
    assert gate["gates"]["Hallucination Rate"]["status"] == "not_fully_measurable"


def test_numeric_unit_requires_value_and_unit_and_condition():
    """99% alone without condition must not pass a condition-bound numeric fact."""
    from evals.hit_rate_v2.scoring import _fact_group_covered

    group = {
        "fact_id": "rate",
        "value": "99",
        "unit": "%",
        "condition": "实名",
        "match_policy": "numeric_unit",
        "required": True,
    }
    assert _fact_group_covered("准确率达到99%", group) is False  # missing 实名
    assert _fact_group_covered("实名用户准确率达到99%", group) is True


def test_condition_scope_version_slots_participate():
    from evals.hit_rate_v2.scoring import _fact_group_covered

    group = {
        "fact_id": "policy",
        "object_text": "适用财务部门",
        "scope": "广西电信",
        "version": "2024版",
        "match_policy": "normalized",
        "required": True,
    }
    assert _fact_group_covered("适用财务部门", group) is False
    assert _fact_group_covered("广西电信2024版制度适用财务部门", group) is True


def test_semantic_review_does_not_auto_pass_on_substring():
    from evals.hit_rate_v2.scoring import _fact_group_covered

    group = {
        "fact_id": "sem",
        "object_text": "复杂政策含义",
        "match_policy": "semantic_review",
        "required": True,
    }
    assert _fact_group_covered("复杂政策含义已阐明", group) is False
    group["semantic_review_passed"] = True
    assert _fact_group_covered("复杂政策含义已阐明", group) is True


def test_citation_requires_golden_expected_passage_when_present():
    case = _answerable_case(
        expected_sources=[
            {
                "knowledge_id": "doc-good",
                "passage_id": "p-doc-good",
                "source_role": "primary",
            }
        ],
        required_fact_groups=[
            {
                "fact_id": "f1",
                "object_text": "处罚2000元",
                "match_policy": "normalized",
                "required": True,
                "evidence_passage_id": "p-doc-good",
            }
        ],
    )
    # Citation uses a different accepted passage → fail golden binding
    sc = score_answerable_case(
        case,
        _result(
            cand_ids=["doc-good"],
            answer="对代理商处罚2000元。",
            sources=[{"knowledge_id": "doc-good", "passage_id": "p-other"}],
            accepted=["p-other"],
            raw_pids=["p-other"],
        ),
    )
    assert sc.ask_citation_valid is False


def test_unsupported_assertion_rate_is_na_without_claims():
    sc = score_answerable_case(
        _answerable_case(),
        _result(cand_ids=["doc-good"], answer="对代理商处罚2000元。"),
    )
    assert sc.extra.get("unsupported_assertion_rate") is None


def test_clarification_required_scoring():
    from evals.hit_rate_v2.scoring import score_case

    case = {
        "case_id": "KB-CL",
        "answerability": "clarification_required",
        "ambiguity": {
            "status": "needs_clarification",
            "clarifying_question": "请问指的是哪个分公司",
        },
    }
    # Definitive answer without clarifying → fail
    sc_fail = score_case(
        case,
        {
            "ask": {
                "envelope": {
                    "ok": True,
                    "data": {
                        "answer": "南宁分公司。",
                        "answer_mode": "raw_only",
                        "sources": [],
                        "raw_evidence_used": [],
                        "warnings": [],
                    },
                }
            }
        },
    )
    assert sc_fail.e2e_pass is False
    assert "definitive_answer_without_clarification" in sc_fail.reason_codes

    # Raises the clarification dimension → pass
    sc_ok = score_case(
        case,
        {
            "ask": {
                "envelope": {
                    "ok": True,
                    "data": {
                        "answer": "请问指的是哪个分公司？需要澄清后才能回答。",
                        "answer_mode": "clarification",
                        "sources": [],
                        "raw_evidence_used": [],
                        "warnings": [],
                    },
                }
            }
        },
    )
    assert sc_ok.e2e_pass is True
