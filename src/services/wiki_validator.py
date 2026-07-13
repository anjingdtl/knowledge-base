"""Canonical Wiki v2 校验器:输出结构化 ValidationFinding。

核心校验 = 模型层 from_dict(strict=True)(捕获 schema 错误)
         + 跨对象 invariant(published 引用 draft Claim 等)。
schema/*.json 是权威契约文档,不强制 jsonschema 依赖。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, cast

from src.models.wiki_v2 import (
    Claim,
    ClaimServingValidation,
    ClaimStatus,
    EvidenceStance,
    PageStatus,
    ValidationFinding,
    WikiPage,
)
from src.services.wiki_slug import read_frontmatter

ClaimLookup = Callable[[str], Optional[Claim]]


class WikiValidator:
    """Validate Canonical objects and manage proof used by the serving gate.

    The gate is deliberately fail-closed. These helpers are the only place
    that turns a review-approved Claim into a validation or publication proof;
    callers still need an explicit publish call after validation succeeds.
    """

    SERVING_VALIDATOR_VERSION = "wiki-validator/v1"

    def __init__(self, wiki_dir: Path | str | None = None):
        self._wiki_dir = Path(wiki_dir) if wiki_dir else None

    # ---- 单对象校验 ----
    def validate_page_dict(self, d: dict, *, path: str = "", claim_lookup: ClaimLookup | None = None) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        try:
            page = WikiPage.from_dict(d, strict=True)
        except (ValueError, TypeError, KeyError) as e:
            findings.append(ValidationFinding(
                path=path, object_id=str(d.get("page_id", "?")),
                category="schema_invalid", severity="error",
                message=f"页面 schema 校验失败: {e}",
            ))
            return findings
        findings.extend(self.validate_page(page, path=path, claim_lookup=claim_lookup))
        return findings

    def validate_page(self, page: WikiPage, *, path: str = "", claim_lookup: ClaimLookup | None = None) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        path = path or f"{page.page_type.value}/{page.title}.md"
        # published 页面不得引用 draft Claim
        if page.status is PageStatus.PUBLISHED and page.claim_ids and claim_lookup:
            for cid in page.claim_ids:
                c = claim_lookup(cid)
                if c is not None and c.status is ClaimStatus.DRAFT:
                    findings.append(ValidationFinding(
                        path=path, object_id=page.page_id,
                        category="publish_gate_violation", severity="error",
                        message=f"published 页面引用了 draft Claim: {cid}",
                    ))
        return findings

    def validate_claim(self, claim: Claim, *, path: str = "") -> list[ValidationFinding]:
        path = path or f"claims/{claim.claim_id}.yaml"
        errors = claim.validate()
        return [ValidationFinding(
            path=path, object_id=claim.claim_id,
            category="schema_invalid" if "supports" not in e else "evidence_missing",
            severity="error", message=e,
        ) for e in errors]

    def validate_and_record_serving(
        self,
        repository,
        claim_id: str,
        *,
        validated_at: str,
        operation_id: str | None = None,
    ) -> ClaimServingValidation | None:
        """Validate one reviewed Claim and persist a non-published proof."""
        claim = repository.get_claim(claim_id)
        if claim is None:
            return None

        prior = claim.serving_validation
        supports = [
            evidence for evidence in claim.evidence
            if evidence.stance is EvidenceStance.SUPPORTS
            and not evidence.stale
            and bool(evidence.block_id)
            and bool(evidence.excerpt_hash)
        ]
        findings = self.validate_claim(claim)
        passed = not any(f.severity == "error" for f in findings) and bool(supports)
        next_revision = claim.revision + 1
        validation = ClaimServingValidation(
            passed=passed,
            review_approved=bool(prior and prior.review_approved),
            validated_revision=next_revision,
            published_revision=None,
            serving_evidence_ids=[e.evidence_id for e in supports],
            validator_version=self.SERVING_VALIDATOR_VERSION,
            validated_at=validated_at,
            review_id=prior.review_id if prior else None,
            operation_id=operation_id or (prior.operation_id if prior else None),
        )
        claim.serving_validation = validation
        with repository.transaction() as tx:
            tx.stage_claim(claim, expected_revision=claim.revision)
        return validation

    def publish_serving_revision(
        self,
        repository,
        projection,
        claim_id: str,
        *,
        published_at: str,
        operation_id: str | None = None,
    ) -> ClaimServingValidation | None:
        """Explicitly publish a current validation record after parity passes."""
        claim = repository.get_claim(claim_id)
        if claim is None or claim.serving_validation is None:
            return None
        validation = claim.serving_validation
        if not (
            validation.passed
            and validation.review_approved
            and validation.validated_revision == claim.revision
        ):
            return None
        if projection is not None:
            try:
                result = projection.process_outbox()
                if getattr(result, "errors", []):
                    return None
                if list(projection.verify_parity()):
                    return None
            except Exception:
                return None

        next_revision = claim.revision + 1
        claim.serving_validation = ClaimServingValidation(
            passed=True,
            review_approved=True,
            validated_revision=next_revision,
            published_revision=next_revision,
            serving_evidence_ids=list(validation.serving_evidence_ids),
            validator_version=validation.validator_version,
            validated_at=published_at,
            review_id=validation.review_id,
            operation_id=operation_id or validation.operation_id,
        )
        with repository.transaction() as tx:
            tx.stage_claim(claim, expected_revision=claim.revision)
        return cast(ClaimServingValidation, claim.serving_validation)

    # ---- 目录级校验 ----
    def validate_directory(self) -> list[ValidationFinding]:
        """扫 wiki_dir 下所有 page md,检查 claim 文件存在性等目录级 invariant。"""
        findings: list[ValidationFinding] = []
        if not self._wiki_dir or not self._wiki_dir.exists():
            return findings
        claims_dir = self._wiki_dir / "claims"
        for pt in ("sources", "entities", "concepts", "comparisons", "syntheses"):
            d = self._wiki_dir / pt
            if not d.exists():
                continue
            for md in d.glob("*.md"):
                fm = read_frontmatter(md)
                if not fm.get("page_id"):
                    continue
                for cid in fm.get("claim_ids", []) or []:
                    if not claims_dir.exists() or not (claims_dir / f"{cid}.yaml").exists():
                        findings.append(ValidationFinding(
                            path=str(md.relative_to(self._wiki_dir)).replace("\\", "/"),
                            object_id=fm["page_id"], category="claim_missing",
                            severity="error", message=f"Claim 文件缺失: {cid}.yaml",
                        ))
        return findings

    def validate_canonical_store(self, repository) -> list[ValidationFinding]:
        """Phase 6:扫 repository 中的 claims/pages，检查 provenance 与 published 约束。

        - active 无 supports → error (evidence_missing)
        - supports 无 block_id → warning (page_only_evidence)
        - published page 引用 disputed claim → warning (unresolved_conflict)
        - published page 引用 draft claim → error (publish_gate_violation)
        - projection parity(若 repository 附带 projection 由调用方另查)
        """
        findings: list[ValidationFinding] = []
        claims = {c.claim_id: c for c in repository.list_claims()}
        for claim in claims.values():
            findings.extend(self.validate_claim(claim))
            if claim.status is ClaimStatus.ACTIVE:
                supports = [
                    e for e in claim.evidence
                    if e.stance is EvidenceStance.SUPPORTS and not getattr(e, "stale", False)
                ]
                if not supports:
                    findings.append(ValidationFinding(
                        path=f"claims/{claim.claim_id}.yaml",
                        object_id=claim.claim_id,
                        category="missing_provenance",
                        severity="error",
                        message="active Claim 缺少非 stale supports Evidence",
                    ))
                for e in supports:
                    if not e.block_id:
                        findings.append(ValidationFinding(
                            path=f"claims/{claim.claim_id}.yaml",
                            object_id=claim.claim_id,
                            category="page_only_evidence",
                            severity="warning",
                            message=f"Evidence {e.evidence_id} 无 block_id(location_quality=page_only)",
                        ))
        for page in repository.list_pages():
            for cid in page.claim_ids:
                c = claims.get(cid)
                if c is None:
                    findings.append(ValidationFinding(
                        path=f"{page.page_type.value}/{page.title}.md",
                        object_id=page.page_id,
                        category="claim_missing",
                        severity="error",
                        message=f"页面引用的 Claim 不存在: {cid}",
                    ))
                    continue
                if page.status is PageStatus.PUBLISHED:
                    if c.status is ClaimStatus.DRAFT:
                        findings.append(ValidationFinding(
                            path=f"{page.page_type.value}/{page.title}.md",
                            object_id=page.page_id,
                            category="publish_gate_violation",
                            severity="error",
                            message=f"published 页面引用了 draft Claim: {cid}",
                        ))
                    if c.status is ClaimStatus.DISPUTED:
                        findings.append(ValidationFinding(
                            path=f"{page.page_type.value}/{page.title}.md",
                            object_id=page.page_id,
                            category="unresolved_conflict",
                            severity="warning",
                            message=f"published 页面引用了 disputed Claim: {cid}",
                        ))
        return findings
