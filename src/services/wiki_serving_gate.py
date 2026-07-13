"""Wiki Serving Eligibility Gate — unique Claim entry for Search / Ask.

Spec: docs/ShineHeKnowledge 融合收束开发规格说明.md §6 / Phase 2.

Rules (primary reliable conclusion):
  status == active
  AND at least one non-stale resolvable supports Evidence
  AND block evidence present when require_block_evidence
  AND knowledge not soft-deleted
  AND excerpt_hash matches current block (when hash is recorded)
  AND claim.validate() passes
  AND review_required == false (DISPUTED / SUPERSEDED are not primary)

No LLM calls — deterministic only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from src.models.wiki_v2 import Claim, ClaimStatus, Evidence, EvidenceStance
from src.services.wiki_claim_extractor import compute_excerpt_hash

# ---------------------------------------------------------------------------
# Reason codes (Spec §6.4) — stable for Trace / Eval
# ---------------------------------------------------------------------------
REASON_SERVING_DISABLED = "serving_disabled"
REASON_WIKI_READ_DISABLED = "wiki_read_disabled"
REASON_CLAIM_STATUS_NOT_ALLOWED = "claim_status_not_allowed"
REASON_CLAIM_STALE = "claim_stale"
REASON_CLAIM_UNSUPPORTED = "claim_unsupported"
REASON_CLAIM_RETRACTED = "claim_retracted"
REASON_CLAIM_DRAFT = "claim_draft"
REASON_CLAIM_SUPERSEDED = "claim_superseded"
REASON_MISSING_EVIDENCE = "missing_evidence"
REASON_EVIDENCE_BLOCK_MISSING = "evidence_block_missing"
REASON_EVIDENCE_HASH_MISMATCH = "evidence_hash_mismatch"
REASON_KNOWLEDGE_DELETED = "knowledge_deleted"
REASON_VALIDATION_FAILED = "validation_failed"
REASON_SERVING_VALIDATION_MISSING = "serving_validation_missing"
REASON_REVIEW_NOT_APPROVED = "review_not_approved"
REASON_VALIDATED_REVISION_STALE = "validated_revision_stale"
REASON_PUBLISHED_REVISION_STALE = "published_revision_stale"
REASON_SERVING_EVIDENCE_MISSING = "serving_evidence_missing"
REASON_REVIEW_REQUIRED = "review_required"
REASON_SCOPE_MISMATCH = "scope_mismatch"
REASON_UNIT_INCOMPATIBLE = "unit_incompatible"
REASON_POLARITY_MISMATCH = "polarity_mismatch"
REASON_INTENSITY_MISMATCH = "intensity_mismatch"

PRIMARY_ALLOWED_STATUSES_DEFAULT: frozenset[str] = frozenset({ClaimStatus.ACTIVE.value})

# Statuses that may be disclosed (not primary) when policy=disclose
DISCLOSE_STATUSES: frozenset[str] = frozenset({
    ClaimStatus.DISPUTED.value,
})


GetBlockFn = Callable[[str], Mapping[str, Any] | None]
GetKnowledgeFn = Callable[[str], Mapping[str, Any] | None]
HashFn = Callable[[str], str]


@dataclass
class ResolvedEvidence:
    """Evidence after block / knowledge / hash resolution."""

    evidence: Evidence
    block: Mapping[str, Any] | None = None
    knowledge: Mapping[str, Any] | None = None
    current_excerpt_hash: str | None = None
    ok: bool = False
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence.evidence_id,
            "claim_ref_knowledge_id": self.evidence.knowledge_id,
            "block_id": self.evidence.block_id,
            "stance": self.evidence.stance.value,
            "stale": self.evidence.stale,
            "ok": self.ok,
            "reason_codes": list(self.reason_codes),
            "current_excerpt_hash": self.current_excerpt_hash,
            "stored_excerpt_hash": self.evidence.excerpt_hash,
        }


@dataclass
class ServingDecision:
    """Gate result for one Claim."""

    claim_id: str
    eligible: bool
    """True only when claim may be a reliable primary answer."""

    disclose_only: bool = False
    """True when claim may appear as conflict/ambiguity disclosure, not sole answer."""

    reason_codes: list[str] = field(default_factory=list)
    resolved_evidence: list[ResolvedEvidence] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "eligible": self.eligible,
            "disclose_only": self.disclose_only,
            "reason_codes": list(self.reason_codes),
            "resolved_evidence": [r.to_dict() for r in self.resolved_evidence],
            "diagnostics": dict(self.diagnostics),
        }


@dataclass
class ServingGateConfig:
    enabled: bool = True
    allowed_claim_statuses: frozenset[str] = field(
        default_factory=lambda: PRIMARY_ALLOWED_STATUSES_DEFAULT,
    )
    require_block_evidence: bool = True
    exclude_stale: bool = True
    exclude_unsupported: bool = True
    exclude_retracted: bool = True
    require_validation_passed: bool = False
    require_review_approved: bool = False
    require_published_revision: bool = False
    unresolved_policy: str = "disclose"  # disclose | exclude
    contradiction_policy: str = "disclose"
    on_failure: str = "raw_fallback"
    max_claims_per_query: int = 8
    max_evidence_per_claim: int = 3
    check_excerpt_hash: bool = True

    @classmethod
    def from_mapping(cls, cfg: Mapping[str, Any] | None) -> "ServingGateConfig":
        if not cfg:
            return cls()
        statuses = cfg.get("allowed_claim_statuses") or list(PRIMARY_ALLOWED_STATUSES_DEFAULT)
        return cls(
            enabled=bool(cfg.get("enabled", True)),
            allowed_claim_statuses=frozenset(str(s) for s in statuses),
            require_block_evidence=bool(cfg.get("require_block_evidence", True)),
            exclude_stale=bool(cfg.get("exclude_stale", True)),
            exclude_unsupported=bool(cfg.get("exclude_unsupported", True)),
            exclude_retracted=bool(cfg.get("exclude_retracted", True)),
            require_validation_passed=bool(cfg.get("require_validation_passed", True)),
            require_review_approved=bool(cfg.get("require_review_approved", True)),
            require_published_revision=bool(cfg.get("require_published_revision", True)),
            unresolved_policy=str(cfg.get("unresolved_policy", "disclose")),
            contradiction_policy=str(cfg.get("contradiction_policy", "disclose")),
            on_failure=str(cfg.get("on_failure", "raw_fallback")),
            max_claims_per_query=int(cfg.get("max_claims_per_query", 8)),
            max_evidence_per_claim=int(cfg.get("max_evidence_per_claim", 3)),
            check_excerpt_hash=bool(cfg.get("check_excerpt_hash", True)),
        )


def load_serving_gate_config(
    config: Mapping[str, Any] | None = None,
) -> ServingGateConfig:
    """Load wiki.serving from mapping or live Config."""
    if config is not None:
        wiki = config.get("wiki") if isinstance(config.get("wiki"), Mapping) else {}
        serving = (wiki or {}).get("serving") if isinstance(wiki, Mapping) else None
        if serving is None and "serving" in config:
            serving = config.get("serving")
        return ServingGateConfig.from_mapping(
            serving if isinstance(serving, Mapping) else None,
        )
    from src.utils.config import Config

    raw = Config.get("wiki.serving", None)
    if isinstance(raw, Mapping):
        return ServingGateConfig.from_mapping(raw)
    # Nested get may return None — try individual keys for partial configs
    return ServingGateConfig.from_mapping({
        "enabled": Config.get("wiki.serving.enabled", True),
        "allowed_claim_statuses": Config.get(
            "wiki.serving.allowed_claim_statuses",
            list(PRIMARY_ALLOWED_STATUSES_DEFAULT),
        ),
        "require_block_evidence": Config.get("wiki.serving.require_block_evidence", True),
        "exclude_stale": Config.get("wiki.serving.exclude_stale", True),
        "exclude_unsupported": Config.get("wiki.serving.exclude_unsupported", True),
        "exclude_retracted": Config.get("wiki.serving.exclude_retracted", True),
        "require_validation_passed": Config.get(
            "wiki.serving.require_validation_passed", True,
        ),
        "require_review_approved": Config.get("wiki.serving.require_review_approved", True),
        "require_published_revision": Config.get("wiki.serving.require_published_revision", True),
        "unresolved_policy": Config.get("wiki.serving.unresolved_policy", "disclose"),
        "contradiction_policy": Config.get(
            "wiki.serving.contradiction_policy", "disclose",
        ),
        "on_failure": Config.get("wiki.serving.on_failure", "raw_fallback"),
        "max_claims_per_query": Config.get("wiki.serving.max_claims_per_query", 8),
        "max_evidence_per_claim": Config.get("wiki.serving.max_evidence_per_claim", 3),
    })


class WikiServingGate:
    """Deterministic Claim serving eligibility gate (no LLM)."""

    def __init__(
        self,
        *,
        config: ServingGateConfig | Mapping[str, Any] | None = None,
        get_block: GetBlockFn | None = None,
        get_knowledge: GetKnowledgeFn | None = None,
        hash_fn: HashFn = compute_excerpt_hash,
        knowledge_mode: str | None = None,
        wiki_read_enabled: bool | None = None,
    ):
        if isinstance(config, ServingGateConfig):
            self._cfg = config
        elif isinstance(config, Mapping):
            # full app config or serving section
            if "allowed_claim_statuses" in config or "enabled" in config:
                self._cfg = ServingGateConfig.from_mapping(config)
            else:
                self._cfg = load_serving_gate_config(config)
        else:
            self._cfg = load_serving_gate_config(None)
        self._get_block = get_block
        self._get_knowledge = get_knowledge
        self._hash_fn = hash_fn
        self._knowledge_mode = knowledge_mode
        self._wiki_read_enabled = wiki_read_enabled

    @property
    def config(self) -> ServingGateConfig:
        return self._cfg

    def _mode_allows_read(self) -> bool:
        if self._wiki_read_enabled is False:
            return False
        if self._wiki_read_enabled is True:
            return True
        if self._knowledge_mode is not None:
            from src.utils.knowledge_mode import allows_wiki_read

            return allows_wiki_read(self._knowledge_mode)
        try:
            from src.utils.knowledge_settings import resolve_effective_knowledge_settings

            return resolve_effective_knowledge_settings().wiki_read_enabled
        except Exception:  # noqa: BLE001
            return False

    def evaluate(self, claim: Claim) -> ServingDecision:
        """Evaluate one claim for primary serving eligibility."""
        codes: list[str] = []
        resolved: list[ResolvedEvidence] = []
        diag: dict[str, Any] = {
            "status": claim.status.value,
            "evidence_count": len(claim.evidence),
        }

        if not self._cfg.enabled:
            return ServingDecision(
                claim_id=claim.claim_id,
                eligible=False,
                reason_codes=[REASON_SERVING_DISABLED],
                diagnostics=diag,
            )
        if not self._mode_allows_read():
            return ServingDecision(
                claim_id=claim.claim_id,
                eligible=False,
                reason_codes=[REASON_WIKI_READ_DISABLED],
                diagnostics=diag,
            )

        status = claim.status
        status_val = status.value

        # Explicit status gates
        if status is ClaimStatus.RETRACTED:
            return ServingDecision(
                claim_id=claim.claim_id, eligible=False,
                reason_codes=[REASON_CLAIM_RETRACTED], diagnostics=diag,
            )
        if status is ClaimStatus.UNSUPPORTED:
            return ServingDecision(
                claim_id=claim.claim_id, eligible=False,
                reason_codes=[REASON_CLAIM_UNSUPPORTED], diagnostics=diag,
            )
        if status is ClaimStatus.DRAFT:
            return ServingDecision(
                claim_id=claim.claim_id, eligible=False,
                reason_codes=[REASON_CLAIM_DRAFT, REASON_CLAIM_STATUS_NOT_ALLOWED],
                diagnostics=diag,
            )
        if status is ClaimStatus.SUPERSEDED:
            return ServingDecision(
                claim_id=claim.claim_id, eligible=False,
                reason_codes=[REASON_CLAIM_SUPERSEDED, REASON_CLAIM_STATUS_NOT_ALLOWED],
                diagnostics=diag,
            )

        # DISPUTED / unresolved → disclose only (never primary)
        if status is ClaimStatus.DISPUTED:
            if self._cfg.contradiction_policy == "disclose":
                # Still resolve evidence for disclosure packaging
                resolved = self._resolve_supports(claim)
                return ServingDecision(
                    claim_id=claim.claim_id,
                    eligible=False,
                    disclose_only=True,
                    reason_codes=[REASON_REVIEW_REQUIRED],
                    resolved_evidence=resolved,
                    diagnostics=diag,
                )
            return ServingDecision(
                claim_id=claim.claim_id,
                eligible=False,
                reason_codes=[REASON_REVIEW_REQUIRED, REASON_CLAIM_STATUS_NOT_ALLOWED],
                diagnostics=diag,
            )

        if status_val not in self._cfg.allowed_claim_statuses:
            return ServingDecision(
                claim_id=claim.claim_id,
                eligible=False,
                reason_codes=[REASON_CLAIM_STATUS_NOT_ALLOWED],
                diagnostics=diag,
            )

        # Model invariant validation
        if self._cfg.require_validation_passed:
            val_errors = claim.validate()
            if val_errors:
                return ServingDecision(
                    claim_id=claim.claim_id,
                    eligible=False,
                    reason_codes=[REASON_VALIDATION_FAILED],
                    diagnostics={**diag, "validation_errors": val_errors},
                )

        if (
            self._cfg.require_validation_passed
            or self._cfg.require_review_approved
            or self._cfg.require_published_revision
        ):
            validation = claim.serving_validation
            if validation is None:
                return ServingDecision(
                    claim_id=claim.claim_id,
                    eligible=False,
                    reason_codes=[REASON_SERVING_VALIDATION_MISSING],
                    diagnostics=diag,
                )
            validation_codes: list[str] = []
            if self._cfg.require_validation_passed and not validation.passed:
                validation_codes.append(REASON_VALIDATION_FAILED)
            if validation.validated_revision != claim.revision:
                validation_codes.append(REASON_VALIDATED_REVISION_STALE)
            if self._cfg.require_review_approved and not validation.review_approved:
                validation_codes.append(REASON_REVIEW_NOT_APPROVED)
            if self._cfg.require_published_revision and validation.published_revision != claim.revision:
                validation_codes.append(REASON_PUBLISHED_REVISION_STALE)
            if not validation.serving_evidence_ids:
                validation_codes.append(REASON_SERVING_EVIDENCE_MISSING)
            if validation_codes:
                return ServingDecision(
                    claim_id=claim.claim_id,
                    eligible=False,
                    reason_codes=validation_codes,
                    diagnostics=diag,
                )

        # Evidence resolution
        resolved = self._resolve_supports(claim)
        supports = [
            e for e in claim.evidence if e.stance is EvidenceStance.SUPPORTS
        ]
        if claim.serving_validation is not None and (
            self._cfg.require_validation_passed
            or self._cfg.require_review_approved
            or self._cfg.require_published_revision
        ):
            serving_ids = set(claim.serving_validation.serving_evidence_ids)
            supports = [e for e in supports if e.evidence_id in serving_ids]
            resolved = [r for r in resolved if r.evidence.evidence_id in serving_ids]
        ok_supports = [r for r in resolved if r.ok]
        diag["ok_supports"] = len(ok_supports)
        diag["resolved_supports"] = len(resolved)
        if not supports:
            codes.append(REASON_MISSING_EVIDENCE)
            return ServingDecision(
                claim_id=claim.claim_id, eligible=False,
                reason_codes=codes, resolved_evidence=resolved, diagnostics=diag,
            )

        if self._cfg.exclude_stale:
            non_stale = [e for e in supports if not e.stale]
            if not non_stale:
                codes.append(REASON_CLAIM_STALE)
                return ServingDecision(
                    claim_id=claim.claim_id, eligible=False,
                    reason_codes=codes, resolved_evidence=resolved, diagnostics=diag,
                )

        if not ok_supports:
            # Aggregate failure codes from resolutions
            for r in resolved:
                for c in r.reason_codes:
                    if c not in codes:
                        codes.append(c)
            if not codes:
                codes.append(REASON_MISSING_EVIDENCE)
            return ServingDecision(
                claim_id=claim.claim_id, eligible=False,
                reason_codes=codes, resolved_evidence=resolved, diagnostics=diag,
            )

        # Scope / unit / polarity / intensity: carry matcher-style tags if present
        # on evidence.location.diagnostics (deterministic, no LLM).
        for r in ok_supports:
            loc = r.evidence.location or {}
            flags = loc.get("serving_flags") or loc.get("match_reason_codes") or []
            if isinstance(flags, (list, tuple)):
                for f in flags:
                    if f in {
                        REASON_SCOPE_MISMATCH,
                        REASON_UNIT_INCOMPATIBLE,
                        REASON_POLARITY_MISMATCH,
                        REASON_INTENSITY_MISMATCH,
                    } and f not in codes:
                        codes.append(str(f))
        if codes:
            # These demote to non-primary (disclose if policy allows)
            return ServingDecision(
                claim_id=claim.claim_id,
                eligible=False,
                disclose_only=self._cfg.unresolved_policy == "disclose",
                reason_codes=codes,
                resolved_evidence=resolved,
                diagnostics=diag,
            )

        # Cap evidence for packaging
        capped = ok_supports[: max(1, self._cfg.max_evidence_per_claim)]
        return ServingDecision(
            claim_id=claim.claim_id,
            eligible=True,
            disclose_only=False,
            reason_codes=[],
            resolved_evidence=capped,
            diagnostics=diag,
        )

    def resolve_claim_evidence(self, claim: Claim) -> list[ResolvedEvidence]:
        """Public Evidence Resolution for one claim (supports only)."""
        out: list[ResolvedEvidence] = []
        for ev in claim.evidence:
            if ev.stance is not EvidenceStance.SUPPORTS:
                continue
            out.append(self.resolve_evidence(ev))
        return out

    # Back-compat alias used internally
    def _resolve_supports(self, claim: Claim) -> list[ResolvedEvidence]:
        return self.resolve_claim_evidence(claim)

    def resolve_evidence(self, evidence: Evidence) -> ResolvedEvidence:
        """Resolve one evidence row against live Block / Knowledge stores."""
        codes: list[str] = []
        if evidence.stale and self._cfg.exclude_stale:
            return ResolvedEvidence(
                evidence=evidence, ok=False, reason_codes=[REASON_CLAIM_STALE],
            )

        if self._cfg.require_block_evidence and not evidence.block_id:
            return ResolvedEvidence(
                evidence=evidence,
                ok=False,
                reason_codes=[REASON_EVIDENCE_BLOCK_MISSING],
            )

        knowledge = None
        if self._get_knowledge is not None and evidence.knowledge_id:
            knowledge = self._get_knowledge(evidence.knowledge_id)
            if knowledge is None:
                return ResolvedEvidence(
                    evidence=evidence,
                    ok=False,
                    reason_codes=[REASON_KNOWLEDGE_DELETED],
                )
            # Soft-delete: deleted_at set
            if knowledge.get("deleted_at"):
                return ResolvedEvidence(
                    evidence=evidence,
                    knowledge=knowledge,
                    ok=False,
                    reason_codes=[REASON_KNOWLEDGE_DELETED],
                )

        block = None
        current_hash: str | None = None
        if evidence.block_id and self._get_block is not None:
            block = self._get_block(evidence.block_id)
            if block is None:
                return ResolvedEvidence(
                    evidence=evidence,
                    knowledge=knowledge,
                    ok=False,
                    reason_codes=[REASON_EVIDENCE_BLOCK_MISSING],
                )
            content = block.get("content") or ""
            if content and self._cfg.check_excerpt_hash and evidence.excerpt_hash:
                current_hash = self._hash_fn(content)
                if current_hash != evidence.excerpt_hash:
                    return ResolvedEvidence(
                        evidence=evidence,
                        block=block,
                        knowledge=knowledge,
                        current_excerpt_hash=current_hash,
                        ok=False,
                        reason_codes=[REASON_EVIDENCE_HASH_MISMATCH],
                    )
            elif content:
                current_hash = self._hash_fn(content)
        elif evidence.block_id and self._get_block is None and self._cfg.require_block_evidence:
            # Cannot verify block existence — fail closed when require_block_evidence
            return ResolvedEvidence(
                evidence=evidence,
                knowledge=knowledge,
                ok=False,
                reason_codes=[REASON_EVIDENCE_BLOCK_MISSING],
            )

        return ResolvedEvidence(
            evidence=evidence,
            block=block,
            knowledge=knowledge,
            current_excerpt_hash=current_hash,
            ok=True,
            reason_codes=codes,
        )

    def filter_servable(
        self,
        claims: Sequence[Claim],
        *,
        include_disclose: bool = False,
        limit: int | None = None,
    ) -> list[tuple[Claim, ServingDecision]]:
        """Return claims that pass primary gate (optionally + disclose_only).

        This is the **only** path Search/Ask should use to obtain Wiki claims.
        """
        cap = limit if limit is not None else self._cfg.max_claims_per_query
        out: list[tuple[Claim, ServingDecision]] = []
        for claim in claims:
            decision = self.evaluate(claim)
            if decision.eligible or (include_disclose and decision.disclose_only):
                out.append((claim, decision))
            if len(out) >= cap:
                break
        return out

    def diagnostics_for_claims(
        self, claims: Sequence[Claim],
    ) -> dict[str, Any]:
        """Aggregate serving diagnostics (Doctor / health)."""
        by_reason: dict[str, int] = {}
        eligible = 0
        disclose = 0
        excluded = 0
        status_counts: dict[str, int] = {}
        decisions: list[dict[str, Any]] = []

        for claim in claims:
            status_counts[claim.status.value] = status_counts.get(claim.status.value, 0) + 1
            d = self.evaluate(claim)
            decisions.append({
                "claim_id": claim.claim_id,
                "eligible": d.eligible,
                "disclose_only": d.disclose_only,
                "reason_codes": d.reason_codes,
            })
            if d.eligible:
                eligible += 1
            elif d.disclose_only:
                disclose += 1
            else:
                excluded += 1
            for code in d.reason_codes:
                by_reason[code] = by_reason.get(code, 0) + 1

        total = len(claims)
        return {
            "total_claims": total,
            "eligible_primary": eligible,
            "disclose_only": disclose,
            "excluded": excluded,
            "serving_rate": (eligible / total) if total else 0.0,
            "stale_serving_count": by_reason.get(REASON_CLAIM_STALE, 0),
            "unsupported_serving_count": 0,  # unsupported never eligible
            "by_reason": by_reason,
            "by_status": status_counts,
            "gate_uses_llm": False,
            "on_failure": self._cfg.on_failure,
            "decisions_sample": decisions[:50],
        }


def default_block_knowledge_lookups() -> tuple[GetBlockFn, GetKnowledgeFn]:
    """Production DB-backed lookups (lazy import to avoid cycles)."""

    def get_block(block_id: str) -> Mapping[str, Any] | None:
        from src.services.db import Database

        return Database.get_block(block_id)

    def get_knowledge(knowledge_id: str) -> Mapping[str, Any] | None:
        from src.services.db import Database

        return Database.get_knowledge(knowledge_id, include_deleted=True)

    return get_block, get_knowledge
