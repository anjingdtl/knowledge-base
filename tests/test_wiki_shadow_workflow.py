from pathlib import Path

from src.models.block import Block
from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    normalize_statement,
)
from src.services.wiki_claim_extractor import ClaimExtractionResult
from src.services.wiki_claim_matcher import ClaimMatchDecision
from src.services.wiki_shadow_workflow import WikiShadowWorkflow


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


class FakeExtractor:
    def extract(self, **kwargs):
        claim = Claim(
            schema_version=1,
            claim_id="claim_shadow_1",
            statement="FTTR 使用光纤延伸至房间级节点。",
            normalized_statement=normalize_statement("FTTR 使用光纤延伸至房间级节点。"),
            claim_type="fact",
            status=ClaimStatus.DRAFT,
            confidence=0.91,
            valid_from=None,
            valid_to=None,
            subject_refs=["entity:FTTR"],
            predicate="uses",
            object_refs=["concept:fiber-room-node"],
            evidence=[
                Evidence(
                    evidence_id="ev_shadow_1",
                    stance=EvidenceStance.SUPPORTS,
                    knowledge_id=kwargs["knowledge_id"],
                    block_id="block-1",
                    location={"block_index": 0},
                    source_revision="hash-1",
                    excerpt_hash="excerpt-1",
                    observed_at=kwargs["now"],
                )
            ],
            relations=[],
            created_at=kwargs["now"],
            updated_at=kwargs["now"],
            revision=1,
        )
        return ClaimExtractionResult(extracted_claims=[claim], llm_calls=1)


class FakeMatcher:
    def match(self, new_claim, candidates):
        return ClaimMatchDecision(
            action="new",
            target_claim_id=None,
            score=0.0,
            reasons=["no candidates provided"],
            reason_codes=["no_candidates"],
        )


def test_shadow_workflow_writes_only_under_shadow_dir(tmp_path):
    wiki_dir = tmp_path / "wiki"
    workflow = WikiShadowWorkflow(
        block_repository=FakeBlocks(),
        extractor=FakeExtractor(),
        matcher=FakeMatcher(),
        config=DictConfig({
            "knowledge_workflow.wiki_dir": str(wiki_dir),
            "wiki.claims.require_block_evidence": True,
        }),
        clock=lambda: "2026-07-09T10:00:00",
        perf_counter=lambda: 10.0,
    )

    report = workflow.run(
        knowledge_id="kid-1",
        item={
            "id": "kid-1",
            "title": "FTTR",
            "content": "FTTR 使用光纤延伸至房间级节点。",
            "content_hash": "hash-1",
            "source_path": "raw/fttr.md",
        },
        source_summary="FTTR summary",
        now="2026-07-09T10:00:00",
    )

    assert report["status"] == "completed"
    assert report["new_claims"] == 1
    assert report["auto_merged"] == 0
    assert report["unresolved"] == 0
    assert report["conflicts"] == 0
    assert report["evidence_missing"] == 0
    assert report["llm_calls"] == 1
    assert "[claim:claim_shadow_1] created (draft)" in report["page_diff"]
    assert Path(report["report_path"]).is_file()
    assert (wiki_dir / "_shadow" / "claims" / "claim_shadow_1.yaml").is_file()
    assert not (wiki_dir / "claims").exists()
