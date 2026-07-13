"""Phase 2 strict-serving validation migration and proof-chain tests."""
from __future__ import annotations

from pathlib import Path

from src.models.wiki_v2 import Claim, ClaimServingValidation, ClaimStatus, Evidence, EvidenceStance
from src.services.wiki_feedback_service import WikiFeedbackService
from src.services.wiki_repository import WikiRepository
from src.services.wiki_serving_validation_migrator import WikiServingValidationMigrator
from src.services.wiki_validator import WikiValidator


def _repo(tmp_path: Path) -> WikiRepository:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    return WikiRepository(
        wiki_dir=wiki,
        registry_path=wiki / "_meta" / "pages.json",
        redirects_path=wiki / "_meta" / "redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )


def _claim(claim_id: str = "claim-1") -> Claim:
    return Claim(
        schema_version=1, claim_id=claim_id, statement="FTTR supports gigabit access",
        normalized_statement="fttr supports gigabit access", claim_type="fact",
        status=ClaimStatus.DRAFT, confidence=0.8, valid_from=None, valid_to=None,
        subject_refs=["fttr"], predicate="supports", object_refs=["gigabit"],
        evidence=[Evidence(
            evidence_id="e1", stance=EvidenceStance.SUPPORTS, knowledge_id="k1",
            block_id="b1", source_revision="v1", excerpt_hash="hash", observed_at="t",
        )], relations=[], created_at="t", updated_at="t", revision=1,
    )


def _save(repo: WikiRepository, claim: Claim) -> None:
    with repo.transaction() as tx:
        tx.stage_claim(claim)


def test_dry_run_never_invents_proof_and_creates_review_proposal(tmp_path):
    repo = _repo(tmp_path)
    claim = _claim()
    claim.status = ClaimStatus.ACTIVE
    _save(repo, claim)

    report = WikiServingValidationMigrator(repo).dry_run()

    assert report.missing_proof == 1
    assert report.review_proposals[0]["claim_id"] == "claim-1"
    assert repo.get_claim("claim-1").serving_validation is None


def test_dry_run_recognizes_only_current_complete_proof(tmp_path):
    repo = _repo(tmp_path)
    claim = _claim()
    claim.status = ClaimStatus.ACTIVE
    claim.serving_validation = ClaimServingValidation(
        passed=True, review_approved=True, validated_revision=1, published_revision=1,
        serving_evidence_ids=["e1"], validator_version="v1", validated_at="t",
    )
    _save(repo, claim)

    report = WikiServingValidationMigrator(repo).dry_run()

    assert report.already_current == 1
    assert report.missing_proof == 0


def test_review_validation_and_explicit_publish_are_separate_steps(tmp_path):
    repo = _repo(tmp_path)
    _save(repo, _claim())
    feedback = WikiFeedbackService(repository=repo, clock=lambda: "2026-07-13T00:00:00Z")

    confirmation = feedback.apply("claim-1", "confirm")
    reviewed = repo.get_claim("claim-1")
    assert not confirmation.errors
    assert reviewed.serving_validation.review_approved is True
    assert reviewed.serving_validation.passed is False
    assert reviewed.serving_validation.published_revision is None

    validator = WikiValidator()
    validation = validator.validate_and_record_serving(
        repo, "claim-1", validated_at="2026-07-13T00:01:00Z",
    )
    assert validation is not None and validation.passed is True
    assert validation.published_revision is None

    class Projection:
        def process_outbox(self):
            return type("Result", (), {"errors": []})()

        def verify_parity(self):
            return []

    published = validator.publish_serving_revision(
        repo, Projection(), "claim-1", published_at="2026-07-13T00:02:00Z",
    )
    assert published is not None
    assert published.published_revision == repo.get_claim("claim-1").revision


def test_publish_does_not_write_when_projection_parity_fails(tmp_path):
    repo = _repo(tmp_path)
    claim = _claim()
    claim.status = ClaimStatus.ACTIVE
    claim.serving_validation = ClaimServingValidation(
        passed=True, review_approved=True, validated_revision=1, published_revision=None,
        serving_evidence_ids=["e1"], validator_version="v1", validated_at="t",
    )
    _save(repo, claim)

    class BadProjection:
        def process_outbox(self):
            return type("Result", (), {"errors": []})()

        def verify_parity(self):
            return ["drift"]

    assert WikiValidator().publish_serving_revision(repo, BadProjection(), "claim-1", published_at="now") is None
    assert repo.get_claim("claim-1").serving_validation.published_revision is None
