"""No-answer false-positive detection (Phase 0 Task 0.3) — KB-032 class bugs."""

from __future__ import annotations

from evals.hit_rate_v2.scoring import score_no_answer_case


def _case(**overrides):
    base = {
        "case_id": "KB-032",
        "expected_no_answer": True,
        "forbidden_facts": ["具体办公地址"],
        "query": "中国电信集团总部北京的办公楼地址",
    }
    base.update(overrides)
    return base


def _ask_result(
    *,
    answer: str,
    mode: str | None,
    sources: list | None = None,
    raw_ev: list | None = None,
):
    return {
        "ask": {
            "envelope": {
                "ok": True,
                "data": {
                    "answer": answer,
                    "answer_mode": mode,
                    "sources": sources or [],
                    "raw_evidence_used": raw_ev or [],
                    "evidence_snapshot": {},
                    "warnings": [],
                },
            }
        },
        "candidates": [],
    }


def test_kb032_unrelated_raw_only_with_sources_is_false_positive():
    """Regression: non-empty unrelated answer was scored as non-FP under V1."""
    result = _ask_result(
        answer=(
            "- 企微客户经理：在中国电信集团、省或市级企业微信主体里，与客户建立了联系的员工\n"
            "- 一级责任单元即省公司，对应省级行政区，命名为“XX电信”，如“北京电信”"
        ),
        mode="raw_only",
        sources=[
            {"knowledge_id": "k1", "passage_id": "p1"},
            {"knowledge_id": "k2", "passage_id": "p2"},
        ],
        raw_ev=[{"passage_id": "p1"}, {"passage_id": "p2"}],
    )
    sc = score_no_answer_case(_case(), result)
    assert sc.false_positive is True
    assert "unexpected_answer_mode" in sc.reason_codes
    assert "substantive_answer_on_no_answer" in sc.reason_codes
    assert "sources_present_on_no_answer" in sc.reason_codes
    # Does not contain literal "具体办公地址" — still FP
    assert "forbidden_assertion" not in sc.reason_codes or sc.false_positive


def test_fabricated_address_without_forbidden_phrase_is_false_positive():
    result = _ask_result(
        answer="集团总部办公楼位于北京市西城区金融大街31号。",
        mode="verified",
        sources=[{"knowledge_id": "x", "passage_id": "p"}],
        raw_ev=[{"passage_id": "p"}],
    )
    sc = score_no_answer_case(_case(), result)
    assert sc.false_positive is True
    assert "substantive_answer_on_no_answer" in sc.reason_codes


def test_empty_answer_no_answer_mode_passes():
    result = _ask_result(answer="", mode="no_answer", sources=[], raw_ev=[])
    sc = score_no_answer_case(_case(), result)
    assert sc.false_positive is False
    assert sc.expressed_insufficient is True
    assert sc.no_fabrication is True


def test_clear_refusal_without_assertion_passes():
    result = _ask_result(
        answer="知识库中未找到可回答该问题的证据，无法提供办公楼地址。",
        mode="no_answer",
        sources=[],
        raw_ev=[],
    )
    sc = score_no_answer_case(_case(), result)
    assert sc.false_positive is False
    assert sc.expressed_insufficient is True


def test_refusal_then_assertion_is_false_positive():
    result = _ask_result(
        answer="未找到直接依据，但地址是北京市西城区金融大街31号。",
        mode="no_answer",
        sources=[],
    )
    sc = score_no_answer_case(_case(), result)
    assert sc.false_positive is True
    assert "refusal_with_assertion" in sc.reason_codes or (
        "substantive_answer_on_no_answer" in sc.reason_codes
    )


def test_forbidden_literal_assertion_is_false_positive():
    result = _ask_result(
        answer="具体办公地址如下：某某路1号。",
        mode="raw_only",
    )
    sc = score_no_answer_case(_case(), result)
    assert sc.false_positive is True
    assert "forbidden_assertion" in sc.reason_codes
