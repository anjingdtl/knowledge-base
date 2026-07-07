"""Canonical Wiki v2 校验器:输出结构化 ValidationFinding。

核心校验 = 模型层 from_dict(strict=True)(捕获 schema 错误)
         + 跨对象 invariant(published 引用 draft Claim 等)。
schema/*.json 是权威契约文档,不强制 jsonschema 依赖。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from src.models.wiki_v2 import (
    Claim, ClaimStatus, PageStatus, WikiPage,
    ValidationFinding,
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
