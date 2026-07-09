from src.services.wiki_primary_workflow import WikiPrimaryWorkflow
from tests.test_wiki_canary_workflow import (
    DictConfig,
    FakeBlocks,
    FakeExtractor,
    FakeMatcher,
    FakeProjection,
    _claim,
    _decision,
    _item,
    _repo,
)

NOW = "2026-07-09T10:00:00"


def test_primary_workflow_runs_without_canary_allowlist(tmp_path):
    repo = _repo(tmp_path)
    projection = FakeProjection()
    workflow = WikiPrimaryWorkflow(
        block_repository=FakeBlocks(),
        extractor=FakeExtractor([_claim("claim_primary_1")]),
        matcher=FakeMatcher([_decision("new")]),
        repository=repo,
        projection=projection,
        config=DictConfig({
            "wiki.canonical_v2.canary.knowledge_ids": [],
            "knowledge_workflow.wiki_dir": str(tmp_path / "wiki"),
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
    assert repo.get_claim("claim_primary_1") is not None
    assert projection.parity_checks == 1
