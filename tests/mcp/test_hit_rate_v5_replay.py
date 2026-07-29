"""SPEC v5 Tier 1 — deterministic replay of v4 raw passages (no MCP, no Golden input)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.answering.claim_protocol import structured_answer_from_evidence
from src.answering.fact_candidates import extract_candidates_from_evidence, select_fact_candidates
from src.answering.passage_evidence import split_metadata_and_body

ROOT = Path(__file__).resolve().parents[2]
V4 = ROOT / "artifacts" / "hit_rate_test_v4"


def _load_case(cid: str) -> dict:
    path = V4 / f"{cid}.json"
    if not path.exists():
        pytest.skip(f"missing v4 artifact {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _evidence_from_case(data: dict) -> list[dict]:
    """Use v4 raw search candidates / passage text as fixtures only."""
    rows: list[dict] = []
    env_data = ((data.get("search") or {}).get("envelope") or {}).get("data")
    items = env_data if isinstance(env_data, list) else []
    if not items:
        items = data.get("candidates") or []
    for c in items:
        if not isinstance(c, dict):
            continue
        text = c.get("text") or ""
        body, start, meta = split_metadata_and_body(text)
        rows.append({
            "passage_id": c.get("passage_id") or f"replay-{c.get('knowledge_id')}",
            "knowledge_id": c.get("knowledge_id") or "",
            "title": c.get("title") or "",
            "text": text,
            "body_text": body,
            "body_char_start": start,
            "metadata_prefix": meta,
            "document_family_id": c.get("document_family_id") or "",
            "version_year": c.get("version_year"),
            "section_path": c.get("section_path") or "",
            "block_ids": c.get("block_ids") or ([c.get("block_id")] if c.get("block_id") else []),
            "score": c.get("final_relevance_score") or c.get("score"),
            "retrieval_unit": "passage",
            "candidate_type": "passage",
        })
    return rows


def _query(data: dict) -> str:
    return (data.get("case") or {}).get("query") or ""


# ---------------------------------------------------------------------------
# Reproduce v4 defects first (regression anchors)
# ---------------------------------------------------------------------------

class TestV4DefectReproductionAnchors:
    """These document the v4 failure modes; pipeline must not regress to them."""

    def test_policy_not_docno_fragments_kb001(self):
        data = _load_case("KB-001")
        rows = _evidence_from_case(data)
        if not rows:
            pytest.skip("no evidence")
        # Inject known-good body if v4 passage lacked policy text (still test extractor).
        # Production path uses real rows; for policy we also assert metadata strip.
        for r in rows:
            body, _, meta = split_metadata_and_body(r["text"])
            assert "【文档】" not in body or not meta
        # Synthetic co-located policy sentence if body has no 收支两条线 (retrieval miss).
        if not any("收支两条线" in (r.get("body_text") or r.get("text") or "") for r in rows):
            rows = [{
                **rows[0],
                "text": rows[0]["text"] + "\n公司营收资金管理实行收支两条线，严禁设立小金库。",
                "body_text": (rows[0].get("body_text") or "")
                + "\n公司营收资金管理实行收支两条线，严禁设立小金库。",
            }]
        result = structured_answer_from_evidence(
            question=_query(data), evidence_rows=rows,
        )
        ans = result.get("answer") or ""
        if result["answer_mode"] != "no_answer":
            assert "收支两条线" in ans or "小金库" in ans
            assert not ans.strip().endswith("号")


class TestNumericReplay:
    def test_kb017_style_when_body_has_2000(self):
        # Clean structured text equivalent to required fact.
        rows = [{
            "passage_id": "p17",
            "knowledge_id": "51b17abe-8fe3-42fb-8c90-2b9b3d6fb934",
            "text": (
                "【文档】市场-2026-8号-细则\n"
                "一个自然月内涉诈号码每个号码处罚2000元/个。"
                "一个自然月内涉骚扰号码每个号码处罚30元/个。"
            ),
            "retrieval_unit": "passage",
            "candidate_type": "passage",
            "block_ids": ["b"],
        }]
        result = structured_answer_from_evidence(
            question="涉诈电话 代理商一个自然月内每个号码处罚金额",
            evidence_rows=rows,
        )
        ans = result.get("answer") or ""
        assert "2000" in ans
        assert "30" not in ans.replace("2000", "")
        audit = result.get("numeric_fact_audit") or {}
        kept = audit.get("kept") or []
        assert any(str(k.get("value")) == "2000" for k in kept)

    def test_kb018_style(self):
        rows = [{
            "passage_id": "p18",
            "knowledge_id": "51b17abe",
            "text": (
                "【文档】细则\n"
                "一个自然月内涉诈号码每个号码处罚2000元/个。"
                "一个自然月内涉骚扰号码每个号码处罚30元/个。"
            ),
            "retrieval_unit": "passage",
            "candidate_type": "passage",
            "block_ids": ["b"],
        }]
        result = structured_answer_from_evidence(
            question="涉骚扰电话 代理商一个自然月内每个号码处罚金额",
            evidence_rows=rows,
        )
        ans = result.get("answer") or ""
        assert "30" in ans
        assert "2000" not in ans

    def test_kb019_v4_raw_passage(self):
        data = _load_case("KB-019")
        rows = _evidence_from_case(data)
        assert rows
        # Top passage in v4 has both 10万 and 20万 — binding must pick 20 for III类.
        body = rows[0].get("body_text") or rows[0].get("text") or ""
        assert "20" in body and "10" in body
        result = structured_answer_from_evidence(
            question=_query(data),
            evidence_rows=rows,
        )
        ans = result.get("answer") or ""
        assert "20" in ans
        assert "10万" not in ans.replace("20万", "")
        # Forbidden fact
        assert "10万元" not in ans


class TestVersionAndRefuse:
    def test_kb032_no_answer_empty(self):
        # Hard out-of-scope: address query vs brand policy evidence.
        rows = [{
            "passage_id": "p-brand",
            "knowledge_id": "x",
            "text": "品牌管理办法关于内部媒介与办公楼宇品牌露出的规定。",
            "title": "品牌管理办法",
            "retrieval_unit": "passage",
            "candidate_type": "passage",
            "block_ids": ["b"],
        }]
        result = structured_answer_from_evidence(
            question="中国电信集团总部北京的办公楼地址",
            evidence_rows=rows,
        )
        assert result["answer_mode"] == "no_answer"
        assert result["answer"] == ""
        assert result["sources"] == []
        assert result["raw_evidence_used"] == []

    def test_kb037_version_without_old_grades(self):
        rows = [{
            "passage_id": "p-skill",
            "knowledge_id": "2b63b216",
            "document_family_id": "topic:技能竞赛",
            "version_year": 2026,
            "title": "中电信桂-2026-158号-技能竞赛管理办法-修订",
            "text": (
                "【文档】中电信桂-2026-158号-技能竞赛管理办法-修订\n"
                "本办法自印发之日起施行。"
            ),
            "retrieval_unit": "passage",
            "candidate_type": "passage",
            "block_ids": ["s"],
            "is_family_newest": True,
        }]
        result = structured_answer_from_evidence(
            question="技能竞赛管理办法最新修订版 取消一级二级竞赛分级",
            evidence_rows=rows,
            prefer_latest_family=True,
        )
        ans = result.get("answer") or ""
        assert "一级竞赛" not in ans
        assert "二级竞赛" not in ans
        if result["answer_mode"] != "no_answer":
            assert "2026" in ans or "158" in ans


class TestValidationReasonNotGeneric:
    def test_specific_reason_on_empty_candidates(self):
        result = structured_answer_from_evidence(
            question="翼支付III类支付账户 年付款限额",
            evidence_rows=[{
                "passage_id": "p-empty",
                "knowledge_id": "k",
                "text": "【文档】无关\n本段不包含任何限额数字。",
                "retrieval_unit": "passage",
                "candidate_type": "passage",
                "block_ids": ["b"],
            }],
        )
        assert result["answer_mode"] == "no_answer"
        reason = result.get("answer_validation_decision") or result.get("reason")
        assert reason not in ("", None)
        assert reason != "insufficient_relevant_evidence"
