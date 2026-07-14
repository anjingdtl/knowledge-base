"""Normalize Search/Ask payloads for deterministic contract snapshots."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "snapshots"
UPDATE_ENV = "UPDATE_CONTRACT_SNAPSHOTS"

# Float fields rounded for stable snapshots
_FLOAT_KEYS = frozenset({
    "score", "rrf_score", "rerank_score", "final_score", "vector_score",
    "keyword_score", "distance", "wiki_weight", "raw_weight", "confidence",
})

# Keys stripped from traces / payloads (non-deterministic)
_DROP_KEYS = frozenset({
    "elapsed_ms", "created_at", "updated_at", "timestamp", "request_id",
})


def round_floats(obj: Any, ndigits: int = 4) -> Any:
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _DROP_KEYS:
                continue
            if k in _FLOAT_KEYS and isinstance(v, (int, float)):
                out[k] = round(float(v), ndigits)
            else:
                out[k] = round_floats(v, ndigits)
        return out
    if isinstance(obj, (list, tuple)):
        return [round_floats(x, ndigits) for x in obj]
    return obj


def normalize_search_contract(execution_like: dict[str, Any]) -> dict[str, Any]:
    """Normalize SearchExecution-shaped dict for snapshot compare."""
    data = copy.deepcopy(execution_like)
    data = round_floats(data)
    # Sort result keys for stability; keep list order of results
    if "results" in data:
        data["results"] = [_stable_dict(r) for r in data["results"]]
    if "disclose_claims" in data:
        data["disclose_claims"] = [_stable_dict(r) for r in data["disclose_claims"]]
    if "conflicts" in data:
        data["conflicts"] = [_stable_dict(c) for c in data["conflicts"]]
    if "fallbacks" in data:
        data["fallbacks"] = [_stable_dict(f) for f in data["fallbacks"]]
    if "warnings" in data:
        data["warnings"] = list(data["warnings"])
    if "trace" in data and isinstance(data["trace"], dict):
        data["trace"] = _stable_dict(round_floats(data["trace"]))
    return data


def normalize_ask_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize ask payload; keep answer text (tests use deterministic templates)."""
    keys = [
        "answer", "answer_mode", "sources", "claims_used", "raw_evidence_used",
        "conflicts", "fallbacks", "warnings", "trace_id", "route",
        "conflict_disclosed", "freshness_sensitive", "search_trace",
    ]
    out: dict[str, Any] = {}
    for k in keys:
        if k in payload:
            out[k] = payload[k]
    return round_floats(_stable_dict(out))


def _stable_dict(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _stable_dict(v) for k, v in sorted(obj.items()) if k not in _DROP_KEYS}
    if isinstance(obj, list):
        return [_stable_dict(x) for x in obj]
    if isinstance(obj, tuple):
        return [_stable_dict(x) for x in obj]
    return obj


def execution_to_dict(execution) -> dict[str, Any]:
    return {
        "results": [dict(r) for r in execution.results],
        "trace": dict(execution.trace or {}),
        "disclose_claims": [dict(r) for r in execution.disclose_claims],
        "conflicts": [dict(c) for c in execution.conflicts],
        "fallbacks": [dict(f) for f in execution.fallbacks],
        "warnings": list(execution.warnings),
    }


def assert_matches_snapshot(name: str, actual: dict[str, Any]) -> None:
    """Compare actual to tests/snapshots/<name>.json; update if UPDATE_CONTRACT_SNAPSHOTS=1."""
    path = SNAPSHOT_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if os.environ.get(UPDATE_ENV) == "1" or not path.exists():
        path.write_text(text, encoding="utf-8")
        if not path.exists():
            raise AssertionError(f"failed to write snapshot {path}")
        if os.environ.get(UPDATE_ENV) == "1":
            return
        # First create still asserts file content equals what we wrote
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected, (
        f"Contract snapshot mismatch: {name}\n"
        f"Set {UPDATE_ENV}=1 to refresh.\n"
        f"expected keys sample: {list(expected)[:8]}\n"
        f"actual keys sample: {list(actual)[:8]}"
    )
