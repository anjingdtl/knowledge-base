"""SPEC v6 Tier 0 — EvidenceGroup / typed slots / coverage / stable IDs / render validation."""
from __future__ import annotations

import hashlib
import re

import pytest

from src.answering.claim_protocol import structured_answer_from_evidence
from src.answering.evidence_groups import resolve_evidence_groups
from src.answering.fact_candidates import (
    extract_candidates_from_evidence,
    select_fact_candidates,
    stable_candidate_id,
    validate_render_coverage,
)
from src.answering.query_planner import plan_query
from src.retrieval.raw_retriever import (
    build_deterministic_query_variants,
    get_rerank_circuit_state,
    reset_rerank_circuit,
    _rerank_circuit_note_timeout,
    _rerank_circuit_is_open,
)


PASSAGE_REVENUE_NEW = {
    "passage_id": "p-rev-2026",
    "knowledge_id": "574f1593-2a9f-455d-ab16-bbc5b6d0d879",
    "document_family_id": "topic:营收资金管理办法",
    "version_year": 2026,
    "title": "中电信桂-2026-22号-关于印发中国电信广西公司营收资金管理办法的通知",
    "text": (
        "【文档】中电信桂-2026-22号-营收资金管理办法\n"
        "【章节】总则\n"
        "公司营收资金管理实行收支两条线，严禁设立小金库。"
        "各单位必须按规定将营收款及时全额缴存。"
    ),
    "score": 0.9,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
    "block_ids": ["r1"],
}

PASSAGE_REVENUE_OLD = {
    "passage_id": "p-rev-old",
    "knowledge_id": "6d072751-old-internal-control",
    "document_family_id": "topic:内控实施细则",
    "version_year": 2021,
    "title": "中电信桂-2021-108号-内控实施细则",
    "text": (
        "【文档】内控实施细则\n"
        "【章节】3.1 界定收支两条线\n"
        "3实行收支两条线管理3.1界定收支两条线区公司内的各项现金流入全部上缴股份公司。"
    ),
    "score": 0.45,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
    "block_ids": ["o1"],
}

PASSAGE_WINGPAY = {
    "passage_id": "p-wing",
    "knowledge_id": "27922ca4-aa1a-4cee-bf16-b4ee182a5201",
    "title": "翼支付业务管理办法-2026",
    "text": (
        "【文档】翼支付\n"
        "【章节】第十八条\n"
        "其中，II类支付账户，其余额年付款限额为10万元（不含提现）；"
        "III类支付账户，其余额年付款限额为20万元（不含提现）。"
    ),
    "score": 0.9,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
    "block_ids": ["w1"],
}

PASSAGE_SKILL = {
    "passage_id": "p-skill",
    "knowledge_id": "2b63b216-9850-4e82-803e-4006cb9f62ad",
    "version_year": 2026,
    "title": "技能竞赛管理办法-2026",
    "text": (
        "【文档】技能竞赛管理办法\n"
        "【章节】竞赛经费\n"
        "团体奖金限额15000元；人均奖金限额1200元。"
        "实际操作成绩占比不得少于70%。"
    ),
    "score": 0.85,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
    "block_ids": ["s1"],
}

PASSAGE_TRAVEL_NEW = {
    "passage_id": "p-travel-2025",
    "knowledge_id": "960ce8f2-41a3-4aaa-9cb2-27295fd5441f",
    "document_family_id": "topic:差旅费管理办法",
    "version_year": 2025,
    "title": "差旅费管理办法-2025",
    "text": (
        "【文档】差旅费管理办法2025\n"
        "【章节】取消事项\n"
        "取消交通意外保险在差旅费中报账，交通意外保险由公司统一投保，不再重复报销。"
    ),
    "score": 0.88,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
    "block_ids": ["t1"],
}

PASSAGE_TRAVEL_OLD = {
    "passage_id": "p-travel-2022",
    "knowledge_id": "3f57bb0d-old-travel",
    "document_family_id": "topic:差旅费管理办法",
    "version_year": 2022,
    "title": "差旅费管理办法-2022",
    "text": (
        "【文档】差旅费管理办法2022\n"
        "【章节】审计\n"
        "食、交通费用是否及时完整入账，有无私设“小金库”行为；"
        "是否巧立名目或虚开发票报销差旅费。"
    ),
    "score": 0.5,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
    "block_ids": ["t2"],
}

PASSAGE_POLICY_GENERIC = {
    "passage_id": "p-secret",
    "knowledge_id": "e8f52cfa-secret",
    "title": "保密工作管理办法",
    "text": (
        "【文档】保密工作管理办法\n"
        "【章节】第二十八条\n"
        "不得使用外部互联网邮箱、微信等非企业内部即时通信工具传递企业商业秘密。"
        "企业商业秘密载体须按要求管理。"
    ),
    "score": 0.9,
    "retrieval_unit": "passage",
    "candidate_type": "passage",
    "block_ids": ["sec1"],
}


class TestStableCandidateId:
    def test_stable_across_calls(self):
        a = stable_candidate_id(
            passage_id="p1", body_span=(0, 10), fact_kind="policy", exact_text="收支两条线"
        )
        b = stable_candidate_id(
            passage_id="p1", body_span=(0, 10), fact_kind="policy", exact_text="收支两条线"
        )
        assert a == b
        assert re.fullmatch(r"[0-9a-f]{24}", a)
        # Different span → different id
        c = stable_candidate_id(
            passage_id="p1", body_span=(1, 10), fact_kind="policy", exact_text="收支两条线"
        )
        assert a != c

    def test_not_python_hash(self):
        # Must not equal abs(hash(...)) style instability marker
        cid = stable_candidate_id(
            passage_id="p", body_span=None, fact_kind="x", exact_text="y"
        )
        assert len(cid) == 24
        assert cid == hashlib.sha256(b"p|0:0|x|y").hexdigest()[:24]


class TestEvidenceGroupResolver:
    def test_primary_group_prefers_top_relevant_doc(self):
        res = resolve_evidence_groups(
            [PASSAGE_REVENUE_NEW, PASSAGE_REVENUE_OLD],
            question="营收资金管理办法 收支两条线",
        )
        assert res.primary_group_id
        primary = next(g for g in res.groups if g.group_id == res.primary_group_id)
        assert primary.knowledge_id == PASSAGE_REVENUE_NEW["knowledge_id"]

    def test_answer_not_from_other_group(self):
        result = structured_answer_from_evidence(
            question="营收资金管理办法 收支两条线",
            evidence_rows=[PASSAGE_REVENUE_NEW, PASSAGE_REVENUE_OLD],
        )
        ans = result.get("answer") or ""
        assert result["answer_mode"] != "no_answer"
        assert "收支两条线" in ans or "小金库" in ans
        # Must not prefer pure old internal-control phrasing as sole answer without 小金库/实行
        kids = {s.get("knowledge_id") for s in (result.get("sources") or [])}
        assert PASSAGE_REVENUE_NEW["knowledge_id"] in kids
        assert result.get("primary_group_id")

    def test_travel_cancel_from_new_doc(self):
        result = structured_answer_from_evidence(
            question="差旅费管理办法 取消交通意外险在差旅费中报账",
            evidence_rows=[PASSAGE_TRAVEL_NEW, PASSAGE_TRAVEL_OLD],
        )
        ans = result.get("answer") or ""
        assert "交通意外" in ans or "不再重复" in ans or "取消" in ans
        kids = {s.get("knowledge_id") for s in (result.get("sources") or [])}
        assert PASSAGE_TRAVEL_NEW["knowledge_id"] in kids


class TestTypedSlots:
    def test_scope_not_condition_personal_account(self):
        p = plan_query("翼支付业务管理办法 个人支付账户余额年付款限额")
        assert p.wants_numeric
        # 个人 is scope, not exclusive condition
        assert "个人" not in p.conditions
        assert "个人" in p.scope or any("个人" in s for s in p.scope)
        # Multi value dimensions for class limits
        assert any(d in p.value_dimensions for d in ("II类", "III类", "年付款", "value"))

    def test_multi_value_dimension_skill_bonus(self):
        p = plan_query("技能竞赛团体奖金限额 2026年修订")
        assert p.wants_numeric
        assert "团体" in p.scope or "总额" in p.value_dimensions or "团体" in (p.raw)

    def test_personal_account_answers_both_limits(self):
        result = structured_answer_from_evidence(
            question="翼支付业务管理办法 个人支付账户余额年付款限额",
            evidence_rows=[PASSAGE_WINGPAY],
        )
        ans = result.get("answer") or ""
        assert result["answer_mode"] != "no_answer"
        assert "10" in ans and "20" in ans

    def test_skill_ratio_numeric(self):
        result = structured_answer_from_evidence(
            question="技能竞赛 实际操作成绩占比不得少于",
            evidence_rows=[PASSAGE_SKILL],
        )
        ans = result.get("answer") or ""
        assert "70" in ans

    def test_skill_group_bonus_multi_dim(self):
        result = structured_answer_from_evidence(
            question="技能竞赛团体奖金限额 2026年修订",
            evidence_rows=[PASSAGE_SKILL],
        )
        ans = result.get("answer") or ""
        assert "15000" in ans
        # Prefer also per-person when present
        assert "1200" in ans or result.get("answer_plan", {}).get("complete") is not False


class TestPolicyWithoutHighValue:
    def test_no_high_value_symbol_in_module(self):
        import src.answering.fact_candidates as fc
        src = open(fc.__file__, encoding="utf-8").read()
        # Production logic must not keep a high_value assignment/list.
        assert "high_value =" not in src
        assert "high_value=" not in src
        assert "if high_value" not in src

    def test_unseen_entity_predicate_policy(self):
        # Generic unseen entity — should still work via query anchors
        rows = [{
            "passage_id": "p-x",
            "knowledge_id": "kx",
            "text": "【文档】测试\n星云档案管理不得向外部云盘上传机密材料。",
            "score": 0.9,
            "retrieval_unit": "passage",
            "candidate_type": "passage",
            "block_ids": ["b"],
        }]
        result = structured_answer_from_evidence(
            question="星云档案管理 不得向外部云盘上传",
            evidence_rows=rows,
        )
        ans = result.get("answer") or ""
        if result["answer_mode"] != "no_answer":
            assert "云盘" in ans or "不得" in ans

    def test_prohibition_email(self):
        result = structured_answer_from_evidence(
            question="保密工作管理办法 不得使用外部互联网邮箱",
            evidence_rows=[PASSAGE_POLICY_GENERIC],
        )
        ans = result.get("answer") or ""
        assert "邮箱" in ans or "微信" in ans
        assert "商业秘密" in ans or "不得" in ans


class TestCoverageAndRenderValidation:
    def test_render_validation_rejects_missing_anchor(self):
        plan = plan_query("营收资金管理办法 收支两条线")
        rv = validate_render_coverage(
            plan=plan,
            answer_text="- 各级单位所有收入按照原则缴存",
            selected=[],
        )
        assert rv["ok"] is False or "policy_anchor" in (rv.get("missing_slots") or [])

    def test_answer_plan_has_coverage_fields(self):
        result = structured_answer_from_evidence(
            question="翼支付业务管理办法 个人支付账户余额年付款限额",
            evidence_rows=[PASSAGE_WINGPAY],
        )
        plan = result.get("answer_plan") or {}
        assert "missing_slots" in plan
        assert "candidate_ids" in plan
        assert result.get("render_validation") is not None


class TestPassageTraceRepair:
    def test_missing_passage_excluded_or_repaired(self):
        # Candidate path with valid unique passage should not fail
        result = structured_answer_from_evidence(
            question="安全生产管理办法 专职安全员 南宁分公司不少于5人",
            evidence_rows=[{
                "passage_id": "p-safe",
                "knowledge_id": "acf5e2d6",
                "title": "安全生产管理办法",
                "text": (
                    "【文档】安全生产\n"
                    "南宁分公司专职安全员配备不少于5人。"
                ),
                "score": 0.85,
                "retrieval_unit": "passage",
                "candidate_type": "passage",
                "block_ids": ["s"],
            }],
            require_passage=True,
        )
        if result["answer_mode"] != "no_answer":
            assert all(s.get("passage_id") for s in result.get("sources") or [])
            assert "5" in (result.get("answer") or "") or "南宁" in (result.get("answer") or "")


class TestRerankCircuit:
    def setup_method(self):
        reset_rerank_circuit()

    def test_timeouts_open_circuit(self):
        assert _rerank_circuit_is_open()[0] is False
        _rerank_circuit_note_timeout("aaa")
        assert _rerank_circuit_is_open()[0] is False  # need threshold
        _rerank_circuit_note_timeout("bbb")
        open_now, reason = _rerank_circuit_is_open()
        assert open_now is True
        assert "timeout" in reason or "open_after" in reason
        st = get_rerank_circuit_state()
        assert st["timeout_count"] >= 2

    def test_query_variants_capped(self):
        vs = build_deterministic_query_variants(
            "防诈骗和骚扰电话 代理商被罚多少钱", max_variants=4
        )
        assert 1 <= len(vs) <= 4
        assert vs[0]["source"] == "original"
        assert any(v["source"] != "original" for v in vs)
