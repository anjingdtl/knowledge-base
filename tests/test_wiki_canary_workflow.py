from pathlib import Path

from src.models.block import Block
from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    normalize_statement,
)
from src.services.wiki_canary_workflow import WikiCanaryWorkflow
from src.services.wiki_claim_extractor import ClaimExtractionResult
from src.services.wiki_claim_matcher import ClaimMatchDecision
from src.services.wiki_repository import WikiRepository

NOW = "2026-07-09T10:00:00"


class DictConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeBlocks:
    def list_by_page(self, page_id, limit=1000):
        return [
            Block(
                id="block-1",
                page_id=page_id,
                content="FTTR 使用光纤延伸至房间级节点。",
                block_type="text",
                order_idx=0,
            )
        ]


class FakeProjection:
    def __init__(self, parity_findings=None):
        self.processed = 0
        self.parity_checks = 0
        self.rebuilds = 0
        self._parity_findings = list(parity_findings or [])

    def process_outbox(self):
        self.processed += 1
        return type("ProjectionResult", (), {
            "processed": 1,
            "skipped": 0,
            "warnings": [],
            "errors": [],
        })()

    def verify_parity(self):
        self.parity_checks += 1
        if self._parity_findings:
            findings = self._parity_findings
            self._parity_findings = []
            return findings
        return []

    def rebuild(self):
        self.rebuilds += 1
        return type("ProjectionResult", (), {
            "processed": 1,
            "skipped": 0,
            "warnings": [],
            "errors": [],
        })()


class FakeExtractor:
    def __init__(self, claims):
        self.claims = claims
        self.calls = 0

    def extract(self, **kwargs):
        self.calls += 1
        return ClaimExtractionResult(extracted_claims=self.claims, llm_calls=1)


class FakeMatcher:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def match(self, new_claim, candidates):
        return self.decisions.pop(0)


def _repo(tmp_path):
    wiki_dir = tmp_path / "wiki"
    return WikiRepository(
        wiki_dir=wiki_dir,
        registry_path=wiki_dir / "_meta" / "pages.json",
        redirects_path=wiki_dir / "_meta" / "redirects.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )


def _claim(
    claim_id,
    statement="FTTR 使用光纤延伸至房间级节点。",
    *,
    knowledge_id="kid-1",
    block_id="block-1",
    status=ClaimStatus.DRAFT,
):
    return Claim(
        schema_version=1,
        claim_id=claim_id,
        statement=statement,
        normalized_statement=normalize_statement(statement),
        claim_type="fact",
        status=status,
        confidence=0.91,
        valid_from=None,
        valid_to=None,
        subject_refs=["entity:FTTR"],
        predicate="uses",
        object_refs=["concept:fiber-room-node"],
        evidence=[
            Evidence(
                evidence_id=f"ev_{claim_id}",
                stance=EvidenceStance.SUPPORTS,
                knowledge_id=knowledge_id,
                block_id=block_id,
                location={"block_index": 0},
                source_revision="hash-1",
                excerpt_hash=f"hash-{claim_id}",
                observed_at=NOW,
            )
        ],
        relations=[],
        created_at=NOW,
        updated_at=NOW,
        revision=1,
    )


def _decision(action, target_claim_id=None, score=0.9):
    return ClaimMatchDecision(
        action=action,
        target_claim_id=target_claim_id,
        score=score,
        reasons=["test"],
    )


def _item(kid="kid-1", source_path="raw/canary/fttr.md"):
    return {
        "id": kid,
        "title": "FTTR",
        "content": "FTTR 使用光纤延伸至房间级节点。",
        "content_hash": "hash-1",
        "source_path": source_path,
    }


def test_canary_skips_objects_outside_allowlist_without_extracting(tmp_path):
    extractor = FakeExtractor([_claim("claim_canary_1")])
    workflow = WikiCanaryWorkflow(
        block_repository=FakeBlocks(),
        extractor=extractor,
        matcher=FakeMatcher([_decision("new")]),
        repository=_repo(tmp_path),
        projection=FakeProjection(),
        config=DictConfig({
            "wiki.canonical_v2.canary.knowledge_ids": ["allowed-kid"],
            "wiki.canonical_v2.canary.source_paths": ["raw/canary/"],
        }),
        perf_counter=lambda: 10.0,
    )

    report = workflow.run(
        knowledge_id="kid-1",
        item=_item(source_path="raw/other/fttr.md"),
        source_summary="FTTR summary",
        now=NOW,
    )

    assert report["status"] == "skipped"
    assert report["reason"] == "not_allowlisted"
    assert extractor.calls == 0


def test_canary_writes_to_formal_repository_and_reports_tx_id_and_parity(tmp_path):
    repo = _repo(tmp_path)
    projection = FakeProjection()
    workflow = WikiCanaryWorkflow(
        block_repository=FakeBlocks(),
        extractor=FakeExtractor([_claim("claim_canary_1")]),
        matcher=FakeMatcher([_decision("new")]),
        repository=repo,
        projection=projection,
        config=DictConfig({
            "wiki.canonical_v2.canary.knowledge_ids": ["kid-1"],
        }),
        perf_counter=lambda: 10.0,
    )

    report = workflow.run(
        knowledge_id="kid-1",
        item=_item(),
        source_summary="FTTR summary",
        now=NOW,
    )

    assert report["status"] == "completed"
    assert report["tx_id"].startswith("tx_")
    assert report["new_claims"] == 1
    assert report["auto_publish"] is False
    assert report["projection"]["parity_findings"] == 0
    assert projection.processed == 1
    assert projection.parity_checks == 1
    assert (Path(repo._wiki_dir) / "claims" / "claim_canary_1.yaml").is_file()


def test_canary_rebuilds_projection_when_parity_drift_is_detected(tmp_path):
    drift = type("Finding", (), {"message": "claim missing from projection"})()
    projection = FakeProjection(parity_findings=[drift])
    workflow = WikiCanaryWorkflow(
        block_repository=FakeBlocks(),
        extractor=FakeExtractor([_claim("claim_canary_1")]),
        matcher=FakeMatcher([_decision("new")]),
        repository=_repo(tmp_path),
        projection=projection,
        config=DictConfig({
            "wiki.canonical_v2.canary.knowledge_ids": ["kid-1"],
        }),
        perf_counter=lambda: 10.0,
    )

    report = workflow.run(
        knowledge_id="kid-1",
        item=_item(),
        source_summary="FTTR summary",
        now=NOW,
    )

    assert report["projection"]["rebuilt"] is True
    assert report["projection"]["parity_findings"] == 0
    assert projection.rebuilds == 1
    assert projection.parity_checks == 2


def test_canary_forces_high_risk_and_low_confidence_refines_to_review(tmp_path):
    repo = _repo(tmp_path)
    target = _claim("claim_existing", status=ClaimStatus.ACTIVE)
    repo.save_claim(target)

    claims = [
        _claim("claim_contradicts"),
        _claim("claim_supersedes"),
        _claim("claim_refines_low"),
    ]
    workflow = WikiCanaryWorkflow(
        block_repository=FakeBlocks(),
        extractor=FakeExtractor(claims),
        matcher=FakeMatcher([
            _decision("contradicts", target_claim_id="claim_existing", score=0.97),
            _decision("supersedes", target_claim_id="claim_existing", score=0.96),
            _decision("refines", target_claim_id="claim_existing", score=0.81),
        ]),
        repository=repo,
        projection=FakeProjection(),
        config=DictConfig({
            "wiki.canonical_v2.canary.knowledge_ids": ["kid-1"],
            "wiki.canonical_v2.canary.refines_auto_merge_min_score": 0.9,
        }),
        perf_counter=lambda: 10.0,
    )

    report = workflow.run(
        knowledge_id="kid-1",
        item=_item(),
        source_summary="FTTR summary",
        now=NOW,
    )

    assert report["status"] == "completed"
    assert report["new_claims"] == 0
    assert report["unresolved"] == 3
    assert report["forced_review"] == 3
    assert {item["original_action"] for item in report["review_items"]} == {
        "contradicts",
        "supersedes",
        "refines",
    }
    assert repo.get_claim("claim_contradicts") is None
    assert repo.get_claim("claim_supersedes") is None
    assert repo.get_claim("claim_refines_low") is None
    assert repo.get_claim("claim_existing").status == ClaimStatus.ACTIVE
