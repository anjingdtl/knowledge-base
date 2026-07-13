"""Knowledge workflow mode resolution (verified / authoring / evidence_only).

Spec: docs/ShineHeKnowledge 融合收束开发规格说明.md §4–§5.

Canonical modes:
  - verified      — default: Raw + verified Wiki read intent; no Agent authoring
  - authoring     — full Wiki maintenance (maps from legacy wiki_first)
  - evidence_only — Raw Retrieval only (maps from legacy)

Legacy config values are accepted at runtime and never silently rewritten on disk.
"""
from __future__ import annotations

from typing import Any, Mapping

# Canonical product modes
MODE_VERIFIED = "verified"
MODE_AUTHORING = "authoring"
MODE_EVIDENCE_ONLY = "evidence_only"

CANONICAL_MODES: frozenset[str] = frozenset(
    {MODE_VERIFIED, MODE_AUTHORING, MODE_EVIDENCE_ONLY}
)

# Historical config values → canonical
LEGACY_MODE_MAP: dict[str, str] = {
    "wiki_first": MODE_AUTHORING,
    "legacy": MODE_EVIDENCE_ONLY,
}

# CLI accepts hyphens; YAML uses underscores
_CLI_ALIASES: dict[str, str] = {
    "evidence-only": MODE_EVIDENCE_ONLY,
    "wiki-first": "wiki_first",  # resolved via LEGACY_MODE_MAP
}


class InvalidKnowledgeModeError(ValueError):
    """Raised when a mode string cannot be resolved."""


def normalize_mode_token(raw: str | None) -> str | None:
    """Normalize user/config tokens: strip, lower, map CLI hyphens."""
    if raw is None:
        return None
    token = str(raw).strip().lower()
    if not token:
        return None
    if token in _CLI_ALIASES:
        token = _CLI_ALIASES[token]
    # evidence-only already handled; also allow verified/authoring with hyphens
    token = token.replace("-", "_")
    return token


def resolve_knowledge_mode(
    raw: str | None,
    *,
    default: str = MODE_VERIFIED,
) -> str:
    """Resolve config/CLI mode to a canonical mode.

    Rules (Spec §5.1):
      - unset / empty → default (verified)
      - wiki_first → authoring
      - legacy → evidence_only
      - verified | authoring | evidence_only → as-is
      - illegal → InvalidKnowledgeModeError (no silent guess)
    """
    token = normalize_mode_token(raw)
    if token is None:
        resolved_default = normalize_mode_token(default) or MODE_VERIFIED
        if resolved_default in LEGACY_MODE_MAP:
            return LEGACY_MODE_MAP[resolved_default]
        if resolved_default not in CANONICAL_MODES:
            raise InvalidKnowledgeModeError(
                f"非法默认知识模式: {default!r}；"
                f"允许: {sorted(CANONICAL_MODES)}"
            )
        return resolved_default

    if token in LEGACY_MODE_MAP:
        return LEGACY_MODE_MAP[token]
    if token in CANONICAL_MODES:
        return token
    raise InvalidKnowledgeModeError(
        f"非法知识模式: {raw!r}；"
        f"允许: verified | authoring | evidence_only "
        f"（兼容旧值 wiki_first → authoring, legacy → evidence_only）"
    )


def is_legacy_mode_alias(raw: str | None) -> bool:
    """True if the raw config token is a deprecated alias."""
    token = normalize_mode_token(raw)
    return token in LEGACY_MODE_MAP


def allows_authoring(mode: str | None) -> bool:
    """Whether Wiki Authoring / compile / maintenance write path is allowed."""
    return resolve_knowledge_mode(mode) == MODE_AUTHORING


def allows_wiki_read(mode: str | None) -> bool:
    """Whether Verified Wiki read intent is on (verified + authoring)."""
    return resolve_knowledge_mode(mode) in {MODE_VERIFIED, MODE_AUTHORING}


def is_evidence_only(mode: str | None) -> bool:
    return resolve_knowledge_mode(mode) == MODE_EVIDENCE_ONLY


def get_configured_knowledge_mode(
    config: Mapping[str, Any] | None = None,
    *,
    default: str = MODE_VERIFIED,
) -> str:
    """Read mode from a config mapping or live Config, then resolve.

    Does not mutate config files.
    """
    if config is not None:
        kw = config.get("knowledge_workflow") if isinstance(config, Mapping) else None
        if isinstance(kw, Mapping):
            raw = kw.get("mode")
        else:
            raw = config.get("knowledge_workflow.mode")  # type: ignore[union-attr]
        return resolve_knowledge_mode(raw, default=default)

    from src.utils.config import Config

    raw = Config.get("knowledge_workflow.mode", None)
    return resolve_knowledge_mode(raw, default=default)


def describe_mode(mode: str | None) -> dict[str, Any]:
    """Human/Doctor-friendly mode summary."""
    raw = None if mode is None else str(mode)
    resolved = resolve_knowledge_mode(mode)
    return {
        "raw": raw,
        "resolved": resolved,
        "legacy_alias": is_legacy_mode_alias(raw),
        "wiki_read_enabled": allows_wiki_read(resolved),
        "authoring_enabled": allows_authoring(resolved),
        "raw_retrieval": True,
        "deprecation_hint": (
            f"配置值 {raw!r} 已弃用，语义等价于 {resolved!r}；"
            f"建议在下次人工编辑时改为 knowledge_workflow.mode: {resolved}"
            if is_legacy_mode_alias(raw)
            else None
        ),
    }
