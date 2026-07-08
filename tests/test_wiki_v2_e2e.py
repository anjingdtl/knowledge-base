"""E2E-1: Two sources support the same fact -> 1 Claim, 2 Evidence.

Spec 13.2: extractor -> matcher -> merge pipeline integration.
Uses real ClaimMatcher (with injected scores) + real WikiMergeEngine.
Extractor is hand-crafted Claims (E2E focuses on merge, not extraction).
"""
from __future__ import annotations

from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    PageStatus,
    PageType,
    WikiPage,
)
from src.services.wiki_claim_matcher import ClaimMatcher, _normalize
from src.services.wiki_merge_engine import WikiMergeEngine
from src.services.wiki_repository import WikiRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
NOW = "2026-07-08T12:00:00+08:00"


def _make_page(page_id: str = "concepts/fttr") -> WikiPage:
    return WikiPage(
        schema_version=1,
        page_id=page_id,
        title="FTTR",
        page_type=PageType.CONCEPTS,
        status=PageStatus.PUBLISHED,
        revision=1,
        aliases=[],
        tags=[],
        source_ids=[],
        claim_ids=[],
        created_at=NOW,
        updated_at=NOW,
        content_hash="h0",
        body="",
    )


def _claim(
    claim_id: str,
    statement: str,
    knowledge_id: str,
    block_id: str,
) -> Claim:
    ev = Evidence(
        evidence_id=f"ev_{claim_id}",
        stance=EvidenceStance.SUPPORTS,
        knowledge_id=knowledge_id,
        block_id=block_id,
    )
    return Claim(
        schema_version=1,
        claim_id=claim_id,
        statement=statement,
        normalized_statement=_normalize(statement),
        claim_type="fact",
        status=ClaimStatus.ACTIVE,
        confidence=0.9,
        valid_from=None,
        valid_to=None,
        subject_refs=[],
        predicate="",
        object_refs=[],
        evidence=[ev],
        relations=[],
        created_at=NOW,
        updated_at=NOW,
        revision=1,
    )


# ---------------------------------------------------------------------------
# E2E-1: Two sources -> 1 Claim, 2 Evidence
# ---------------------------------------------------------------------------
class TestE2E1:
    """Two sources supporting the same fact should result in 1 claim with 2 evidence items.

    Flow:
    1. Source A claim pre-seeded as ACTIVE in repo (simulating prior extraction+merge).
    2. Source B provides same fact from different source.
    3. Matcher decides "supports" (injected score >= semantic threshold).
    4. Merge engine adds evidence from source B to existing claim.
    5. Result: 1 claim, 2 evidence items (k_source_a + k_source_b).
    """

    def test_two_sources_one_claim_two_evidence(self, tmp_path):
        repo = WikiRepository(
            wiki_dir=tmp_path / "wiki",
            registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
            redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
            outbox_path=tmp_path / "outbox.jsonl",
        )
        matcher = ClaimMatcher()
        engine = WikiMergeEngine(repository=repo)

        statement = "FTTR下行速率可达100Mbps"
        statement_b = "FTTR下行速率支持100Mbps"  # slightly different wording

        # --- Source A: pre-seed as ACTIVE claim (simulates prior merge) ---
        claim_a = _claim("claim_a", statement, knowledge_id="k_source_a", block_id="b1")
        repo.save_claim(claim_a, expected_revision=None)

        # --- Source B: same fact, different source -> matcher says "supports" ---
        claim_b = _claim("claim_b", statement_b, knowledge_id="k_source_b", block_id="b2")
        existing = repo.list_claims()
        assert len(existing) == 1

        # Inject score to guarantee "supports" (above semantic threshold, below exact)
        decision_b = matcher.match(claim_b, candidates=existing, scores={"claim_a": 0.92})

        assert decision_b.action == "supports"
        assert decision_b.target_claim_id == "claim_a"

        result = engine.apply([(claim_b, decision_b)], now=NOW)
        assert result.committed is True
        assert result.claims_created == []
        assert "claim_a" in result.claims_updated

        # --- Assertions: 1 claim, 2 evidence ---
        all_claims = repo.list_claims()
        assert len(all_claims) == 1

        sole_claim = all_claims[0]
        assert sole_claim.claim_id == "claim_a"
        assert sole_claim.status == ClaimStatus.ACTIVE
        assert len(sole_claim.evidence) == 2

        knowledge_ids = {e.knowledge_id for e in sole_claim.evidence}
        assert knowledge_ids == {"k_source_a", "k_source_b"}
