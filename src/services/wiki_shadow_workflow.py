"""Phase 4A shadow canonical workflow.

Runs extractor -> matcher -> merge against an isolated ``wiki/_shadow`` canonical
store after raw ingest succeeds. Shadow output never writes to formal
``wiki/claims`` or the formal projection outbox.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from src.models.block import Block
from src.models.wiki_v2 import Claim
from src.services.wiki_claim_extractor import ExtractionBlock
from src.services.wiki_claim_matcher import ClaimMatchDecision
from src.services.wiki_merge_engine import MergeResult, WikiMergeEngine
from src.services.wiki_repository import WikiRepository


class WikiShadowWorkflow:
    """Run canonical v2 in a shadow-only area and emit comparison statistics."""

    def __init__(
        self,
        block_repository: Any,
        extractor: Any,
        matcher: Any,
        config: Any = None,
        repository: Any | None = None,
        merge_engine: Any | None = None,
        clock: Callable[[], str] | None = None,
        perf_counter: Callable[[], float] | None = None,
    ) -> None:
        self._blocks = block_repository
        self._extractor = extractor
        self._matcher = matcher
        self._config = config
        self._repository = repository
        self._merge_engine = merge_engine
        self._clock = clock or (lambda: "")
        self._perf_counter = perf_counter or time.perf_counter

    def _cfg(self, key: str, default: Any = None) -> Any:
        if self._config is not None:
            return self._config.get(key, default)
        return default

    def run(
        self,
        *,
        knowledge_id: str,
        item: dict,
        source_summary: str,
        now: str | None = None,
    ) -> dict:
        """Run the shadow chain and write a JSON report under ``wiki/_shadow``."""
        started = self._perf_counter()
        ts = now or self._clock()
        repo = self._get_repository()
        blocks = self._extraction_blocks(knowledge_id, item)
        candidates = repo.list_claims()

        extraction = self._extractor.extract(
            knowledge_id=knowledge_id,
            blocks=blocks,
            source_summary=source_summary,
            candidate_claims=candidates,
            now=ts,
            max_llm_calls=self._cfg("wiki.claims.max_llm_calls_per_ingest", 4),
        )
        extracted_claims: list[Claim] = list(extraction.extracted_claims)
        decisions = [
            (claim, self._matcher.match(claim, candidates))
            for claim in extracted_claims
        ]

        merge_result = self._apply(decisions, repo, ts)
        report = self._build_report(
            knowledge_id=knowledge_id,
            extracted_claims=extracted_claims,
            decisions=[d for _, d in decisions],
            merge_result=merge_result,
            llm_calls=int(extraction.llm_calls),
            warnings=list(extraction.warnings),
            errors=list(extraction.errors),
            latency_ms=int((self._perf_counter() - started) * 1000),
        )
        report_path = self._write_report(knowledge_id, report)
        report["report_path"] = str(report_path)
        return report

    def _get_repository(self) -> Any:
        if self._repository is not None:
            return self._repository

        shadow_dir = self._shadow_dir()
        self._repository = WikiRepository(
            wiki_dir=shadow_dir,
            registry_path=shadow_dir / "_meta" / "pages.json",
            redirects_path=shadow_dir / "_meta" / "redirects.json",
            outbox_path=shadow_dir / "_meta" / "projection_outbox.jsonl",
        )
        return self._repository

    def _get_merge_engine(self, repo: Any) -> Any:
        if self._merge_engine is None:
            self._merge_engine = WikiMergeEngine(repository=repo, config=self._config)
        return self._merge_engine

    def _extraction_blocks(self, knowledge_id: str, item: dict) -> list[ExtractionBlock]:
        source_revision = str(
            item.get("content_hash") or item.get("updated_at") or item.get("version") or ""
        )
        blocks: list[ExtractionBlock] = []

        try:
            raw_blocks = self._blocks.list_by_page(knowledge_id, limit=1000)
        except Exception:  # noqa: BLE001 - shadow must never break ingest
            raw_blocks = []

        for idx, raw in enumerate(raw_blocks):
            block = raw if isinstance(raw, Block) else Block.from_row(dict(raw))
            if not block.content:
                continue
            blocks.append(ExtractionBlock(
                block_id=block.id,
                content=block.content,
                location={
                    "block_index": block.order_idx if block.order_idx is not None else idx,
                    "block_type": block.block_type,
                    "source_path": item.get("source_path", ""),
                },
                source_revision=source_revision,
                excerpt_hash=self._hash_text(block.content),
            ))

        if blocks:
            return blocks

        content = str(item.get("content") or "")
        if not content:
            return []
        return [ExtractionBlock(
            block_id=f"{knowledge_id}:content",
            content=content,
            location={"source_path": item.get("source_path", ""), "fallback": "knowledge_content"},
            source_revision=source_revision,
            excerpt_hash=self._hash_text(content),
        )]

    def _apply(
        self,
        decisions: list[tuple[Claim, ClaimMatchDecision]],
        repo: Any,
        now: str,
    ) -> MergeResult:
        if not decisions:
            return MergeResult(diff="(no changes)", committed=False)
        return cast(MergeResult, self._get_merge_engine(repo).apply(decisions, page=None, now=now))

    def _build_report(
        self,
        *,
        knowledge_id: str,
        extracted_claims: list[Claim],
        decisions: list[ClaimMatchDecision],
        merge_result: MergeResult,
        llm_calls: int,
        warnings: list[str],
        errors: list[str],
        latency_ms: int,
    ) -> dict:
        actions = [d.action for d in decisions]
        auto_merge_actions = {"supports", "duplicate", "refines", "supersedes"}
        evidence_missing = sum(
            1 for claim in extracted_claims if self._claim_has_missing_evidence(claim)
        )
        shadow_dir = self._shadow_dir()
        return {
            "status": "completed",
            "knowledge_id": knowledge_id,
            "claims_extracted": len(extracted_claims),
            "new_claims": len(merge_result.claims_created),
            "auto_merged": sum(1 for action in actions if action in auto_merge_actions),
            "unresolved": sum(1 for action in actions if action == "unresolved"),
            "conflicts": sum(1 for action in actions if action == "contradicts"),
            "evidence_missing": evidence_missing,
            "page_diff": merge_result.diff or "(no changes)",
            "llm_calls": llm_calls,
            "latency_ms": latency_ms,
            "committed": merge_result.committed,
            "output_dir": str(shadow_dir),
            "warnings": warnings,
            "errors": errors + list(merge_result.errors),
        }

    def _claim_has_missing_evidence(self, claim: Claim) -> bool:
        require_block = bool(self._cfg("wiki.claims.require_block_evidence", True))
        if not claim.evidence:
            return True
        for ev in claim.evidence:
            if not ev.knowledge_id:
                return True
            if require_block and not ev.block_id:
                return True
        return False

    def _write_report(self, knowledge_id: str, report: dict) -> Path:
        shadow_dir = self._shadow_dir()
        reports_dir = shadow_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        safe_id = knowledge_id.replace("/", "_").replace("\\", "_")
        path = reports_dir / f"{safe_id}.json"
        tmp_fd, tmp = tempfile.mkstemp(dir=str(reports_dir), suffix=".json.tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path

    @staticmethod
    def _hash_text(text: str) -> str:
        from src.services.wiki_claim_extractor import compute_excerpt_hash
        return compute_excerpt_hash(text)

    def _shadow_dir(self) -> Path:
        wiki_dir = Path(self._cfg("knowledge_workflow.wiki_dir", "wiki"))
        return Path(self._cfg("wiki.canonical_v2.shadow_dir", str(wiki_dir / "_shadow")))
