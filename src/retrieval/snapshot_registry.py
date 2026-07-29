"""In-process short-TTL evidence snapshot registry (SPEC v5 §4.1)."""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from typing import Any

# Process start id — changes on restart so old tokens become invalid.
_PROCESS_START_ID = f"{os.getpid()}-{secrets.token_hex(4)}"
_DEFAULT_TTL_S = 600.0  # 10 minutes
_MAX_ENTRIES = 128

_lock = threading.RLock()
_STORE: dict[str, dict[str, Any]] = {}


def process_start_id() -> str:
    return _PROCESS_START_ID


def _now() -> float:
    return time.time()


def _fingerprint(
    *,
    query: str,
    top_k: int,
    config_hash: str,
    index_revision: str,
    db_revision: str,
) -> str:
    material = "|".join([
        (query or "").strip(),
        str(int(top_k)),
        config_hash or "",
        index_revision or "",
        db_revision or "",
        _PROCESS_START_ID,
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def compute_config_hash(config_bits: dict[str, Any] | None = None) -> str:
    raw = repr(sorted((config_bits or {}).items())).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _purge_locked(now: float | None = None) -> None:
    ts = now if now is not None else _now()
    expired = [k for k, v in _STORE.items() if float(v.get("expires_at") or 0) <= ts]
    for k in expired:
        _STORE.pop(k, None)
    # Capacity: drop oldest
    if len(_STORE) > _MAX_ENTRIES:
        ordered = sorted(_STORE.items(), key=lambda kv: float(kv[1].get("created_at") or 0))
        for k, _ in ordered[: max(0, len(_STORE) - _MAX_ENTRIES)]:
            _STORE.pop(k, None)


def put_snapshot(
    snapshot: dict[str, Any],
    *,
    query: str,
    top_k: int = 5,
    config_hash: str = "",
    index_revision: str = "",
    db_revision: str = "",
    ttl_s: float = _DEFAULT_TTL_S,
) -> str:
    """Store an immutable snapshot; return unguessable evidence_snapshot_id."""
    fp = _fingerprint(
        query=query,
        top_k=top_k,
        config_hash=config_hash,
        index_revision=index_revision,
        db_revision=db_revision,
    )
    sid = secrets.token_urlsafe(24)
    now = _now()
    with _lock:
        _purge_locked(now)
        _STORE[sid] = {
            "snapshot": dict(snapshot),
            "query": (query or "").strip(),
            "top_k": int(top_k),
            "config_hash": config_hash or "",
            "index_revision": index_revision or "",
            "db_revision": db_revision or "",
            "fingerprint": fp,
            "process_start_id": _PROCESS_START_ID,
            "created_at": now,
            "expires_at": now + float(ttl_s),
        }
    return sid


def get_snapshot(
    snapshot_id: str,
    *,
    query: str,
    top_k: int = 5,
    config_hash: str = "",
    index_revision: str = "",
    db_revision: str = "",
) -> tuple[dict[str, Any] | None, str, bool]:
    """Return (snapshot|None, reason, reused).

    reason is empty on success; otherwise a machine-readable miss reason.
    """
    if not snapshot_id:
        return None, "snapshot_id_missing", False
    now = _now()
    with _lock:
        _purge_locked(now)
        entry = _STORE.get(snapshot_id)
        if entry is None:
            return None, "snapshot_not_found_or_expired", False
        if float(entry.get("expires_at") or 0) <= now:
            _STORE.pop(snapshot_id, None)
            return None, "snapshot_expired", False
        if entry.get("process_start_id") != _PROCESS_START_ID:
            return None, "process_restarted", False
        if (entry.get("query") or "").strip() != (query or "").strip():
            return None, "query_mismatch", False
        if int(entry.get("top_k") or 0) != int(top_k):
            return None, "top_k_mismatch", False
        if (config_hash or "") and (entry.get("config_hash") or "") != config_hash:
            return None, "config_changed", False
        if (index_revision or "") and (entry.get("index_revision") or "") != index_revision:
            return None, "index_changed", False
        if (db_revision or "") and (entry.get("db_revision") or "") != db_revision:
            return None, "db_changed", False
        expected = _fingerprint(
            query=query,
            top_k=top_k,
            config_hash=entry.get("config_hash") or config_hash or "",
            index_revision=entry.get("index_revision") or index_revision or "",
            db_revision=entry.get("db_revision") or db_revision or "",
        )
        if entry.get("fingerprint") != expected:
            return None, "fingerprint_mismatch", False
        return dict(entry["snapshot"]), "", True


def clear_registry() -> None:
    """Test helper."""
    with _lock:
        _STORE.clear()


def registry_stats() -> dict[str, Any]:
    with _lock:
        _purge_locked()
        return {
            "size": len(_STORE),
            "process_start_id": _PROCESS_START_ID,
            "max_entries": _MAX_ENTRIES,
            "ttl_s": _DEFAULT_TTL_S,
        }
