"""Fast V7 tests for reproducible artifacts and offline rerank A/B."""
from __future__ import annotations

import json

from scripts.hit_rate_artifact_integrity import verify
from scripts.hit_rate_search_ab import compare, deterministic_fallback
from scripts.hit_rate_test_harness import _build_manifest, _manifest_compatible


def test_manifest_is_complete_and_resume_rejects_source_change(tmp_path, monkeypatch):
    golden = tmp_path / "golden.json"
    golden.write_text(json.dumps({"cases": [{"case_id": "T-001"}]}), encoding="utf-8")
    for key in ("HIT_RATE_CONFIG_HASH", "HIT_RATE_INDEX_REVISION", "HIT_RATE_DB_REVISION"):
        monkeypatch.setenv(key, key.lower())
    manifest = _build_manifest(
        golden_path=golden, out_dir=tmp_path, reuse_snapshot=True,
        read_mode="unique", workers=1, case_filter=None,
    )
    assert all(manifest.get(key) not in (None, "", [], {}) for key in (
        "dirty_patch_sha256", "production_source_sha256", "scorer_sha256",
        "config_hash", "index_revision", "db_revision", "process_start_id",
        "python_version", "dependency_lock_sha256",
    ))
    changed = dict(manifest)
    changed["production_source_sha256"] = "changed"
    assert _manifest_compatible(manifest, changed) == (False, "manifest_mismatch:production_source_sha256")


def test_artifact_integrity_requires_one_run_fingerprint(tmp_path):
    golden = tmp_path / "golden.json"
    golden.write_text(json.dumps({"cases": [{"case_id": "T-001"}]}), encoding="utf-8")
    manifest = {
        "git_revision": "g", "dirty_patch_sha256": "d", "production_source_sha256": "p",
        "golden_sha256": "gold", "scorer_sha256": "s", "config_hash": "c",
        "index_revision": "i", "db_revision": "db", "process_start_id": "proc",
        "python_version": "py", "dependency_lock_sha256": "lock", "retrieval_mode": "unified",
        "rerank_mode": "fallback", "timeout_settings": {"rerank": 20}, "run_fingerprint": "run",
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "T-001.json").write_text(json.dumps({
        "case": {"case_id": "T-001"}, "run_fingerprint": "run", "snapshot_integrity_error": False,
    }), encoding="utf-8")
    for name in ("summary.json", "snapshot_reuse_audit.json", "00_capabilities.json", "final_scored.json", "metrics_comparison.txt"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert verify(tmp_path, golden, require_scored=True)["ok"] is True
    (tmp_path / "T-001.json").write_text(json.dumps({"case": {"case_id": "T-001"}, "run_fingerprint": "other"}), encoding="utf-8")
    assert verify(tmp_path, golden, require_scored=True)["ok"] is False


def test_deterministic_fallback_and_compare_detect_regression(tmp_path):
    ranked = deterministic_fallback([
        {"knowledge_id": "b", "score": 0.5}, {"knowledge_id": "a", "score": 0.5},
    ], top_k=5)
    assert [item["knowledge_id"] for item in ranked] == ["b", "a"]
    normal = tmp_path / "normal.json"
    fallback = tmp_path / "fallback.json"
    normal.write_text(json.dumps({"mode": "normal-rerank", "top1": 1.0, "recall5": 1.0, "rows": [{"case_id": "T-001", "top_ids": ["good"], "top1_ok": True, "recall5_ok": True, "rank_ms": 1}]}), encoding="utf-8")
    fallback.write_text(json.dumps({"mode": "deterministic-fallback", "top1": 0.0, "recall5": 0.0, "rows": [{"case_id": "T-001", "top_ids": ["bad"], "top1_ok": False, "recall5_ok": False, "rank_ms": 1}]}), encoding="utf-8")
    result = compare(normal, fallback, tmp_path / "comparison.json")
    assert result["pass"] is False
    assert result["regressions"] == ["T-001"]
