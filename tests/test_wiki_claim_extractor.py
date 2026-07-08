"""ClaimExtractor 单元测试 — T3.1 Claim 抽取器。

9 个 mock LLM 确定性测试 + 1 个 skipif 门控真实 LLM 测试。
"""
import json
import os
from unittest.mock import MagicMock

import pytest

from src.models.wiki_v2 import Claim, ClaimStatus
from src.services.wiki_claim_extractor import ClaimExtractor, ExtractionBlock


def _blk(
    block_id: str,
    content: str,
    location: dict | None = None,
    source_revision: str = "sha256:src1",
    excerpt_hash: str | None = None,
) -> ExtractionBlock:
    return ExtractionBlock(
        block_id=block_id,
        content=content,
        location=location or {"paragraph_index": 0},
        source_revision=source_revision,
        excerpt_hash=excerpt_hash,
    )


def _make_extractor(llm_response: str) -> ClaimExtractor:
    llm = MagicMock()
    llm.chat.return_value = llm_response
    return ClaimExtractor(llm=llm)


# ---------------------------------------------------------------------------
# 测试 1: 多 block 多 claim
# ---------------------------------------------------------------------------
def test_extract_multi_block_multi_claim():
    blocks = [
        _blk("b1", "FTTR使用光纤将千兆宽带延伸到房间。GPON是主要的PON技术。"),
        _blk("b2", "Wi-Fi 6E支持6GHz频段，最大吞吐量可达2.4Gbps。"),
    ]
    llm_json = json.dumps({
        "claims": [
            {
                "statement": "FTTR使用光纤将千兆宽带延伸到房间",
                "claim_type": "fact",
                "confidence": 0.9,
                "evidence_block_id": "b1",
                "stance": "supports",
                "subject_refs": ["FTTR"],
                "predicate": "使用",
                "object_refs": ["光纤"],
            },
            {
                "statement": "Wi-Fi 6E支持6GHz频段",
                "claim_type": "fact",
                "confidence": 0.85,
                "evidence_block_id": "b2",
                "stance": "supports",
                "subject_refs": ["Wi-Fi 6E"],
                "predicate": "支持",
                "object_refs": ["6GHz频段"],
            },
        ]
    })
    extractor = _make_extractor(llm_json)
    result = extractor.extract(
        knowledge_id="k1",
        blocks=blocks,
        source_summary="宽带技术文档",
        now="2026-07-08T00:00:00+08:00",
    )
    assert len(result.extracted_claims) == 2
    assert result.llm_calls >= 1

    # 每条 evidence.block_id 对应
    block_ids = {c.evidence[0].block_id for c in result.extracted_claims}
    assert block_ids == {"b1", "b2"}

    # evidence.location 从 block 拷贝
    for c in result.extracted_claims:
        assert c.evidence[0].location == {"paragraph_index": 0}


# ---------------------------------------------------------------------------
# 测试 2: 每条 claim 都携带 evidence
# ---------------------------------------------------------------------------
def test_every_claim_carries_evidence():
    blocks = [_blk("b1", "Python是一种解释型编程语言，由Guido van Rossum创建。")]
    llm_json = json.dumps({
        "claims": [
            {
                "statement": "Python是一种解释型编程语言",
                "claim_type": "definition",
                "confidence": 0.95,
                "evidence_block_id": "b1",
                "stance": "supports",
                "subject_refs": ["Python"],
                "predicate": "是",
                "object_refs": ["解释型编程语言"],
            },
        ]
    })
    extractor = _make_extractor(llm_json)
    result = extractor.extract(
        knowledge_id="k1",
        blocks=blocks,
        source_summary="编程语言概述",
        now="2026-07-08T00:00:00+08:00",
    )
    assert len(result.extracted_claims) == 1
    claim = result.extracted_claims[0]
    assert len(claim.evidence) >= 1
    ev = claim.evidence[0]
    assert ev.knowledge_id == "k1"
    assert ev.source_revision == "sha256:src1"
    assert ev.block_id == "b1"


# ---------------------------------------------------------------------------
# 测试 3: location 完整保留
# ---------------------------------------------------------------------------
def test_location_preserved():
    loc = {"heading_path": ["技术方案"], "paragraph_index": 3}
    blocks = [_blk("b1", "系统采用微服务架构，每个服务独立部署。", location=loc)]
    llm_json = json.dumps({
        "claims": [
            {
                "statement": "系统采用微服务架构",
                "claim_type": "fact",
                "confidence": 0.9,
                "evidence_block_id": "b1",
                "stance": "supports",
                "subject_refs": ["系统"],
                "predicate": "采用",
                "object_refs": ["微服务架构"],
            },
        ]
    })
    extractor = _make_extractor(llm_json)
    result = extractor.extract(
        knowledge_id="k1",
        blocks=blocks,
        source_summary="架构文档",
        now="2026-07-08T00:00:00+08:00",
    )
    assert len(result.extracted_claims) == 1
    assert result.extracted_claims[0].evidence[0].location == loc


# ---------------------------------------------------------------------------
# 测试 4: LLM 返回非 JSON 降级
# ---------------------------------------------------------------------------
def test_llm_returns_non_json_degrades_gracefully():
    blocks = [_blk("b1", "这是一段关于网络协议的描述文本。")]
    extractor = _make_extractor("抱歉，我无法处理")
    result = extractor.extract(
        knowledge_id="k1",
        blocks=blocks,
        source_summary="网络协议文档",
        now="2026-07-08T00:00:00+08:00",
    )
    assert len(result.extracted_claims) == 0
    assert result.llm_calls >= 1
    assert len(result.errors) > 0 or len(result.warnings) > 0


# ---------------------------------------------------------------------------
# 测试 5: LLM 超时降级
# ---------------------------------------------------------------------------
def test_llm_timeout_degrades():
    blocks = [_blk("b1", "这是一段测试文本。")]
    llm = MagicMock()
    llm.chat.side_effect = RuntimeError("timeout")
    extractor = ClaimExtractor(llm=llm)
    result = extractor.extract(
        knowledge_id="k1",
        blocks=blocks,
        source_summary="测试文档",
        now="2026-07-08T00:00:00+08:00",
    )
    assert len(result.extracted_claims) == 0
    assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# 测试 6: 超过 max_claims 截断
# ---------------------------------------------------------------------------
def test_over_max_claims_truncates():
    blocks = [_blk("b1", "第一个事实。第二个事实。第三个事实。第四个事实。")]
    claims_data = []
    for i in range(4):
        claims_data.append({
            "statement": f"第{i + 1}个事实",
            "claim_type": "fact",
            "confidence": 0.8,
            "evidence_block_id": "b1",
            "stance": "supports",
            "subject_refs": [],
            "predicate": "",
            "object_refs": [],
        })
    llm_json = json.dumps({"claims": claims_data})
    extractor = _make_extractor(llm_json)
    result = extractor.extract(
        knowledge_id="k1",
        blocks=blocks,
        source_summary="测试",
        now="2026-07-08T00:00:00+08:00",
        max_claims=2,
    )
    assert len(result.extracted_claims) <= 2


# ---------------------------------------------------------------------------
# 测试 7: 重复 statement 去重
# ---------------------------------------------------------------------------
def test_duplicate_fragments_deduped():
    blocks = [
        _blk("b1", "FTTR使用光纤。"),
        _blk("b2", "FTTR使用光纤。"),
    ]
    llm_json = json.dumps({
        "claims": [
            {
                "statement": "FTTR使用光纤",
                "claim_type": "fact",
                "confidence": 0.9,
                "evidence_block_id": "b1",
                "stance": "supports",
                "subject_refs": ["FTTR"],
                "predicate": "使用",
                "object_refs": ["光纤"],
            },
            {
                "statement": "FTTR使用光纤",
                "claim_type": "fact",
                "confidence": 0.9,
                "evidence_block_id": "b2",
                "stance": "supports",
                "subject_refs": ["FTTR"],
                "predicate": "使用",
                "object_refs": ["光纤"],
            },
        ]
    })
    extractor = _make_extractor(llm_json)
    result = extractor.extract(
        knowledge_id="k1",
        blocks=blocks,
        source_summary="测试",
        now="2026-07-08T00:00:00+08:00",
    )
    # 归一化去重后只保留 1 条
    assert len(result.extracted_claims) <= 1


# ---------------------------------------------------------------------------
# 测试 8: 无可验证事实 → 空，不调 LLM
# ---------------------------------------------------------------------------
def test_no_verifiable_facts_returns_empty():
    # 只有标题级别的内容，应被规则筛掉
    blocks = [_blk("b1", "目录")]
    extractor = _make_extractor('{"claims":[]}')
    result = extractor.extract(
        knowledge_id="k1",
        blocks=blocks,
        source_summary="测试",
        now="2026-07-08T00:00:00+08:00",
    )
    assert len(result.extracted_claims) == 0
    assert result.llm_calls == 0


# ---------------------------------------------------------------------------
# 测试 9: candidate 去重
# ---------------------------------------------------------------------------
def test_candidate_dedup_skips_existing():
    blocks = [
        _blk("b1", "FTTR使用光纤到房间技术提供千兆接入。"),
    ]
    # candidate 已有 normalized_statement 与 fragment 相同
    candidate = Claim(
        schema_version=1,
        claim_id="claim_existing",
        statement="FTTR使用光纤到房间技术提供千兆接入",
        normalized_statement="fttr使用光纤到房间技术提供千兆接入",
        claim_type="fact",
        status=ClaimStatus.ACTIVE,
        confidence=0.9,
        valid_from=None,
        valid_to=None,
        subject_refs=["FTTR"],
        predicate="使用",
        object_refs=["光纤到房间技术"],
        evidence=[],
        relations=[],
        created_at="2026-07-01T00:00:00+08:00",
        updated_at="2026-07-01T00:00:00+08:00",
        revision=1,
    )
    llm_json = json.dumps({
        "claims": [
            {
                "statement": "FTTR使用光纤到房间技术提供千兆接入",
                "claim_type": "fact",
                "confidence": 0.9,
                "evidence_block_id": "b1",
                "stance": "supports",
                "subject_refs": ["FTTR"],
                "predicate": "使用",
                "object_refs": ["光纤到房间技术"],
            },
        ]
    })
    extractor = _make_extractor(llm_json)
    result = extractor.extract(
        knowledge_id="k1",
        blocks=blocks,
        source_summary="测试",
        now="2026-07-08T00:00:00+08:00",
        candidate_claims=[candidate],
    )
    # 该 claim 被候选去重跳过
    assert len(result.extracted_claims) == 0


# ---------------------------------------------------------------------------
# 测试 10: 真实 LLM（skipif 门控，默认 skip）
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not os.environ.get("RUN_LLM_LIVE"),
    reason="needs live LLM; set RUN_LLM_LIVE=1",
)
def test_extract_with_real_llm():
    from src.services.llm import LLMService

    llm = LLMService()
    blocks = [
        _blk("b1", "FTTR（光纤到房间）通过将光纤延伸到房间级的分配点来实现千兆宽带接入。"),
    ]
    extractor = ClaimExtractor(llm=llm)
    result = extractor.extract(
        knowledge_id="k1",
        blocks=blocks,
        source_summary="宽带接入技术文档",
        now="2026-07-08T00:00:00+08:00",
    )
    assert len(result.extracted_claims) >= 1, "期望真实 LLM 至少抽取 1 条 claim"
    for claim in result.extracted_claims:
        assert len(claim.evidence) >= 1
        assert claim.evidence[0].block_id == "b1"
