"""SPEC Phase 3.2 §3.3 — direct slot gate must not resurrect no-answer intents.

The unified gate short-circuits ``live_external`` and ``out_of_domain`` queries
to ``no_match``. ``apply_direct_slot_accept`` is called by ``ask`` AFTER the
gate decision, so it must respect that decision — otherwise weak lexical hits
on consumer-recommendation / forecast queries would re-inflate into answers.

These tests pin the invariant by simulating a base_decision whose intent is
``out_of_domain`` (or ``live_external``) and asserting that no candidate set,
no matter how slot-heavy, can flip ``accept`` back to True.
"""
from __future__ import annotations

from src.answering.direct_slot_gate import apply_direct_slot_accept


def _slot_heavy_candidates() -> list[dict]:
    """Candidate set designed to fire direct-slot evidence under the old logic.

    Title + text both contain ``火锅`` (matching the user slot), plus a fact
    cue ("推荐"), so ``evaluate_direct_slot_evidence`` alone would accept it.
    """
    return [{
        "passage_id": "p1",
        "knowledge_id": "k1",
        "title": "星级服务管理办法 火锅推荐",
        "text": "火锅推荐具体限额 100 元。",
        "score": 0.10,
    }]


def test_out_of_domain_intent_short_circuits_direct_slot_accept() -> None:
    """KB-034 regression: 推荐火锅底料 must not be promoted by direct slot."""
    question = "推荐一款好吃的火锅底料品牌"
    base = {
        "accept": False,
        "no_match": True,
        "reason": "out_of_domain:consumer_recommendation",
        "intent": "out_of_domain",
        "top_score": 0.0,
        "threshold": 0.35,
        "items": [],
    }
    decision = apply_direct_slot_accept(
        question,
        _slot_heavy_candidates(),
        base_decision=base,
        threshold=0.35,
    )
    assert decision["accept"] is False
    assert decision["no_match"] is True
    # Reason must NOT flip to "direct_query_evidence"
    assert decision["reason"].startswith("out_of_domain:")
    assert decision["direct_slot_evidence"] is False
    assert "intent_short_circuit:out_of_domain" in str(
        decision.get("direct_slot_audit", {})
    )


def test_live_external_intent_short_circuits_direct_slot_accept() -> None:
    """KB-030 regression: 营收预测 must not be promoted by direct slot."""
    question = "中国电信广西公司2026年营收预测是多少亿元"
    base = {
        "accept": False,
        "no_match": True,
        "reason": "requires_current_external_data",
        "intent": "live_external",
        "top_score": 0.0,
        "threshold": 0.35,
        "items": [],
    }
    decision = apply_direct_slot_accept(
        question,
        _slot_heavy_candidates(),
        base_decision=base,
        threshold=0.35,
    )
    assert decision["accept"] is False
    assert decision["no_match"] is True
    assert decision["reason"] == "requires_current_external_data"
    assert decision["direct_slot_evidence"] is False
    assert "intent_short_circuit:live_external" in str(
        decision.get("direct_slot_audit", {})
    )


def test_ordinary_intent_still_uses_direct_slot_logic() -> None:
    """Ordinary intent must still go through the direct slot promotion path.

    Regression guard: the intent short-circuit is a deny-list for the two
    no-answer intents, not a blanket suppression of direct-slot promotion.
    A near-threshold ordinary query with strong anchor hits must still be
    promoted to ``accept``.
    """
    question = "星云档案制度 不得向外部云盘上传机密材料"
    # top_score above threshold * 0.90 ⇒ min_slots = 1
    base = {
        "accept": False,
        "no_match": True,
        "reason": "insufficient_relevant_evidence",
        "intent": "ordinary",
        "top_score": 0.34,  # close to 0.35 threshold
        "threshold": 0.35,
        "items": [],
    }
    decision = apply_direct_slot_accept(
        question,
        [{
            "passage_id": "p-ord",
            "knowledge_id": "k-ord",
            "title": "星云档案制度",
            "text": "星云档案制度规定，不得向外部云盘上传机密材料。",
            "score": 0.10,
        }],
        base_decision=base,
        threshold=0.35,
    )
    assert decision["accept"] is True
    assert decision["reason"] == "direct_query_evidence"
    assert decision["direct_slot_evidence"] is True


def test_missing_intent_field_falls_through_to_direct_slot_logic() -> None:
    """Backward compatibility: a base_decision without ``intent`` key still
    applies the direct-slot promotion logic. This guards against breaking
    older callers that built the base_decision before the intent field was
    added to the unified gate output.
    """
    question = "星云档案制度 不得向外部云盘上传机密材料"
    base = {
        "accept": False,
        "no_match": True,
        "reason": "insufficient_relevant_evidence",
        # No "intent" key
        "top_score": 0.34,
        "threshold": 0.35,
        "items": [],
    }
    decision = apply_direct_slot_accept(
        question,
        [{
            "passage_id": "p-noint",
            "knowledge_id": "k-noint",
            "title": "星云档案制度",
            "text": "星云档案制度规定，不得向外部云盘上传机密材料。",
            "score": 0.10,
        }],
        base_decision=base,
        threshold=0.35,
    )
    assert decision["accept"] is True
    assert decision["direct_slot_evidence"] is True
