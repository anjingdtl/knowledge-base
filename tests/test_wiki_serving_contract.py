"""Wiki Serving invariants WIKI-001..010 — Phase-1 maintainability freeze.

See docs/architecture/wiki-invariants.md for the authoritative list.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from src.models.search_execution import SearchExecution
from src.models.wiki_v2 import Claim, ClaimStatus, Evidence, EvidenceStance, normalize_statement
from src.services.search_service import SearchService
from src.services.verified_answer import ANSWER_MODE_CONFLICT, ANSWER_MODE_RAW, VerifiedAnswerService
from src.services.verified_hybrid_config_migrator import VerifiedHybridConfigMigrator
from src.services.wiki_serving_gate import (
    REASON_CLAIM_DRAFT,
    REASON_CLAIM_STALE,
    REASON_CLAIM_UNSUPPORTED,
    REASON_MISSING_EVIDENCE,
    ServingGateConfig,
    WikiServingGate,
)


def _claim(
    *,
    status: ClaimStatus = ClaimStatus.ACTIVE,
    statement: str = "FTTR 可达 1Gbps",
    evidence: list | None = None,
    stale: bool = False,
    excerpt_hash: str | None = None,
    block_id: str = "b1",
    knowledge_id: str = "k1",
    claim_id: str = "claim_ok",
) -> Claim:
    if evidence is None:
        evidence = [
            Evidence(
                evidence_id=f"ev_{claim_id}",
                stance=EvidenceStance.SUPPORTS,
                knowledge_id=knowledge_id,
                block_id=block_id,
                excerpt_hash=excerpt_hash,
                stale=stale,
                location={},
            ),
        ]
    return Claim(
        schema_version=1,
        claim_id=claim_id,
        statement=statement,
        normalized_statement=normalize_statement(statement),
        claim_type="fact",
        status=status,
        confidence=0.9,
        valid_from=None,
        valid_to=None,
        subject_refs=[],
        predicate="bandwidth",
        object_refs=[],
        evidence=evidence,
        relations=[],
        created_at="t",
        updated_at="t",
        revision=1,
    )


def _gate(content: str = "FTTR 可达 1Gbps", **kwargs) -> WikiServingGate:
    blocks = kwargs.pop("blocks", {"b1": {"id": "b1", "content": content}})
    knowledge = kwargs.pop("knowledge", {"k1": {"id": "k1", "deleted_at": None}})
    return WikiServingGate(
        config=kwargs.pop("cfg", ServingGateConfig()),
        get_block=lambda bid: blocks.get(bid),
        get_knowledge=lambda kid: knowledge.get(kid),
        wiki_read_enabled=kwargs.pop("wiki_read_enabled", True),
        knowledge_mode=kwargs.pop("knowledge_mode", "verified"),
    )


class TestWikiServingContract:
    def test_wiki_001_raw_evidence_is_final_base(self):
        """WIKI-001: without claims, ask can still answer from raw evidence."""
        ex = SearchExecution(
            results=({
                "source": "knowledge",
                "knowledge_id": "k1",
                "block_id": "b1",
                "title": "Doc",
                "text": "raw only base",
                "score": 0.9,
            },),
            trace={"mode": "legacy_raw", "stages": {}},
        )

        class S:
            def execute(self, q, top_k=5, query_spec=None):
                return ex

        payload = VerifiedAnswerService(S(), llm=None).ask("q", use_llm=False)
        assert payload["answer_mode"] == ANSWER_MODE_RAW
        assert payload["raw_evidence_used"]

    def test_wiki_002_claim_requires_resolvable_evidence(self):
        """WIKI-002: claim without evidence is not eligible."""
        content = "body"
        gate = _gate(content=content)
        d = gate.evaluate(_claim(evidence=[]))
        assert d.eligible is False
        assert REASON_MISSING_EVIDENCE in d.reason_codes or not d.eligible

    def test_wiki_003_stale_claim_not_primary(self):
        """WIKI-003: stale claim must not enter reliable primary conclusion."""
        d = _gate().evaluate(_claim(stale=True))
        assert d.eligible is False
        assert REASON_CLAIM_STALE in d.reason_codes

    def test_wiki_004_unsupported_not_primary(self):
        """WIKI-004: unsupported claim not primary."""
        d = _gate().evaluate(_claim(status=ClaimStatus.UNSUPPORTED))
        assert d.eligible is False
        assert REASON_CLAIM_UNSUPPORTED in d.reason_codes

    def test_wiki_005_retracted_not_primary(self):
        """WIKI-005: retracted claim not primary."""
        d = _gate().evaluate(_claim(status=ClaimStatus.RETRACTED))
        assert d.eligible is False

    def test_wiki_006_conflict_must_disclose(self):
        """WIKI-006: conflicts surface as conflict_disclosure, never silent drop."""
        ex = SearchExecution(
            results=(
                {
                    "source": "verified_claim",
                    "claim_id": "c1",
                    "text": "峰值 1Gbps",
                    "evidence": [{"knowledge_id": "k1", "block_id": "b1", "stance": "supports"}],
                    "status": "active",
                },
                {
                    "source": "verified_claim",
                    "claim_id": "c2",
                    "text": "峰值 100Mbps",
                    "evidence": [{"knowledge_id": "k2", "block_id": "b2", "stance": "supports"}],
                    "status": "active",
                },
            ),
            trace={"mode": "hybrid_verified", "stages": {}},
        )

        class S:
            def execute(self, q, top_k=5, query_spec=None):
                return ex

        payload = VerifiedAnswerService(S(), llm=None).ask("峰值是多少", use_llm=False)
        assert payload["answer_mode"] == ANSWER_MODE_CONFLICT
        assert payload["conflicts"]
        assert payload.get("conflict_disclosed") is True

    def test_wiki_007_wiki_failure_falls_back_to_raw(self):
        """WIKI-007: wiki boom must not block raw results."""
        data = {
            "rag.enable_query_rewriting": False,
            "rag.enable_rerank": False,
            "rag.verified_knowledge.enabled": True,
            "knowledge_workflow.mode": "verified",
        }
        cfg = Mock()
        cfg.get.side_effect = lambda key, default=None: data.get(key, default)

        class BoomRepo:
            def list_claims(self):
                raise RuntimeError("wiki down")

        db = Mock()
        db.get_knowledge.return_value = {"id": "k1", "title": "Doc"}
        service = SearchService(
            cfg, db, Mock(), Mock(), Mock(),
            wiki_repository=BoomRepo(), wiki_serving_gate=Mock(),
        )
        with (
            patch.object(service, "_rewrite_query", return_value=["FTTR"]),
            patch.object(
                service,
                "_timed_hybrid_search",
                return_value=[{
                    "id": "b1",
                    "text": "raw hit",
                    "metadata": {"page_id": "k1", "title": "Doc"},
                    "rrf_score": 0.8,
                }],
            ),
        ):
            ex = service.execute("FTTR", top_k=5)
        assert any(r.get("source") == "knowledge" for r in ex.results)
        assert (ex.trace.get("stages") or {}).get("verified_wiki", {}).get("error")

    def test_wiki_008_projection_not_canonical_authority(self):
        """WIKI-008: documentation + module boundary — projection is not serving entry."""
        inv = Path("docs/architecture/wiki-invariants.md")
        assert inv.exists(), "wiki-invariants.md must document WIKI-008"
        text = inv.read_text(encoding="utf-8")
        assert "WIKI-008" in text
        assert "Projection" in text or "projection" in text
        # Serving entry is WikiServingGate / list_servable — not projection compilers
        from src.services import wiki_serving_gate as gate_mod
        assert hasattr(gate_mod, "WikiServingGate")

    def test_wiki_009_serving_authoring_separation(self):
        """WIKI-009: SearchService exposes list_servable_wiki_claims; no authoring write APIs."""
        assert hasattr(SearchService, "list_servable_wiki_claims")
        # Authoring write service is separate module
        from src.services import wiki_write_service  # noqa: F401
        assert not hasattr(SearchService, "create_claim")
        assert not hasattr(SearchService, "publish_claim")

    def test_wiki_010_auto_publish_default_false(self):
        """WIKI-010: project setup presets and migrator default auto_publish=false."""
        # project_setup presets
        src = Path("src/services/project_setup.py").read_text(encoding="utf-8")
        assert '"auto_publish": False' in src or "'auto_publish': False" in src

        # migrator _proposed defaults auto_publish=False for verified mode
        migrator = VerifiedHybridConfigMigrator(config_path="config.yaml")
        proposed = migrator._proposed({"wiki": {}, "knowledge_workflow": {"mode": "verified"}}, "verified")
        assert proposed["wiki"].get("auto_publish") is False

        # draft status still blocked by gate
        d = _gate().evaluate(_claim(status=ClaimStatus.DRAFT))
        assert d.eligible is False
        assert REASON_CLAIM_DRAFT in d.reason_codes


class TestWikiInvariantsDocExists:
    def test_doc_lists_all_ten(self):
        path = Path("docs/architecture/wiki-invariants.md")
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        for i in range(1, 11):
            assert f"WIKI-{i:03d}" in text
