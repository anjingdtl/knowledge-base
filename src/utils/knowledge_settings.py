"""Single runtime resolver for verified-hybrid knowledge settings.

The resolver intentionally derives missing compatibility fields in memory only.
It never writes configuration files and preserves every unknown user-owned key.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.utils.knowledge_mode import (
    MODE_AUTHORING,
    MODE_EVIDENCE_ONLY,
    MODE_VERIFIED,
    is_legacy_mode_alias,
    resolve_knowledge_mode,
)

_MISSING = object()


@dataclass(frozen=True)
class EffectiveKnowledgeSettings:
    """Fully-resolved, read-only knowledge runtime contract."""

    mode: str
    wiki_read_enabled: bool
    authoring_enabled: bool
    verified_hybrid_enabled: bool
    maintenance_enabled: bool
    automation_level: str
    mcp_tool_profile: str
    mcp_write_policy: str
    allow_http_write: bool
    canonical_write_mode: str
    compatibility_warnings: tuple[str, ...]
    raw_mode: str | None
    sources: Mapping[str, str]


def _mapping_value(config: Mapping[str, Any], path: str) -> Any:
    if path in config:
        return config[path]
    current: Any = config
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _value(config: Mapping[str, Any] | Any | None, path: str) -> Any:
    if isinstance(config, Mapping):
        return _mapping_value(config, path)
    if config is not None:
        getter = getattr(config, "get", None)
        if callable(getter):
            return getter(path, _MISSING)
        return _MISSING
    from src.utils.config import Config

    return Config.get(path, _MISSING)


def _bool(value: Any, *, default: bool, path: str, warnings: list[str]) -> bool:
    if value is _MISSING or value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "yes", "1", "on"}:
            return True
        if token in {"false", "no", "0", "off"}:
            return False
    warnings.append(f"{path} has a non-boolean value; derived default is used")
    return default


def _string(value: Any, *, default: str, path: str, warnings: list[str]) -> str:
    if value is _MISSING or value is None:
        return default
    if isinstance(value, str) and value.strip():
        return value.strip()
    warnings.append(f"{path} has an invalid value; derived default is used")
    return default


def _resolved_value(
    config: Mapping[str, Any] | Any | None,
    path: str,
    *,
    default: Any,
    parser,
    warnings: list[str],
    sources: dict[str, str],
    field: str,
) -> Any:
    value = _value(config, path)
    if value is _MISSING or value is None:
        sources[field] = "derived:mode"
        return parser(default, default=default, path=path, warnings=warnings)
    sources[field] = f"explicit:{path}"
    return parser(value, default=default, path=path, warnings=warnings)


def resolve_effective_knowledge_settings(
    config: Mapping[str, Any] | Any | None = None,
) -> EffectiveKnowledgeSettings:
    """Resolve legacy and current config shapes into one effective contract.

    Explicit values always win. Missing values are derived from the resolved
    product mode and reported through ``sources`` / compatibility warnings.
    Invalid modes deliberately raise instead of silently falling back.
    """
    warnings: list[str] = []
    sources: dict[str, str] = {}

    raw_mode_value = _value(config, "knowledge_workflow.mode")
    raw_mode = None if raw_mode_value is _MISSING or raw_mode_value is None else str(raw_mode_value)
    mode = resolve_knowledge_mode(raw_mode)
    sources["mode"] = (
        "derived:default" if raw_mode_value is _MISSING or raw_mode_value is None
        else "explicit:knowledge_workflow.mode"
    )
    if is_legacy_mode_alias(raw_mode):
        warnings.append(f"legacy mode {raw_mode!r} resolves to {mode!r}; config is not rewritten")

    supports_wiki = mode in {MODE_VERIFIED, MODE_AUTHORING}
    authoring = mode == MODE_AUTHORING
    hybrid = supports_wiki

    wiki_read_enabled = _resolved_value(
        config, "wiki.read_enabled", default=supports_wiki, parser=_bool,
        warnings=warnings, sources=sources, field="wiki_read_enabled",
    )
    authoring_enabled = _resolved_value(
        config, "wiki.authoring_enabled", default=authoring, parser=_bool,
        warnings=warnings, sources=sources, field="authoring_enabled",
    )
    verified_hybrid_enabled = _resolved_value(
        config, "rag.verified_knowledge.enabled", default=hybrid, parser=_bool,
        warnings=warnings, sources=sources, field="verified_hybrid_enabled",
    )
    maintenance_enabled = _resolved_value(
        config, "maintenance.enabled", default=True, parser=_bool,
        warnings=warnings, sources=sources, field="maintenance_enabled",
    )
    automation_level = _resolved_value(
        config, "maintenance.automation_level", default="supervised", parser=_string,
        warnings=warnings, sources=sources, field="automation_level",
    )
    # Existing MCP configs predate tool_profile.  Their presence is an
    # integration signal, so retain the legacy surface (including write tools)
    # until the owner explicitly selects a profile.  A completely absent MCP
    # section is a new install and keeps the safer mode-derived default.
    has_legacy_mcp_settings = any(
        _value(config, path) is not _MISSING
        for path in ("mcp.write_policy", "mcp.allow_http_write", "mcp.auth_token")
    )
    default_profile = (
        "legacy" if has_legacy_mcp_settings else "extended" if authoring else "core"
    )
    mcp_tool_profile = _resolved_value(
        config, "mcp.tool_profile", default=default_profile, parser=_string,
        warnings=warnings, sources=sources, field="mcp_tool_profile",
    )
    default_write_policy = "local_confirm" if authoring or has_legacy_mcp_settings else "disabled"
    mcp_write_policy = _resolved_value(
        config, "mcp.write_policy", default=default_write_policy, parser=_string,
        warnings=warnings, sources=sources, field="mcp_write_policy",
    )
    allow_http_write = _resolved_value(
        config, "mcp.allow_http_write", default=False, parser=_bool,
        warnings=warnings, sources=sources, field="allow_http_write",
    )

    canonical_value = _value(config, "wiki.canonical_v2.mode")
    if canonical_value is _MISSING or canonical_value is None:
        canonical_write_mode = "off"
        sources["canonical_write_mode"] = "derived:mode"
    elif canonical_value is False:
        canonical_write_mode = "off"
        sources["canonical_write_mode"] = "explicit:wiki.canonical_v2.mode"
        warnings.append("wiki.canonical_v2.mode used YAML boolean false; normalized to string 'off'")
    elif canonical_value is True:
        canonical_write_mode = "primary"
        sources["canonical_write_mode"] = "explicit:wiki.canonical_v2.mode"
        warnings.append("wiki.canonical_v2.mode used YAML boolean true; normalized to string 'primary'")
    else:
        canonical_write_mode = _string(
            canonical_value, default="off", path="wiki.canonical_v2.mode", warnings=warnings,
        ).lower()
        sources["canonical_write_mode"] = "explicit:wiki.canonical_v2.mode"

    if mode == MODE_EVIDENCE_ONLY and verified_hybrid_enabled:
        warnings.append("evidence_only mode has verified hybrid explicitly enabled")
    if not wiki_read_enabled and verified_hybrid_enabled:
        warnings.append("verified hybrid is enabled while wiki read is explicitly disabled")

    return EffectiveKnowledgeSettings(
        mode=mode,
        wiki_read_enabled=wiki_read_enabled,
        authoring_enabled=authoring_enabled,
        verified_hybrid_enabled=verified_hybrid_enabled,
        maintenance_enabled=maintenance_enabled,
        automation_level=automation_level,
        mcp_tool_profile=mcp_tool_profile,
        mcp_write_policy=mcp_write_policy,
        allow_http_write=allow_http_write,
        canonical_write_mode=canonical_write_mode,
        compatibility_warnings=tuple(warnings),
        raw_mode=raw_mode,
        sources=dict(sources),
    )
