"""Canonical Wiki V2 Claim 语义黄金评测集 — 确定性测试(Phase 3.5 / C2)。

消费 evals/wiki_v2/ 下 claim_matching / claim_merge / claim_extraction 黄金集,
用注入 scores / fake LLM / 固定 clock 跑,零 embedding/LLM 依赖,CI 可跑。

xfail case:当前 matcher 未满足保守契约的(单位/型号/地区/否定/强度词),
记录 gap,测试体 try/except + pytest.xfail 标记;将来 matcher 收紧后自动转 pass。
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    normalize_statement,
)
from src.services.wiki_claim_extractor import ClaimExtractionResult, ClaimExtractor, ExtractionBlock
from src.services.wiki_claim_matcher import ClaimMatcher
from src.services.wiki_merge_engine import WikiMergeEngine
from src.services.wiki_repository import WikiRepository

NOW = "2026-07-08T12:00:00+08:00"
EVAL_DIR = Path(__file__).resolve().parent.parent / "evals" / "wiki_v2"


def load_jsonl(name: str) -> list[dict]:
    path = EVAL_DIR / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _evidence(knowledge_id: str = "k1", stance: EvidenceStance = EvidenceStance.SUPPORTS) -> Evidence:
    return Evidence(
        evidence_id=f"ev_{uuid4().hex[:8]}", stance=stance,
        knowledge_id=knowledge_id, block_id="b1",
    )


def _claim_from(d: dict, claim_id: str | None = None, status: str = "active") -> Claim:
    """从黄金集紧凑 dict 构造 Claim。"""
    stance = EvidenceStance(d.get("stance", "supports"))
    return Claim(
        schema_version=1,
        claim_id=claim_id or d.get("id") or f"claim_{uuid4().hex[:8]}",
        statement=d["s"],
        normalized_statement=normalize_statement(d["s"]),
        claim_type="fact",
        status=ClaimStatus(status),
        confidence=0.9,
        valid_from=d.get("vf"),
        valid_to=d.get("vt"),
        subject_refs=list(d.get("sub", [])),
        predicate=d.get("pred", ""),
        object_refs=list(d.get("obj", [])),
        evidence=[_evidence(d.get("ev_knowledge", "k1"), stance)],
        relations=[],
        created_at=NOW, updated_at=NOW, revision=1,
    )


# ===========================================================================
# 1. matcher merge action 分类准确率(claim_matching.jsonl)
# ===========================================================================
@pytest.mark.parametrize("case", load_jsonl("claim_matching.jsonl"), ids=lambda c: c["id"])
def test_matching_action_and_reason_codes(case: dict):
    """每 case 断言 action + reason_codes;xfail case 当前判错→标 xfail。"""
    matcher = ClaimMatcher()
    new = _claim_from(case["new"], claim_id="new")
    candidates = [_claim_from(c, claim_id=c["id"]) for c in case["candidates"]]
    decision = matcher.match(new, candidates=candidates, scores=case["scores"])
    try:
        assert decision.action == case["expected_action"], (
            f"{case['id']}: action {decision.action} != {case['expected_action']}"
        )
        for code in case["expected_codes"]:
            assert code in decision.reason_codes, (
                f"{case['id']}: reason_codes {decision.reason_codes} 缺 {code}"
            )
    except AssertionError:
        if case.get("xfail"):
            pytest.xfail(f"{case['id']}: {case['xfail']} (actual action={decision.action})")
        raise


# ===========================================================================
# 2. merge engine 行为(claim_merge.jsonl)
# ===========================================================================
@pytest.fixture
def repo(tmp_path):
    return WikiRepository(
        wiki_dir=tmp_path / "wiki",
        registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
        redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
        outbox_path=tmp_path / "wiki_projection_outbox.jsonl",
    )


@pytest.mark.parametrize("case", load_jsonl("claim_merge.jsonl"), ids=lambda c: c["id"])
def test_merge_behavior(case: dict, repo: WikiRepository):
    from src.services.wiki_claim_matcher import ClaimMatchDecision

    target = _claim_from(case["target"], claim_id=case["target"]["claim_id"], status=case["target"]["status"])
    repo.save_claim(target)
    new_id = case["new"].get("claim_id", "new")
    new = _claim_from(case["new"], claim_id=new_id)
    decision = ClaimMatchDecision(action=case["decision"]["action"], target_claim_id=case["decision"]["target"])
    engine = WikiMergeEngine(repository=repo)
    result = engine.apply([(new, decision)], page=None, now=NOW)

    exp = case["expected"]
    assert sorted(result.claims_updated) == sorted(exp.get("claims_updated", [])), case["id"]
    assert sorted(result.claims_created) == sorted(exp.get("claims_created", [])), case["id"]
    if "claims_disputed" in exp:
        assert sorted(result.claims_disputed) == sorted(exp["claims_disputed"]), case["id"]
        got = repo.get_claim(case["target"]["claim_id"])
        assert got is not None and got.status.value == exp["status"], case["id"]
    if "claims_superseded" in exp:
        assert sorted(result.claims_superseded) == sorted(exp["claims_superseded"]), case["id"]
        assert sorted(result.claims_created_active) == sorted(exp["claims_created_active"]), case["id"]
    if "evidence_count" in exp:
        got = repo.get_claim(case["target"]["claim_id"])
        assert got is not None and len(got.evidence) == exp["evidence_count"], case["id"]
    if exp.get("skipped_nonempty"):
        assert result.skipped, case["id"]
    if exp.get("review_unresolved"):
        assert any(item.get("type") == "unresolved" for item in result.review_items), case["id"]


# ===========================================================================
# 3. extractor 抽取(claim_extraction.jsonl, fake LLM 注入响应)
# ===========================================================================
class _FakeLLM:
    def __init__(self, response):
        self._response = response

    def chat(self, messages, silent=False, max_tokens_override=None):
        if isinstance(self._response, str):
            return self._response
        return json.dumps(self._response, ensure_ascii=False)


@pytest.mark.parametrize("case", load_jsonl("claim_extraction.jsonl"), ids=lambda c: c["id"])
def test_extraction_behavior(case: dict):
    fake_llm = _FakeLLM(case["llm_response"])
    extractor = ClaimExtractor(
        llm=fake_llm,
        config={"wiki.claims.enabled": True, "wiki.claims.require_block_evidence": True,
                "max_claims_per_ingest": 30, "max_llm_calls_per_ingest": 4},
    )
    blocks = [
        ExtractionBlock(block_id=b["block_id"], content=b["content"],
                        location=b.get("location", {}), source_revision=b["source_revision"])
        for b in case["blocks"]
    ]
    result = extractor.extract(knowledge_id="k1", blocks=blocks, source_summary="FTTR", now=NOW)
    assert isinstance(result, ClaimExtractionResult)
    assert len(result.extracted_claims) == case["expected_claims_count"], case["id"]
    if case.get("expected_errors_nonempty"):
        assert result.errors, case["id"]
    if case.get("expected_warnings_nonempty"):
        assert result.warnings, case["id"]
    if case.get("expected_first_claim_block"):
        ev = result.extracted_claims[0].evidence[0]
        assert ev.block_id == case["expected_first_claim_block"], case["id"]
        assert ev.knowledge_id == "k1", case["id"]


# ===========================================================================
# 4. 数据集完整性(防漂移)
# ===========================================================================
def test_matching_dataset_has_expected_scenarios():
    cases = load_jsonl("claim_matching.jsonl")
    assert len(cases) >= 12
    # 至少覆盖:绿 case + xfail gap case
    assert any(not c.get("xfail") for c in cases), "缺少绿 case(锁定 matcher 正确行为)"
    assert any(c.get("xfail") for c in cases), "缺少 xfail case(记录保守性 gap)"


def test_source_datasets_marked_phase5():
    """source_update/source_delete 数据集标注 Phase 5 启用,当前不消费。"""
    for name in ("source_update.jsonl", "source_delete.jsonl"):
        for case in load_jsonl(name):
            assert case.get("note", "").startswith("Phase 5"), f"{name}:{case['id']} 未标 Phase 5"
