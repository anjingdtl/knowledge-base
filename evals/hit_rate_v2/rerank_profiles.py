"""Reranker evaluation profiles: deterministic baseline vs provider-enhanced.

Phase 0 does not fix external providers. It makes requested vs effective
profile state honest and non-confusable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

RerankProfileName = Literal["deterministic-baseline", "provider-enhanced"]

VALID_PROFILES: frozenset[str] = frozenset(
    {"deterministic-baseline", "provider-enhanced"}
)


@dataclass
class RerankProfileStatus:
    requested_profile: str
    effective_profile: str
    provider_available: bool | None = None
    provider_timeout_s: float | None = None
    fallback_reason: str | None = None
    blocked: bool = False
    track_status: str = "ok"  # ok | blocked | fallback_internal
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_rerank_profile(
    requested: str,
    *,
    provider_available: bool | None = None,
    provider_timeout_s: float | None = None,
    probe_error: str | None = None,
) -> RerankProfileStatus:
    """Resolve requested profile to effective track status.

    Rules:
    - deterministic-baseline always runs locally; never claims provider equivalence.
    - provider-enhanced must not silently rename to normal/fallback on failure;
      the track is ``blocked`` when provider is unavailable.
    """
    req = (requested or "").strip()
    if req not in VALID_PROFILES:
        raise ValueError(
            f"invalid --rerank-profile {requested!r}; "
            f"expected one of {sorted(VALID_PROFILES)}"
        )

    if req == "deterministic-baseline":
        return RerankProfileStatus(
            requested_profile=req,
            effective_profile="deterministic-baseline",
            provider_available=provider_available,
            provider_timeout_s=provider_timeout_s,
            fallback_reason=None,
            blocked=False,
            track_status="ok",
            notes=[
                "deterministic-baseline is independent; "
                "does not prove provider-enhanced equivalence",
            ],
        )

    # provider-enhanced
    if provider_available is False or probe_error:
        reason = probe_error or "provider_unavailable"
        return RerankProfileStatus(
            requested_profile=req,
            effective_profile="blocked",
            provider_available=False,
            provider_timeout_s=provider_timeout_s,
            fallback_reason=reason,
            blocked=True,
            track_status="blocked",
            notes=[
                "provider-enhanced track blocked; "
                "must not be reported as normal/deterministic success",
            ],
        )

    return RerankProfileStatus(
        requested_profile=req,
        effective_profile="provider-enhanced",
        provider_available=True if provider_available is None else provider_available,
        provider_timeout_s=provider_timeout_s,
        fallback_reason=None,
        blocked=False,
        track_status="ok",
        notes=[],
    )


def redacted_probe_log(message: str) -> str:
    """Strip secrets from provider probe diagnostics before logging."""
    import re

    text = str(message or "")
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(\S+)",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(api[_-]?key\s*[:=]\s*)(\S+)",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(bearer\s+)([A-Za-z0-9._\-+/=]+)",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"sk-[A-Za-z0-9]{10,}",
        "sk-[REDACTED]",
        text,
    )
    return text
