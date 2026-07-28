"""SPEC v4 answer-contract tests (passage evidence, claims, numeric, gate, no-answer).

These tests capture the v3 failure modes *before* implementation is complete and
must remain green after the answer-layer rewrite. No Golden case_id branches.
"""
from __future__ import annotations

import json

import pytest

from src.answering.claim_protocol import (
    ClaimDraft,
    ground_claims,
    parse_claim_drafts,
    render_short_answer,
    structured_answer_from_evidence,
)
from src.answering.direct_slot_gate import evaluate_direct_slot_evidence
from src.answering.numeric_triples import (
    extract_numeric_triples,
    filter_triples_for_query,
    select_answer_triples,
)
from src.answering.passage_evidence import (
    PassageEvidence,
    ensure_passage_trace,
    normalize_to_passage_evidence,
)
from src.services.relevance_gate import evaluate_evidence_unified


# ---------------------------------------------------------------------------
# Fixtures (reusable, not Golden-case hardcodes for production code)
# ---------------------------------------------------------------------------

PASSAGE_FRAUD = {
    "passage_id": "p-fraud-2000",
    "knowledge_id": "51b17abe-8fe3-42fb-8c90-2b9b3d6fb934",
    "document_family_id": "topic:涉诈涉骚扰电话号码入网渠道处置细则",
    "version_year": 2026,
    "title": "市场-2026-8号-涉诈涉骚扰处置细则-2026",
    "text": (
        "附件1 代理商入网号码涉诈处置规则。"
        "一个自然月内涉诈号码每个号码处罚2000元/个。"
        "入网时间和涉诈月份间隔超过24个月（不含）的号码，不再统计和处置。"
        "附件2 代理商入网号码涉骚扰处置规则。"
        "一个自然月内涉骚扰号码每个号码处罚30元/个。"
    ),
    "block_ids": ["b1", "b2"],
    "score": 0.9,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
}

PASSAGE_WINGPAY = {
    "passage_id": "p-wing-iii",
    "knowledge_id": "27922ca4-aa1a-4cee-bf16-b4ee182a5201",
    "document_family_id": "topic:翼支付业务管理办法",
    "version_year": 2026,
    "title": "翼支付业务管理办法-2026",
    "text": (
        "II类支付账户，其余额年付款限额为10万元（不含提现）；"
        "III类支付账户，其余额年付款限额为20万元（不含提现）。"
    ),
    "block_ids": ["w1"],
    "score": 0.95,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
}

PASSAGE_ASK_NEED = {
    "passage_id": "p-ask-need",
    "knowledge_id": "b40b8949-e458-408a-aa75-292b0540516b",
    "document_family_id": "topic:产品问需",
    "version_year": 2025,
    "title": "产品问需管理办法",
    "text": (
        "产品问需工单审核初审时限为1个工作日；"
        "产品评估时限为5个工作日。"
    ),
    "block_ids": ["a1"],
    "score": 0.30,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
}

PASSAGE_SKILL_2026 = {
    "passage_id": "p-skill-2026",
    "knowledge_id": "2b63b216-9850-4e82-803e-4006cb9f62ad",
    "document_family_id": "topic:技能竞赛管理办法",
    "version_year": 2026,
    "title": "中电信桂-2026-158号-技能竞赛管理办法-修订",
    "text": (
        "中电信桂〔2026〕158号关于印发中国电信广西公司技能竞赛管理办法（修订）的通知。"
        "本办法自印发之日起施行。"
    ),
    "block_ids": ["s1"],
    "score": 0.88,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
    "is_family_newest": True,
}


class TestPassageEvidenceContract:
    def test_normalize_preserves_passage_fields(self):
        pe = normalize_to_passage_evidence(PASSAGE_FRAUD)
        assert pe.passage_id == "p-fraud-2000"
        assert pe.knowledge_id.startswith("51b17abe")
        assert pe.document_family_id
        assert pe.version_year == 2026
        assert pe.block_ids == ["b1", "b2"]
        d = pe.to_row()
        assert d["passage_id"]
        assert d["retrieval_unit"] == "passage"
        assert d["candidate_type"] == "passage"

    def test_successful_rows_forbid_silent_block_downgrade(self):
        rows = [normalize_to_passage_evidence(PASSAGE_FRAUD).to_row()]
        ok, reason = ensure_passage_trace(rows, require_passage=True)
        assert ok, reason
        # Downgraded row must fail when passages were available.
        bad = [{
            "knowledge_id": "x",
            "block_id": "b",
            "text": "x",
            "candidate_type": "raw_block",
            "passage_id": None,
        }]
        ok2, reason2 = ensure_passage_trace(bad, require_passage=True)
        assert not ok2
        assert "passage" in reason2.lower() or "raw_block" in reason2.lower()

    def test_raw_evidence_and_sources_keep_passage_id(self):
        from src.answering.citations import build_raw_evidence_used
        from src.answering.assembler import build_sources

        row = normalize_to_passage_evidence(PASSAGE_FRAUD).to_row()
        raw = build_raw_evidence_used([row])
        assert raw and raw[0].get("passage_id") == "p-fraud-2000"
        assert raw[0].get("retrieval_unit") == "passage"
        srcs = build_sources([], [], [row])
        assert srcs and srcs[0].get("passage_id") == "p-fraud-2000"
        assert srcs[0].get("candidate_type") == "passage"


class TestStructuredClaimProtocol:
    def test_parse_rejects_process_prose(self):
        drafts, err = parse_claim_drafts(
            json.dumps({
                "claims": [{
                    "text": "## 问题拆解\n涉诈处罚2000元",
                    "evidence_passage_ids": ["p-fraud-2000"],
                    "fact_type": "numeric",
                }]
            })
        )
        assert err or not drafts or all("问题拆解" not in (c.text or "") for c in drafts)

    def test_ungrounded_claim_rejected(self):
        drafts = [ClaimDraft(
            text="每个号码处罚9999元",
            evidence_passage_ids=["p-fraud-2000"],
            fact_type="numeric",
            condition="涉诈",
        )]
        evidence = [normalize_to_passage_evidence(PASSAGE_FRAUD)]
        kept, audit = ground_claims(
            drafts, evidence=evidence, question="涉诈电话 每个号码处罚金额",
        )
        assert not kept
        assert audit

    def test_grounded_claim_kept_and_rendered_short(self):
        drafts = [ClaimDraft(
            text="一个自然月内涉诈号码每个号码处罚2000元",
            evidence_passage_ids=["p-fraud-2000"],
            fact_type="numeric",
            condition="涉诈",
        )]
        evidence = [normalize_to_passage_evidence(PASSAGE_FRAUD)]
        kept, _ = ground_claims(
            drafts, evidence=evidence, question="涉诈电话 每个号码处罚金额",
        )
        assert kept
        answer = render_short_answer(kept)
        assert "2000" in answer
        assert "问题拆解" not in answer
        assert "推理" not in answer

    def test_invalid_json_goes_to_no_answer_not_free_text(self):
        result = structured_answer_from_evidence(
            question="涉诈电话 每个号码处罚金额",
            evidence_rows=[PASSAGE_FRAUD],
            llm_json="这不是JSON，而是长篇问题拆解……",
        )
        assert result["answer_mode"] in ("no_answer", "raw_only", "hybrid_verified")
        # Free prose must not become the answer.
        assert "问题拆解" not in (result.get("answer") or "")
        if result["answer_mode"] == "no_answer":
            assert result.get("answer") == ""
            assert result.get("sources") == []


class TestNumericTriples:
    def test_fraud_not_harassment_value(self):
        triples = extract_numeric_triples(PASSAGE_FRAUD["text"], passage_id="p-fraud-2000")
        selected = select_answer_triples(
            triples, question="涉诈电话 代理商一个自然月内每个号码处罚金额",
        )
        texts = " ".join(f"{t.condition}{t.value}{t.unit}" for t in selected)
        assert "2000" in texts
        assert "30" not in texts.replace("2000", "")

    def test_harassment_not_fraud_value(self):
        triples = extract_numeric_triples(PASSAGE_FRAUD["text"], passage_id="p-fraud-2000")
        selected = select_answer_triples(
            triples, question="涉骚扰电话 代理商一个自然月内每个号码处罚金额",
        )
        texts = " ".join(f"{t.condition}{t.value}{t.unit}" for t in selected)
        assert "30" in texts
        assert "2000" not in texts

    def test_iii_class_not_ii_class(self):
        triples = extract_numeric_triples(PASSAGE_WINGPAY["text"], passage_id="p-wing-iii")
        selected = select_answer_triples(
            triples, question="翼支付III类支付账户 年付款限额",
        )
        assert any(t.value == "20" and "万" in (t.unit or "") for t in selected)
        assert not any(t.value == "10" and "万" in (t.unit or "") for t in selected)

    def test_filter_triples_audit_fields(self):
        triples = extract_numeric_triples(PASSAGE_WINGPAY["text"], passage_id="p-wing-iii")
        kept, audit = filter_triples_for_query(
            triples, question="翼支付III类支付账户 年付款限额",
        )
        assert kept
        assert "query_slots" in audit
        assert "kept" in audit and "dropped" in audit


class TestDirectSlotGate:
    def test_low_score_multi_slot_accept(self):
        decision = evaluate_direct_slot_evidence(
            "产品问需工单 审核初审和产品评估的工作日时限",
            [PASSAGE_ASK_NEED],
        )
        assert decision["direct_slot_evidence"] is True
        assert len(decision["matched_slots"]) >= 2
        assert decision["passage_id"] == "p-ask-need"
        assert decision["spans"]

    def test_low_score_single_generic_word_rejected(self):
        weak = {
            **PASSAGE_ASK_NEED,
            "text": "公司管理办法中对工作的一般要求。",
            "score": 0.2,
        }
        decision = evaluate_direct_slot_evidence(
            "产品问需工单 审核初审和产品评估的工作日时限",
            [weak],
        )
        assert decision["direct_slot_evidence"] is False

    def test_gate_with_direct_slot_does_not_change_threshold_constant(self):
        # Global threshold remains 0.35; direct slot is an *extra* accept path.
        from src.answering.direct_slot_gate import apply_direct_slot_accept
        items = [dict(PASSAGE_ASK_NEED)]
        # Force a rejected base decision so direct-slot path is exercised.
        base = {
            "accept": False,
            "items": [],
            "top_score": 0.30,
            "threshold": 0.35,
            "reason": "below_threshold",
        }
        final = apply_direct_slot_accept(
            "产品问需工单 审核初审和产品评估的工作日时限",
            items,
            base_decision=base,
            threshold=0.35,
        )
        assert final.get("threshold") == 0.35
        assert final.get("accept") is True
        assert final.get("direct_slot_evidence") is True


class TestNoAnswerContract:
    def test_empty_answer_and_empty_sources(self):
        result = structured_answer_from_evidence(
            question="中国电信集团总部北京的办公楼地址",
            evidence_rows=[{
                "passage_id": "p-brand",
                "knowledge_id": "423eb665",
                "text": "品牌管理办法关于内部媒介与办公楼宇品牌露出的规定。",
                "title": "品牌管理办法",
                "score": 0.4,
                "retrieval_unit": "passage",
                "block_ids": ["x"],
            }],
        )
        assert result["answer_mode"] == "no_answer"
        assert result["answer"] == ""
        assert result["sources"] == []
        assert result["raw_evidence_used"] == []


class TestVersionIsolationClaims:
    def test_latest_family_only_in_claims(self):
        old = {
            "passage_id": "p-skill-2023",
            "knowledge_id": "1acb61b4",
            "document_family_id": "topic:技能竞赛管理办法",
            "version_year": 2023,
            "title": "技能竞赛管理办法-2023",
            "text": "一级竞赛 二级竞赛 奖金限额规定。",
            "block_ids": ["o1"],
            "score": 0.7,
            "retrieval_unit": "passage",
        }
        result = structured_answer_from_evidence(
            question="技能竞赛管理办法最新修订版 取消一级二级竞赛分级",
            evidence_rows=[PASSAGE_SKILL_2026, old],
            prefer_latest_family=True,
        )
        kids = {s.get("knowledge_id") for s in (result.get("sources") or [])}
        raw_kids = {r.get("knowledge_id") for r in (result.get("raw_evidence_used") or [])}
        assert "1acb61b4" not in kids
        assert "1acb61b4" not in raw_kids
        ans = result.get("answer") or ""
        assert "一级竞赛" not in ans
        assert "二级竞赛" not in ans
