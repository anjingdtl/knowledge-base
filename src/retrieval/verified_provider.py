"""Verified Wiki Serving provider — Gate-protected claim read path.

Responsibilities:
  query candidate claims → Serving Gate → score/rank → Eligible / Disclose

Must NOT: raw/vector search, RRF, rerank, answer generation, MCP envelope,
wiki writes, projection updates, claim creation, auto-publish.
"""
from __future__ import annotations

import logging
from typing import Any

from src.retrieval.models import VerifiedServingResult

logger = logging.getLogger(__name__)


def _claim_summary_dict(claim: Any, decision: Any) -> dict[str, Any]:
    """Stable, non-sensitive summary for eligible/disclose side-channels."""
    claim_id = getattr(claim, "claim_id", None) or getattr(decision, "claim_id", "")
    return {
        "claim_id": claim_id,
        "eligible": bool(getattr(decision, "eligible", False)),
        "disclose_only": bool(getattr(decision, "disclose_only", False)),
        "reason_codes": list(getattr(decision, "reason_codes", None) or []),
        "status": getattr(getattr(claim, "status", None), "value", str(getattr(claim, "status", ""))),
    }


class VerifiedProvider:
    """Adapter over WikiRepository + WikiServingGate (no rule rewrite)."""

    def __init__(
        self,
        wiki_repository=None,
        wiki_serving_gate=None,
        config=None,
    ):
        self._repo = wiki_repository
        self._gate = wiki_serving_gate
        self._config = config or {}

    def serve(self, query: str, *, limit: int = 10) -> VerifiedServingResult:
        """Return gate-filtered claim pairs; never raises to caller."""
        if self._repo is None:
            return VerifiedServingResult(
                eligible_claims=(),
                disclose_claims=(),
                fallback_reason="no_wiki_repository",
                trace={"stage": "verified_provider", "pairs": 0},
            )

        try:
            gate = self._gate
            if gate is None:
                from src.services.wiki_serving_gate import WikiServingGate

                gate = WikiServingGate()

            claims = self._repo.list_claims()
            # include_disclose=True so conflict side-channel can see them;
            # primary packaging still skips disclose_only rows.
            pairs = gate.filter_servable(
                claims, include_disclose=True, limit=limit,
            )

            from src.services.verified_hybrid_fusion import claim_retrieval_score

            scored = [
                (claim_retrieval_score(query, c), c, d) for c, d in pairs
            ]
            scored.sort(key=lambda x: x[0], reverse=True)
            selected = [
                (c, d) for s, c, d in scored if s > 0 or len(scored) <= limit
            ][:limit]

            eligible_rows: list[dict[str, Any]] = []
            disclose_rows: list[dict[str, Any]] = []
            for claim, decision in selected:
                row = _claim_summary_dict(claim, decision)
                if getattr(decision, "disclose_only", False) and not getattr(decision, "eligible", False):
                    disclose_rows.append(row)
                else:
                    eligible_rows.append(row)

            return VerifiedServingResult(
                eligible_claims=tuple(eligible_rows),
                disclose_claims=tuple(disclose_rows),
                claim_pairs=tuple(selected),
                trace={
                    "stage": "verified_provider",
                    "pairs": len(selected),
                    "eligible": len(eligible_rows),
                    "disclose": len(disclose_rows),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("VerifiedProvider.serve internal error: %s", e)
            return VerifiedServingResult(
                eligible_claims=(),
                disclose_claims=(),
                fallback_reason=str(e),
                warnings=(f"verified_provider_error:{e}",),
                trace={"stage": "verified_provider", "error": str(e)},
            )
