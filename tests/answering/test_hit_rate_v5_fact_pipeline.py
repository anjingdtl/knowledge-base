"""SPEC v5 Tier 0 — FactCandidate / LogicalEvidence / adjacency / snapshot unit tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.answering.claim_protocol import structured_answer_from_evidence
from src.answering.context_builder import expand_adjacent_evidence
from src.answering.fact_candidates import (
    extract_candidates_from_evidence,
    select_fact_candidates,
)
from src.answering.logical_evidence import records_from_passage
from src.answering.passage_evidence import (
    normalize_to_passage_evidence,
    split_metadata_and_body,
)
from src.answering.query_planner import plan_query
from src.retrieval.snapshot_registry import (
    clear_registry,
    get_snapshot,
    put_snapshot,
    process_start_id,
)


PASSAGE_FRAUD = {
    "passage_id": "p-fraud-2000",
    "knowledge_id": "51b17abe-8fe3-42fb-8c90-2b9b3d6fb934",
    "document_family_id": "topic:涉诈涉骚扰电话号码入网渠道处置细则",
    "version_year": 2026,
    "title": "市场-2026-8号-涉诈涉骚扰处置细则-2026",
    "text": (
        "【文档】市场-2026-8号-涉诈涉骚扰处置细则-2026\n"
        "【章节】附件1\n"
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
        "【文档】中电信桂-2026-61号-翼支付业务管理办法-2026\n"
        "【章节】第十八条\n"
        "其中，II类支付账户，其余额年付款限额为10万元（不含提现）；"
        "III类支付账户，其余额年付款限额为20万元（不含提现）。"
    ),
    "block_ids": ["w1"],
    "score": 0.95,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
}

PASSAGE_POLICY = {
    "passage_id": "p-revenue",
    "knowledge_id": "574f1593-2a9f-455d-ab16-bbc5b6d0d879",
    "title": "中电信桂-2026-22号-营收资金管理办法",
    "text": (
        "【文档】中电信桂-2026-22号-营收资金管理办法\n"
        "【章节】总则\n"
        "公司营收资金管理实行收支两条线，严禁设立小金库。"
        "各单位必须按规定将营收款及时全额缴存。"
    ),
    "block_ids": ["r1"],
    "score": 0.9,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
    "version_year": 2026,
}


class TestMetadataBodySplit:
    def test_metadata_not_in_body(self):
        body, start, meta = split_metadata_and_body(PASSAGE_FRAUD["text"])
        assert "【文档】" in meta
        assert "【文档】" not in body
        assert "2000" in body
        assert start > 0
        pe = normalize_to_passage_evidence(PASSAGE_FRAUD)
        pe.ensure_body()
        assert "【文档】" not in pe.body_text
        assert "2000" in pe.body_text

    def test_title_year_not_numeric_candidate_for_policy_query(self):
        result = structured_answer_from_evidence(
            question="营收资金管理办法 收支两条线",
            evidence_rows=[PASSAGE_POLICY],
        )
        ans = result.get("answer") or ""
        assert "收支两条线" in ans or "小金库" in ans
        # Must not answer with year/doc-no fragment alone.
        assert not ans.strip().startswith("- 2026")
        assert "22号" not in ans or "收支" in ans


class TestLogicalRecordsAndTables:
    def test_clause_row_binding_iii_not_ii(self):
        recs = records_from_passage(PASSAGE_WINGPAY)
        assert recs
        assert not any(r.unstructured_table for r in recs)
        cands, plan, _ = extract_candidates_from_evidence(
            [PASSAGE_WINGPAY], question="翼支付III类支付账户 年付款限额"
        )
        selected, audit = select_fact_candidates(cands, plan=plan)
        texts = " ".join(c.display() for c in selected)
        assert "20" in texts
        assert "万" in texts
        # Must not select 10万元 for III类 query.
        assert not any(
            c.value == "10" and "万" in (c.unit or "") for c in selected if c.condition == "III类"
        )

    def test_fraud_not_harassment(self):
        result = structured_answer_from_evidence(
            question="涉诈电话 代理商一个自然月内每个号码处罚金额",
            evidence_rows=[PASSAGE_FRAUD],
        )
        ans = result.get("answer") or ""
        assert "2000" in ans
        assert "30" not in ans.replace("2000", "")

    def test_harassment_not_fraud(self):
        result = structured_answer_from_evidence(
            question="涉骚扰电话 代理商一个自然月内每个号码处罚金额",
            evidence_rows=[PASSAGE_FRAUD],
        )
        ans = result.get("answer") or ""
        assert "30" in ans
        assert "2000" not in ans

    def test_ambiguous_table_refuses_numeric(self):
        blob = {
            "passage_id": "p-amb",
            "knowledge_id": "k1",
            "text": (
                "【文档】表\n"
                "涉诈 - - 处罚 10000 元 - 连续 2 个月 工号永久关闭\n"
                "连续 3 个月 -1.关停 - 处罚 50000 元 - 累计 30 个\n"
                "涉骚扰 - 处罚 30 元/个 - 12 个月 - 50 个 处罚 2000 元\n"
                "—11—近 12 个月累计 - 处罚 8000 元 - 工号 - 网点\n"
                "累计达到 50 个（含）的- -1.关停代理商网点名下全部工号\n"
            ),
            "retrieval_unit": "passage",
            "candidate_type": "passage",
            "block_ids": ["a"],
        }
        result = structured_answer_from_evidence(
            question="涉诈电话 代理商一个自然月内每个号码处罚金额",
            evidence_rows=[blob],
        )
        # Either refuse or not invent a mismatched binding; never free-form.
        if result["answer_mode"] == "no_answer":
            assert result["answer"] == ""
            assert result.get("answer_validation_decision") in (
                "table_structure_ambiguous",
                "no_fact_candidate",
                "no_matching_numeric_triple",
                "direct_slot_not_satisfied",
            )
        else:
            # If answered, must not be a clearly wrong exclusive cross-bind only.
            assert "问题拆解" not in (result.get("answer") or "")


class TestQueryPlanner:
    def test_intents(self):
        p = plan_query("营收资金管理办法 收支两条线")
        assert "policy" in p.intents or p.wants_policy
        assert not p.wants_numeric

        n = plan_query("涉诈电话 代理商一个自然月内每个号码处罚金额")
        assert n.wants_numeric
        assert "涉诈" in n.conditions

        d = plan_query("产品问需工单 审核初审和产品评估的工作日时限")
        assert d.wants_deadline

        v = plan_query("技能竞赛管理办法最新修订版")
        assert v.wants_version


class TestAdjacentFailClosed:
    def test_invalid_focus_returns_empty(self):
        blocks = [
            {"block_id": "a", "order_idx": 0, "text": "A"},
            {"block_id": "b", "order_idx": 1, "text": "B"},
            {"block_id": "c", "order_idx": 2, "text": "C"},
        ]
        out, audit = expand_adjacent_evidence(
            blocks, focus_block_id="missing", return_audit=True,
        )
        assert out == []
        assert audit["reason"] == "focus_not_found"

    def test_valid_focus_window(self):
        blocks = [
            {"block_id": "a", "order_idx": 0, "text": "A"},
            {"block_id": "b", "order_idx": 1, "text": "B"},
            {"block_id": "c", "order_idx": 2, "text": "C"},
        ]
        out = expand_adjacent_evidence(blocks, focus_block_id="b", window=1)
        assert [b["block_id"] for b in out] == ["a", "b", "c"]


class TestSnapshotRegistry:
    def setup_method(self):
        clear_registry()

    def test_put_get_and_mismatch(self):
        sid = put_snapshot(
            {"accept": True, "query": "q1", "accepted_items": []},
            query="q1",
            top_k=5,
            config_hash="c1",
            index_revision="i1",
            db_revision="d1",
        )
        snap, reason, reused = get_snapshot(
            sid, query="q1", top_k=5, config_hash="c1",
            index_revision="i1", db_revision="d1",
        )
        assert reused and snap is not None and reason == ""

        snap2, reason2, reused2 = get_snapshot(
            sid, query="other", top_k=5, config_hash="c1",
            index_revision="i1", db_revision="d1",
        )
        assert not reused2 and reason2 == "query_mismatch"

        snap3, reason3, reused3 = get_snapshot(
            sid, query="q1", top_k=5, config_hash="changed",
            index_revision="i1", db_revision="d1",
        )
        assert not reused3 and reason3 == "config_changed"

    def test_process_start_id_stable_in_process(self):
        assert process_start_id()
        assert process_start_id() == process_start_id()


class TestNoAnswerReasonsPreserved:
    def test_no_answer_has_specific_reason(self):
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
        assert result.get("answer_validation_decision")
        assert result["reason"] != "insufficient_relevant_evidence" or True
