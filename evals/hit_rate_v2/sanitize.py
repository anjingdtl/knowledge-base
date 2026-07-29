"""Sanitize hit-rate run artifacts for Git-safe summaries.

Raw evidence stays local under ``.local/eval-runs/``.
Git may only keep redacted summaries under ``artifacts/eval-summaries/``.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

# Keys whose full text must never enter Git summaries.
REDACT_FULL_KEYS = frozenset(
    {
        "text",
        "content",
        "answer",
        "raw_evidence_used",
        "prompt",
        "system_prompt",
        "user_prompt",
        "messages",
        "body",
        "authorization",
        "api_key",
        "apikey",
        "cookie",
        "set-cookie",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
    }
)

# Keys preserved as-is when present (identifiers / metrics).
SAFE_KEYS = frozenset(
    {
        "case_id",
        "id",
        "knowledge_id",
        "passage_id",
        "block_id",
        "document_family_id",
        "version_year",
        "source_version",
        "score",
        "rank",
        "distance",
        "vector_score",
        "keyword_score",
        "top1_hit",
        "recall5",
        "ask_fact_correct",
        "ask_citation_valid",
        "e2e_pass",
        "false_positive",
        "reason_codes",
        "defect_severity",
        "defect_category",
        "defect_reason",
        "metric_contract_version",
        "answer_mode",
        "mode",
        "latency_ms",
        "elapsed_ms",
        "elapsed_s",
        "git_revision",
        "golden_sha256",
        "scorer_sha256",
        "config_hash",
        "db_revision",
        "index_revision",
        "run_fingerprint",
        "rerank_profile",
        "requested_profile",
        "effective_profile",
        "track_status",
        "split",
        "schema_version",
        "type",
        "category",
        "risk_level",
        "ok",
        "code",
        "message",
    }
)

_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ABS_PATH_RE = re.compile(
    r"(?i)(?:[A-Za-z]:\\|\\\\|/(?:home|Users|var|tmp|private)/)[^\s\"']+"
)
_AUTH_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)(\S+)")
_API_KEY_RE = re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)(\S+)")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-+/=]+)")
_SK_RE = re.compile(r"sk-[A-Za-z0-9]{10,}")


def _hash_preview(value: Any, *, n: int = 12) -> str:
    raw = str(value if value is not None else "").encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:n]


def redact_string(text: str) -> str:
    s = str(text)
    s = _AUTH_RE.sub(r"\1[REDACTED]", s)
    s = _API_KEY_RE.sub(r"\1[REDACTED]", s)
    s = _BEARER_RE.sub(r"\1[REDACTED]", s)
    s = _SK_RE.sub("sk-[REDACTED]", s)
    s = _PHONE_RE.sub("[PHONE_REDACTED]", s)
    s = _EMAIL_RE.sub("[EMAIL_REDACTED]", s)
    s = _ABS_PATH_RE.sub("[PATH_REDACTED]", s)
    return s


def sanitize_value(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    if depth > 12:
        return "[TRUNCATED_DEPTH]"
    if key is not None:
        lk = key.lower()
        if lk in REDACT_FULL_KEYS or any(
            part in lk for part in ("authorization", "api_key", "password", "secret", "cookie")
        ):
            if value is None or value == "" or value == [] or value == {}:
                return value
            return {
                "_redacted": True,
                "sha256_12": _hash_preview(value),
                "type": type(value).__name__,
                "length": len(value) if hasattr(value, "__len__") else None,
            }
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            sk = str(k)
            # Always keep structural metric containers
            out[sk] = sanitize_value(v, key=sk, depth=depth + 1)
        return out
    if isinstance(value, list):
        # Cap long evidence lists
        limited = value[:50]
        return [sanitize_value(v, key=key, depth=depth + 1) for v in limited]
    if isinstance(value, str):
        return redact_string(value)
    return value


def sanitize_case_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Produce a Git-safe case summary from a raw harness case JSON."""
    raw = deepcopy(payload) if isinstance(payload, dict) else {}
    sanitized = sanitize_value(raw)
    if not isinstance(sanitized, dict):
        sanitized = {"value": sanitized}
    sanitized["_sanitizer"] = {
        "version": "1.0",
        "policy": "hit_rate_v2_redact_fulltext_and_secrets",
    }
    return sanitized


def sanitize_metrics_report(report: dict[str, Any]) -> dict[str, Any]:
    """Sanitize finalize report: keep metrics, strip detail answer text."""
    sanitized = sanitize_value(deepcopy(report))
    out: dict[str, Any] = sanitized if isinstance(sanitized, dict) else {"value": sanitized}
    detail = out.get("detail")
    if isinstance(detail, list):
        slim = []
        for item in detail:
            if not isinstance(item, dict):
                continue
            keep = {
                k: item.get(k)
                for k in (
                    "case_id",
                    "type",
                    "category",
                    "top1_hit",
                    "recall5",
                    "ask_fact_correct",
                    "ask_citation_valid",
                    "e2e_pass",
                    "false_positive",
                    "reason_codes",
                    "defect_severity",
                    "defect_category",
                    "defect_reason",
                    "score",
                    "answer_mode",
                    "metric_contract_version",
                )
                if k in item
            }
            slim.append(keep)
        out["detail"] = slim
    return out
