"""Phase 2: WikiServingGate + Repository serving API tests."""
from __future__ import annotations

from src.models.wiki_v2 import (
    Claim,
    ClaimServingValidation,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    normalize_statement,
)
from src.services.wiki_claim_extractor import compute_excerpt_hash
from src.services.wiki_repository import WikiRepository
from src.services.wiki_serving_gate import (
    REASON_CLAIM_DRAFT,
    REASON_CLAIM_STALE,
    REASON_CLAIM_UNSUPPORTED,
    REASON_EVIDENCE_BLOCK_MISSING,
    REASON_EVIDENCE_HASH_MISMATCH,
    REASON_KNOWLEDGE_DELETED,
    REASON_MISSING_EVIDENCE,
    REASON_PUBLISHED_REVISION_STALE,
    REASON_REVIEW_NOT_APPROVED,
    REASON_REVIEW_REQUIRED,
    REASON_SCOPE_MISMATCH,
    REASON_SERVING_EVIDENCE_MISSING,
    REASON_SERVING_VALIDATION_MISSING,
    REASON_VALIDATED_REVISION_STALE,
    REASON_VALIDATION_FAILED,
    ServingGateConfig,
    WikiServingGate,
)


def _claim(
    claim_id: str = "claim_ok",
    *,
    status: ClaimStatus = ClaimStatus.ACTIVE,
    statement: str = "FTTR 可达 1Gbps",
    evidence: list[Evidence] | None = None,
    block_id: str = "b1",
    knowledge_id: str = "k1",
    stale: bool = False,
    excerpt_hash: str | None = None,
    location: dict | None = None,
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
                location=location or {},
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


def _gate(
    *,
    blocks: dict[str, dict] | None = None,
    knowledge: dict[str, dict] | None = None,
    content: str = "FTTR 可达 1Gbps",
    cfg: ServingGateConfig | None = None,
    wiki_read_enabled: bool = True,
    knowledge_mode: str = "verified",
) -> WikiServingGate:
    blocks = blocks if blocks is not None else {"b1": {"id": "b1", "content": content}}
    knowledge = knowledge if knowledge is not None else {"k1": {"id": "k1", "deleted_at": None}}

    def get_block(bid: str):
        return blocks.get(bid)

    def get_knowledge(kid: str):
        return knowledge.get(kid)

    return WikiServingGate(
        config=cfg or ServingGateConfig(),
        get_block=get_block,
        get_knowledge=get_knowledge,
        wiki_read_enabled=wiki_read_enabled,
        knowledge_mode=knowledge_mode,
    )


class TestPrimaryEligibility:
    @staticmethod
    def _strict_config() -> ServingGateConfig:
        return ServingGateConfig(
            require_validation_passed=True,
            require_review_approved=True,
            require_published_revision=True,
        )

    @staticmethod
    def _validated_claim(*, content: str = "FTTR 可达 1Gbps") -> Claim:
        claim = _claim(excerpt_hash=compute_excerpt_hash(content))
        claim.serving_validation = ClaimServingValidation(
            passed=True,
            review_approved=True,
            validated_revision=1,
            published_revision=1,
            serving_evidence_ids=[claim.evidence[0].evidence_id],
            validator_version="test-v1",
            validated_at="2026-07-13T12:00:00+08:00",
        )
        return claim

    def test_strict_gate_requires_persisted_validation_record(self):
        decision = _gate(cfg=self._strict_config()).evaluate(_claim())

        assert decision.eligible is False
        assert REASON_SERVING_VALIDATION_MISSING in decision.reason_codes

    def test_strict_gate_accepts_matching_validated_reviewed_published_claim(self):
        content = "strict body"
        decision = _gate(content=content, cfg=self._strict_config()).evaluate(
            self._validated_claim(content=content),
        )

        assert decision.eligible is True

    def test_strict_gate_rejects_stale_or_unapproved_records(self):
        claim = self._validated_claim()
        claim.serving_validation.review_approved = False
        claim.serving_validation.validated_revision = 0
        claim.serving_validation.published_revision = None

        decision = _gate(cfg=self._strict_config()).evaluate(claim)

        assert decision.eligible is False
        assert REASON_REVIEW_NOT_APPROVED in decision.reason_codes
        assert REASON_VALIDATED_REVISION_STALE in decision.reason_codes
        assert REASON_PUBLISHED_REVISION_STALE in decision.reason_codes

    def test_strict_gate_rejects_empty_serving_evidence_set(self):
        claim = self._validated_claim()
        claim.serving_validation.serving_evidence_ids = []

        decision = _gate(cfg=self._strict_config()).evaluate(claim)

        assert decision.eligible is False
        assert REASON_SERVING_EVIDENCE_MISSING in decision.reason_codes
    def test_active_with_resolvable_evidence_eligible(self):
        content = "body text"
        h = compute_excerpt_hash(content)
        gate = _gate(content=content)
        claim = _claim(excerpt_hash=h)
        d = gate.evaluate(claim)
        assert d.eligible is True
        assert d.reason_codes == []
        assert d.resolved_evidence and d.resolved_evidence[0].ok

    def test_draft_not_eligible(self):
        gate = _gate()
        d = gate.evaluate(_claim(status=ClaimStatus.DRAFT))
        assert d.eligible is False
        assert REASON_CLAIM_DRAFT in d.reason_codes

    def test_unsupported_not_eligible(self):
        gate = _gate()
        d = gate.evaluate(_claim(status=ClaimStatus.UNSUPPORTED))
        assert d.eligible is False
        assert REASON_CLAIM_UNSUPPORTED in d.reason_codes

    def test_retracted_not_eligible(self):
        gate = _gate()
        # get_claim hides retracted; evaluate still rejects
        d = gate.evaluate(_claim(status=ClaimStatus.RETRACTED))
        assert d.eligible is False

    def test_stale_supports_not_eligible(self):
        gate = _gate()
        d = gate.evaluate(_claim(stale=True))
        assert d.eligible is False
        assert REASON_CLAIM_STALE in d.reason_codes

    def test_missing_supports_not_eligible(self):
        gate = _gate()
        claim = _claim(evidence=[])
        d = gate.evaluate(claim)
        assert d.eligible is False
        assert REASON_MISSING_EVIDENCE in d.reason_codes or REASON_VALIDATION_FAILED in d.reason_codes

    def test_block_missing_not_eligible(self):
        gate = _gate(blocks={})
        d = gate.evaluate(_claim())
        assert d.eligible is False
        assert REASON_EVIDENCE_BLOCK_MISSING in d.reason_codes

    def test_hash_mismatch_not_eligible(self):
        gate = _gate(content="new content")
        d = gate.evaluate(_claim(excerpt_hash=compute_excerpt_hash("old content")))
        assert d.eligible is False
        assert REASON_EVIDENCE_HASH_MISMATCH in d.reason_codes

    def test_knowledge_soft_deleted_not_eligible(self):
        gate = _gate(knowledge={"k1": {"id": "k1", "deleted_at": "2026-01-01"}})
        d = gate.evaluate(_claim())
        assert d.eligible is False
        assert REASON_KNOWLEDGE_DELETED in d.reason_codes

    def test_disputed_disclose_only(self):
        gate = _gate()
        d = gate.evaluate(_claim(status=ClaimStatus.DISPUTED))
        assert d.eligible is False
        assert d.disclose_only is True
        assert REASON_REVIEW_REQUIRED in d.reason_codes

    def test_scope_flag_demotes(self):
        content = "x"
        h = compute_excerpt_hash(content)
        gate = _gate(content=content)
        claim = _claim(
            excerpt_hash=h,
            location={"serving_flags": [REASON_SCOPE_MISMATCH]},
        )
        d = gate.evaluate(claim)
        assert d.eligible is False
        assert REASON_SCOPE_MISMATCH in d.reason_codes
        assert d.disclose_only is True

    def test_wiki_read_disabled(self):
        gate = _gate(wiki_read_enabled=False)
        d = gate.evaluate(_claim())
        assert d.eligible is False

    def test_evidence_only_mode_blocks(self):
        gate = _gate(knowledge_mode="evidence_only", wiki_read_enabled=None)
        # wiki_read_enabled None → derive from mode
        d = gate.evaluate(_claim())
        assert d.eligible is False

    def test_no_llm_flag_in_diagnostics(self):
        gate = _gate()
        diag = gate.diagnostics_for_claims([_claim(status=ClaimStatus.UNSUPPORTED)])
        assert diag["gate_uses_llm"] is False
        assert diag["eligible_primary"] == 0


class TestFilterServable:
    def test_filter_excludes_stale_and_unsupported(self):
        content = "ok"
        h = compute_excerpt_hash(content)
        gate = _gate(content=content)
        claims = [
            _claim("c_ok", excerpt_hash=h),
            _claim("c_stale", stale=True, excerpt_hash=h),
            _claim("c_unsup", status=ClaimStatus.UNSUPPORTED, excerpt_hash=h),
            _claim("c_draft", status=ClaimStatus.DRAFT, excerpt_hash=h),
        ]
        pairs = gate.filter_servable(claims)
        ids = {c.claim_id for c, _ in pairs}
        assert ids == {"c_ok"}
        # Acceptance: stale/unsupported serving rate 0
        diag = gate.diagnostics_for_claims(claims)
        assert diag["stale_serving_count"] == 0 or all(
            not d.eligible for d in [gate.evaluate(c) for c in claims if c.claim_id == "c_stale"]
        )
        assert all(not gate.evaluate(c).eligible for c in claims if c.status is ClaimStatus.UNSUPPORTED)


class TestRepositoryServingAPI:
    def test_list_servable_claims_read_only(self, tmp_path):
        repo = WikiRepository(
            wiki_dir=tmp_path / "wiki",
            registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
            redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
            outbox_path=tmp_path / "outbox.jsonl",
        )
        content = "servable body"
        h = compute_excerpt_hash(content)
        ok = _claim("claim_ok", excerpt_hash=h)
        bad = _claim("claim_bad", status=ClaimStatus.UNSUPPORTED, excerpt_hash=h)
        repo.save_claim(ok)
        repo.save_claim(bad)

        gate = _gate(content=content)
        servable = repo.list_servable_claims(gate=gate)
        assert [c.claim_id for c in servable] == ["claim_ok"]
        assert repo.get_servable_claim("claim_bad", gate=gate) is None
        assert repo.get_servable_claim("claim_ok", gate=gate) is not None

        # no staging read: staging dir empty / unused
        assert not (tmp_path / "wiki" / "_staging").exists() or \
            not any((tmp_path / "wiki" / "_staging").iterdir())

        diag = repo.get_claim_serving_diagnostics(gate=gate)
        assert diag["eligible_primary"] == 1
        assert diag["total_claims"] == 2
        assert diag["gate_uses_llm"] is False

    def test_resolve_claim_evidence(self, tmp_path):
        repo = WikiRepository(
            wiki_dir=tmp_path / "wiki",
            registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
            redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
            outbox_path=tmp_path / "outbox.jsonl",
        )
        content = "ev body"
        h = compute_excerpt_hash(content)
        claim = _claim("claim_ev", excerpt_hash=h)
        repo.save_claim(claim)
        gate = _gate(content=content)
        resolved = repo.resolve_claim_evidence("claim_ev", gate=gate)
        assert len(resolved) == 1
        assert resolved[0].ok is True

    def test_get_claim_still_returns_non_servable_for_authoring(self, tmp_path):
        """Authoring paths use get_claim; serving uses get_servable_claim."""
        repo = WikiRepository(
            wiki_dir=tmp_path / "wiki",
            registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
            redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
            outbox_path=tmp_path / "outbox.jsonl",
        )
        draft = _claim("claim_draft", status=ClaimStatus.DRAFT)
        repo.save_claim(draft)
        assert repo.get_claim("claim_draft") is not None
        gate = _gate()
        assert repo.get_servable_claim("claim_draft", gate=gate) is None


class TestDoctorServing:
    def test_check_serving_claims_smoke(self, tmp_path, monkeypatch):
        from src.services.doctor import DoctorService
        from src.utils.config import Config

        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "claims").mkdir()
        monkeypatch.setattr(Config, "get", lambda key, default=None: {
            "knowledge_workflow.wiki_dir": str(wiki),
            "storage.data_dir": str(tmp_path / "data"),
        }.get(key, default))

        results = DoctorService().check_serving_claims()
        names = {r["name"] for r in results}
        assert "serving_claims" in names
        assert "serving_stale" in names
        assert "serving_unsupported" in names


class TestSearchServiceEntry:
    def test_search_service_only_returns_servable_claims(self):
        from src.services.search_service import SearchService

        content = "ok body"
        h = compute_excerpt_hash(content)
        claims = {
            "ok": _claim("ok", excerpt_hash=h),
            "bad": _claim("bad", status=ClaimStatus.UNSUPPORTED, excerpt_hash=h),
        }

        class FakeRepo:
            def list_claims(self):
                return list(claims.values())

            def list_servable_claims(self, *, gate=None, include_disclose=False, limit=None):
                g = gate or _gate(content=content)
                return [c for c, _ in g.filter_servable(
                    self.list_claims(), include_disclose=include_disclose, limit=limit,
                )]

        svc = SearchService(
            wiki_repository=FakeRepo(),
            wiki_serving_gate=_gate(content=content),
        )
        got = svc.list_servable_wiki_claims()
        assert [c.claim_id for c in got] == ["ok"]
