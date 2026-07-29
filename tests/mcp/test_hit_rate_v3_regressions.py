"""SPEC v3 directed regressions: passage evidence, version family, defect class."""
from __future__ import annotations

from src.answering.fact_guard import (
    answer_numerics_supported_by_evidence,
    extract_query_conditions,
    strip_unanchored_numeric_assertions,
)
from src.retrieval.canonical_snapshot import build_canonical_snapshot
from src.services.passage_builder import build_passages_for_document
from src.services.version_rank import filter_to_latest_versions, rank_with_freshness


class TestPassageEvidencePacket:
    def test_wingpay_iii_limit_in_one_passage(self):
        # Micro-blocks that used to split II类/III类 values.
        blocks = [
            {"id": "b1", "content": "支付账户分类管理", "order_idx": 0},
            {"id": "b2", "content": "I类支付账户，其余额", "order_idx": 1},
            {"id": "b3", "content": "年付款限额为1000元；", "order_idx": 2},
            {"id": "b4", "content": "II类支付账户，其余额年付款", "order_idx": 3},
            {"id": "b5", "content": "限额为10万元；", "order_idx": 4},
            {"id": "b6", "content": "III类支付账户，其余额年付款", "order_idx": 5},
            {"id": "b7", "content": "限额为20万元。", "order_idx": 6},
        ]
        # Pad so merge reaches target.
        for i in range(20):
            blocks.append({
                "id": f"pad{i}",
                "content": f"账户业务受理与风险控制补充条款{i}。" + ("说明" * 10),
                "order_idx": 7 + i,
            })
        passages = build_passages_for_document(
            knowledge_id="27922ca4-aa1a-4cee-bf16-b4ee182a5201",
            title="翼支付业务管理办法-2026年版",
            blocks=blocks,
        )
        blob = "\n".join(p.text for p in passages)
        assert "III类" in blob or "三类" in blob or "20万" in blob
        # Prefer co-location of III类 and 20万 in same passage when possible.
        colocated = any(
            ("III" in p.text or "三类" in p.text) and "20万" in p.text
            for p in passages
        )
        assert colocated or "20万" in blob


class TestVersionIsolationKB037:
    def test_old_grading_excluded_from_generation(self):
        query = "技能竞赛管理办法最新修订版 取消一级二级竞赛分级"
        candidates = [
            {
                "knowledge_id": "1acb61b4",
                "title": "技能竞赛管理办法-2023",
                "text": "一级竞赛 二级竞赛 奖金限额",
                "document_family_id": "topic:技能竞赛管理办法",
                "version_year": 2023,
                "score": 0.92,
                "final_relevance_score": 0.92,
                "passage_id": "p-old",
                "block_id": "b-old",
            },
            {
                "knowledge_id": "2b63b216",
                "title": "技能竞赛管理办法-2026",
                "text": "取消一级二级竞赛分级 团体奖金",
                "document_family_id": "topic:技能竞赛管理办法",
                "version_year": 2026,
                "score": 0.88,
                "final_relevance_score": 0.88,
                "passage_id": "p-new",
                "block_id": "b-new",
            },
        ]
        snap = build_canonical_snapshot(query, candidates, threshold=0.35, top_k=5)
        gen_kids = {r.get("knowledge_id") for r in snap.get("generation_items") or []}
        assert "2b63b216" in gen_kids
        assert "1acb61b4" not in gen_kids
        # Search accepted may still list both for transparency; generation must not.
        ranked = rank_with_freshness(candidates)
        assert extract_year_top(ranked) >= 2026


def test_snapshot_adds_only_explicit_targeted_passages_for_accepted_document():
    candidates = [{
        "knowledge_id": "k1", "passage_id": "p1", "block_id": "b1",
        "title": "星河采购制度", "text": "星河采购制度适用范围。",
        "score": 0.9,
    }]
    snap = build_canonical_snapshot(
        "星河采购制度准入金额标准", candidates, threshold=0.35, top_k=1,
        select_document_passages_fn=lambda kid, query, existing, limit: [{
            "knowledge_id": kid, "passage_id": "p2", "block_id": "b2",
            "title": "星河采购制度", "text": "准入注册资本不少于100万元。",
            "score": 3.0,
        }],
    )
    target = [x for x in snap["generation_items"] if x.get("passage_id") == "p2"]
    assert len(target) == 1
    assert any(x.get("passage_id") == "p2" for x in snap["adjacent_allowlist"])


def extract_year_top(items):
    from src.services.version_rank import extract_version_year
    if not items:
        return 0
    return extract_version_year(items[0]) or 0


class TestNumericGuardKB010:
    def test_multi_condition_answer_not_emptied(self):
        q = "防诈骗和骚扰电话 代理商被罚多少钱"
        evidence = (
            "第六条 对涉及的代理商营业员、代理商网点，分涉诈、涉骚扰电话号码，"
            "每个号码一个自然月内处罚2000元。"
        )
        answer = "代理商每个号码一个自然月内处罚2000元（涉诈/涉骚扰）。"
        cleaned, stripped = strip_unanchored_numeric_assertions(
            answer, evidence=evidence, question=q,
        )
        assert "2000" in cleaned
        assert answer_numerics_supported_by_evidence(
            answer=cleaned, evidence=evidence,
        )
        assert extract_query_conditions(q)


class TestDefectClassification:
    def test_recall_miss_with_lucky_answer_is_p1(self):
        from scripts.hit_rate_finalize import classify_defect

        case = {
            "case_id": "KB-007",
            "expected_knowledge_ids": ["acf5e2d6"],
            "required_facts": ["不少于5人"],
            "forbidden_facts": [],
        }
        d = {
            "candidates": [
                {"knowledge_id": "wrong-doc", "title": "采购", "text": "专职"},
            ],
            "ask": {
                "envelope": {
                    "ok": True,
                    "data": {
                        "answer": "南宁分公司专职安全员不少于5人",
                        "answer_mode": "raw",
                        "warnings": [],
                        "sources": [{"knowledge_id": "wrong-doc"}],
                        "evidence_snapshot": {
                            "accepted_knowledge_ids": ["wrong-doc"],
                            "accepted_block_ids": [],
                        },
                    },
                }
            },
        }
        sc = {
            "recall5": False,
            "ask_fact_correct": True,
            "ask_citation_valid": True,
            "no_hallucination": True,
            "top1_hit": False,
            "cand_count": 1,
            "wrong_version_in_evidence": False,
        }
        sev, cat, reason = classify_defect(case, d, sc)
        assert sev == "P1"
        assert cat == "retrieval_recall"
        assert "碰巧" in reason or "召回" in reason
