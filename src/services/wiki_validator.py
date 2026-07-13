"""Canonical Wiki v2 校验器:输出结构化 ValidationFinding。

核心校验 = 模型层 from_dict(strict=True)(捕获 schema 错误)
         + 跨对象 invariant(published 引用 draft Claim 等)。
schema/*.json 是权威契约文档,不强制 jsonschema 依赖。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    EvidenceStance,
    PageStatus,
    ValidationFinding,
    WikiPage,
)
from src.services.wiki_slug import read_frontmatter

ClaimLookup = Callable[[str], Optional[Claim]]


class WikiValidator:
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
