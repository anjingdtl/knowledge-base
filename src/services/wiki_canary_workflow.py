"""Phase 4B canary canonical workflow.

Runs extractor -> matcher -> guarded merge against the formal canonical v2
repository for explicitly allowlisted objects only. Canary keeps legacy read
fallback available, disables automatic publish, and forces high-risk decisions
into review before any repository write.
"""
from __future__ import annotations

import hashlib
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


class WikiCanaryWorkflow:
    """Run canonical v2 main writes for explicitly allowlisted objects."""

    def __init__(
        self,
        block_repository: Any,
        extractor: Any,
        matcher: Any,
        repository: Any,
        projection: Any,
        config: Any = None,
        merge_engine: Any | None = None,
        clock: Callable[[], str] | None = None,
        perf_counter: Callable[[], float] | None = None,
    ) -> None:
        self._blocks = block_repository
        self._extractor = extractor
        self._matcher = matcher
        self._repo = repository
        self._projection = projection
        self._config = config
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
        """Run the canary chain for an allowlisted knowledge item."""
        if not self.is_allowlisted(knowledge_id=knowledge_id, item=item):
            return {
                "status": "skipped",
                "reason": "not_allowlisted",
                "knowledge_id": knowledge_id,
                "allowlist": self.allowlist(),
            }

        started = self._perf_counter()
        ts = now or self._clock()
        blocks = self._extraction_blocks(knowledge_id, item)
        candidates = self._repo.list_claims()

        extraction = self._extractor.extract(
            knowledge_id=knowledge_id,
            blocks=blocks,
            source_summary=source_summary,
            candidate_claims=candidates,
            now=ts,
            max_llm_calls=self._cfg("wiki.claims.max_llm_calls_per_ingest", 4),
        )
        extracted_claims: list[Claim] = list(extraction.extracted_claims)
        raw_decisions = [
            self._matcher.match(claim, candidates)
            for claim in extracted_claims
        ]
        guarded_decisions, forced_review = self._guard_decisions(extracted_claims, raw_decisions)
        merge_result = self._apply(list(zip(extracted_claims, guarded_decisions)), ts)
        self._annotate_forced_review(merge_result.review_items, forced_review)
        projection_report = self._project_and_verify()

        report = self._build_report(
            knowledge_id=knowledge_id,
            extracted_claims=extracted_claims,
            raw_decisions=raw_decisions,
            guarded_decisions=guarded_decisions,
            forced_review=forced_review,
            merge_result=merge_result,
            projection_report=projection_report,
            llm_calls=int(extraction.llm_calls),
            warnings=list(extraction.warnings),
            errors=list(extraction.errors),
            latency_ms=int((self._perf_counter() - started) * 1000),
        )
        report_path = self._write_report(knowledge_id, report)
        report["report_path"] = str(report_path)
        return report

    def is_allowlisted(self, *, knowledge_id: str, item: dict) -> bool:
        allow = self.allowlist()
        if "*" in allow["knowledge_ids"] or knowledge_id in allow["knowledge_ids"]:
            return True

        source_path = str(item.get("source_path") or "").replace("\\", "/")
        for prefix in allow["source_paths"]:
            normalized = prefix.replace("\\", "/").rstrip("/")
            if not normalized:
                continue
            if source_path == normalized or source_path.startswith(f"{normalized}/"):
                return True
        return False

    def allowlist(self) -> dict[str, list[str]]:
        return {
            "knowledge_ids": self._cfg_list("wiki.canonical_v2.canary.knowledge_ids"),
            "source_paths": (
                self._cfg_list("wiki.canonical_v2.canary.source_paths")
                or self._cfg_list("wiki.canonical_v2.canary.paths")
            ),
        }

    def _cfg_list(self, key: str) -> list[str]:
        raw = self._cfg(key, [])
        if raw is None:
            return []
        if isinstance(raw, str):
            return [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]
        if isinstance(raw, (list, tuple, set)):
            return [str(p).strip() for p in raw if str(p).strip()]
        return [str(raw).strip()] if str(raw).strip() else []

    def _get_merge_engine(self) -> Any:
        if self._merge_engine is None:
            self._merge_engine = WikiMergeEngine(repository=self._repo, config=self._config)
        return self._merge_engine

    def _extraction_blocks(self, knowledge_id: str, item: dict) -> list[ExtractionBlock]:
        source_revision = str(
            item.get("content_hash") or item.get("updated_at") or item.get("version") or ""
        )
        blocks: list[ExtractionBlock] = []

        raw_blocks = self._blocks.list_by_page(knowledge_id, limit=1000)
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

    def _guard_decisions(
        self,
        claims: list[Claim],
        decisions: list[ClaimMatchDecision],
    ) -> tuple[list[ClaimMatchDecision], dict[str, dict]]:
        guarded: list[ClaimMatchDecision] = []
        forced: dict[str, dict] = {}
        refines_min = float(self._cfg("wiki.canonical_v2.canary.refines_auto_merge_min_score", 0.9))

        for claim, decision in zip(claims, decisions):
            reason = ""
            if decision.action in {"contradicts", "supersedes"}:
                reason = "high_risk_action"
            elif decision.action == "refines" and decision.score < refines_min:
                reason = "low_confidence_refines"

            if not reason:
                guarded.append(decision)
                continue

            forced[claim.claim_id] = {
                "original_action": decision.action,
                "reason": reason,
                "score": decision.score,
            }
            guarded.append(ClaimMatchDecision(
                action="unresolved",
                target_claim_id=decision.target_claim_id,
                score=decision.score,
                reasons=list(decision.reasons) + [f"canary forced review: {reason}"],
                reason_codes=list(decision.reason_codes) + [reason],
            ))
        return guarded, forced

    def _apply(
        self,
        decisions: list[tuple[Claim, ClaimMatchDecision]],
        now: str,
    ) -> MergeResult:
        if not decisions:
            return MergeResult(diff="(no changes)", committed=False)
        return cast(MergeResult, self._get_merge_engine().apply(decisions, page=None, now=now))

    def _project_and_verify(self) -> dict:
        process_result = self._projection.process_outbox()
        findings = list(self._projection.verify_parity())
        rebuilt = False
        rebuild_result = None
        if findings and bool(self._cfg("wiki.canonical_v2.canary.auto_repair_projection", True)):
            rebuild_result = self._projection.rebuild()
            rebuilt = True
            findings = list(self._projection.verify_parity())
        return {
            "processed": int(getattr(process_result, "processed", 0)),
            "skipped": int(getattr(process_result, "skipped", 0)),
            "warnings": list(getattr(process_result, "warnings", [])),
            "errors": list(getattr(process_result, "errors", [])),
            "rebuilt": rebuilt,
            "rebuild_processed": int(getattr(rebuild_result, "processed", 0)) if rebuild_result else 0,
            "parity_findings": len(findings),
            "parity": [getattr(f, "message", str(f)) for f in findings],
        }

    def _annotate_forced_review(self, review_items: list[dict], forced: dict[str, dict]) -> None:
        for item in review_items:
            claim_id = item.get("claim_id")
            if claim_id in forced:
                item.update(forced[claim_id])

    def _build_report(
        self,
        *,
        knowledge_id: str,
        extracted_claims: list[Claim],
        raw_decisions: list[ClaimMatchDecision],
        guarded_decisions: list[ClaimMatchDecision],
        forced_review: dict[str, dict],
        merge_result: MergeResult,
        projection_report: dict,
        llm_calls: int,
        warnings: list[str],
        errors: list[str],
        latency_ms: int,
    ) -> dict:
        actions = [d.action for d in guarded_decisions]
        raw_actions = [d.action for d in raw_decisions]
        return {
            "status": "completed",
            "knowledge_id": knowledge_id,
            "allowlist": self.allowlist(),
            "claims_extracted": len(extracted_claims),
            "new_claims": len(merge_result.claims_created),
            "auto_publish": False,
            "auto_merged": sum(1 for action in actions if action in {"supports", "duplicate", "refines"}),
            "unresolved": sum(1 for action in actions if action == "unresolved"),
            "conflicts": sum(1 for action in raw_actions if action == "contradicts"),
            "forced_review": len(forced_review),
            "tx_id": merge_result.tx_id,
            "page_diff": merge_result.diff or "(no changes)",
            "review_items": list(merge_result.review_items),
            "projection": projection_report,
            "llm_calls": llm_calls,
            "latency_ms": latency_ms,
            "committed": merge_result.committed,
            "warnings": warnings,
            "errors": errors + list(merge_result.errors),
        }

    def _write_report(self, knowledge_id: str, report: dict) -> Path:
        wiki_dir = Path(self._cfg("knowledge_workflow.wiki_dir", getattr(self._repo, "_wiki_dir", "wiki")))
        reports_dir = wiki_dir / "_meta" / "canary_reports"
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
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
