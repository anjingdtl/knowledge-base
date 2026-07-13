"""WikiValidator.validate_canonical_store 测试(Phase 6 T6.2)。"""
from __future__ import annotations

from pathlib import Path

from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    PageStatus,
    PageType,
    WikiPage,
)
from src.services.wiki_repository import WikiRepository
from src.services.wiki_validator import WikiValidator


def _repo(tmp: Path) -> WikiRepository:
    wiki = tmp / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    return WikiRepository(
        wiki_dir=wiki,
        registry_path=wiki / "_meta" / "pages.json",
        redirects_path=wiki / "_meta" / "redirects.json",
        outbox_path=tmp / "outbox.jsonl",
    )


def test_page_only_evidence_warning(tmp_path):
    repo = _repo(tmp_path)
    claim = Claim(
        schema_version=1, claim_id="c1", statement="s", normalized_statement="s",
        claim_type="fact", status=ClaimStatus.ACTIVE, confidence=0.9,
        valid_from=None, valid_to=None, subject_refs=[], predicate="p", object_refs=[],
        evidence=[Evidence(
            evidence_id="e1", stance=EvidenceStance.SUPPORTS, knowledge_id="k1",
            block_id=None, source_revision="mig",
        )],
        relations=[], created_at="t", updated_at="t", revision=1,
    )
    with repo.transaction() as tx:
        tx.stage_claim(claim)
    findings = WikiValidator().validate_canonical_store(repo)
    cats = {f.category for f in findings}
    assert "page_only_evidence" in cats
    assert all(f.severity == "warning" for f in findings if f.category == "page_only_evidence")


def test_published_page_with_disputed_claim_warning(tmp_path):
    repo = _repo(tmp_path)
    claim = Claim(
        schema_version=1, claim_id="c2", statement="s", normalized_statement="s",
        claim_type="fact", status=ClaimStatus.DISPUTED, confidence=0.5,
        valid_from=None, valid_to=None, subject_refs=[], predicate="p", object_refs=[],
        evidence=[Evidence(
            evidence_id="e2", stance=EvidenceStance.SUPPORTS, knowledge_id="k1",
            block_id="b1",
        )],
        relations=[], created_at="t", updated_at="t", revision=1,
    )
    page = WikiPage(
        schema_version=1, page_id="p1", title="P1", page_type=PageType.CONCEPTS,
        status=PageStatus.PUBLISHED, revision=1, aliases=[], tags=[], source_ids=[],
        claim_ids=["c2"], created_at="t", updated_at="t", content_hash="h", body="b",
    )
    with repo.transaction() as tx:
        tx.stage_claim(claim)
        tx.stage_page(page)
    findings = WikiValidator().validate_canonical_store(repo)
    assert any(f.category == "unresolved_conflict" for f in findings)
