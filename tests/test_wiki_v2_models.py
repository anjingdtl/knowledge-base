from __future__ import annotations

import pytest

from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    PageRegistryEntry,
    PageType,
    SaveResult,
    ValidationFinding,
    WikiPage,
)


def _sample_page(**over) -> dict:
    d = dict(
        schema_version=2, page_id="page_abc", title="FTTR", page_type="concepts",
        status="draft", revision=1, aliases=[], tags=[], source_ids=["k1"],
        claim_ids=[], created_at="2026-07-07T12:00:00+08:00",
        updated_at="2026-07-07T12:00:00+08:00", content_hash="sha256:x",
        body="# FTTR\n", supersedes_page_id=None,
    )
    d.update(over)
    return d


def _sample_claim(**over) -> dict:
    d = dict(
        schema_version=1, claim_id="claim_abc",
        statement="FTTR 使用光纤。", normalized_statement="fttr使用光纤",
        claim_type="fact", status="active", confidence=0.9,
        valid_from=None, valid_to=None, subject_refs=["entity:FTTR"],
        predicate="uses", object_refs=["concept:fiber"],
        evidence=[
            dict(evidence_id="ev1", stance="supports", knowledge_id="k1",
                 block_id="b1", location={"page": 1}, source_revision="sha256:s",
                 excerpt_hash="sha256:e", observed_at="2026-07-07T12:00:00+08:00"),
        ],
        relations=[], created_at="2026-07-07T12:00:00+08:00",
        updated_at="2026-07-07T12:00:00+08:00", revision=1,
    )
    d.update(over)
    return d


def test_page_roundtrip_strict():
    p = WikiPage.from_dict(_sample_page(), strict=True)
    assert p.page_id == "page_abc" and p.page_type is PageType.CONCEPTS
    out = p.to_dict()
    again = WikiPage.from_dict(out, strict=True)
    assert again == p


def test_page_missing_required_strict_fails():
    d = _sample_page()
    d.pop("page_id")
    with pytest.raises((ValueError, TypeError)):
        WikiPage.from_dict(d, strict=True)


def test_page_invalid_status_fails():
    with pytest.raises(ValueError):
        WikiPage.from_dict(_sample_page(status="bogus"), strict=True)


def test_page_invalid_revision_fails():
    with pytest.raises(ValueError):
        WikiPage.from_dict(_sample_page(revision=0), strict=True)
    with pytest.raises(ValueError):
        WikiPage.from_dict(_sample_page(revision=-1), strict=True)


def test_claim_roundtrip_strict():
    c = Claim.from_dict(_sample_claim(), strict=True)
    assert c.status is ClaimStatus.ACTIVE and c.evidence[0].stance is EvidenceStance.SUPPORTS
    assert Claim.from_dict(c.to_dict(), strict=True) == c


def test_active_claim_without_supports_evidence_invalid():
    # active Claim 必须至少一条有效 supports Evidence
    d = _sample_claim(status="active", evidence=[
        dict(evidence_id="ev1", stance="contradicts", knowledge_id="k1",
             block_id="b1", location={}, source_revision="sha256:s",
             excerpt_hash=None, observed_at="2026-07-07T12:00:00+08:00"),
    ])
    c = Claim.from_dict(d, strict=True)
    errors = c.validate()
    assert any("supports" in e for e in errors)


def test_evidence_requires_knowledge_id():
    d = _sample_claim()
    d["evidence"][0]["knowledge_id"] = ""
    with pytest.raises(ValueError):
        Claim.from_dict(d, strict=True)


def test_compat_mode_tolerates_unknown_keys():
    d = _sample_page()
    d["future_field"] = "x"
    # strict=True 拒绝未知键;strict=False 容忍
    with pytest.raises(ValueError):
        WikiPage.from_dict(d, strict=True)
    WikiPage.from_dict(d, strict=False)  # 不抛


def test_save_result_and_validation_finding_dataclasses():
    sr = SaveResult(ok=True, object_id="page_abc", revision=2, warnings=[], outbox_events=["page.updated"])
    assert sr.to_dict()["ok"] is True
    vf = ValidationFinding(path="concepts/fttr.md", object_id="page_abc",
                           category="schema_invalid", severity="error", message="bad")
    assert vf.severity == "error"


def test_page_registry_entry_roundtrip():
    e = PageRegistryEntry(path="concepts/fttr.md", title="FTTR",
                          page_type="concepts", revision=1, content_hash="sha256:x")
    assert PageRegistryEntry.from_dict(e.to_dict()) == e


def test_evidence_stale_roundtrip():
    """Evidence.stale/stale_at 序列化往返(Phase 5)。"""
    ev = Evidence(
        evidence_id="ev1", stance=EvidenceStance.SUPPORTS, knowledge_id="k1",
        block_id="b1", source_revision="v1", excerpt_hash="h1",
        stale=True, stale_at="2026-07-13T10:00:00",
    )
    d = ev.to_dict()
    assert d["stale"] is True
    assert d["stale_at"] == "2026-07-13T10:00:00"
    back = Evidence.from_dict(d)
    assert back.stale is True
    assert back.stale_at == "2026-07-13T10:00:00"


def test_evidence_stale_defaults_and_legacy_compat():
    """新 Evidence 默认 stale=False;旧 dict 无 stale 字段时 strict=False 兼容。"""
    ev = Evidence(evidence_id="ev2", stance=EvidenceStance.SUPPORTS, knowledge_id="k1")
    assert ev.stale is False
    assert ev.stale_at == ""
    legacy = {
        "evidence_id": "ev3", "stance": "supports", "knowledge_id": "k1",
    }
    back = Evidence.from_dict(legacy, strict=False)
    assert back.stale is False
    assert back.stale_at == ""
