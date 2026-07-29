"""Verify a hit-rate run is complete and tied to one reproducible fingerprint."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_MANIFEST_FIELDS = (
    "git_revision", "dirty_patch_sha256", "production_source_sha256",
    "golden_sha256", "scorer_sha256", "config_hash", "index_revision",
    "db_revision", "process_start_id", "python_version",
    "dependency_lock_sha256", "retrieval_mode", "rerank_mode",
    "timeout_settings",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(out_dir: Path, golden_path: Path, *, require_scored: bool) -> dict[str, Any]:
    golden = _load(golden_path)
    cases = golden.get("cases", golden)
    expected_case_ids = [str(case["case_id"]) for case in cases]
    manifest_path = out_dir / "manifest.json"
    errors: list[str] = []
    manifest: dict[str, Any] = {}
    if not manifest_path.exists():
        errors.append("manifest_missing")
    else:
        manifest = _load(manifest_path)
        for field in REQUIRED_MANIFEST_FIELDS:
            if manifest.get(field) in (None, "", [], {}):
                errors.append(f"manifest_field_empty:{field}")
    case_files: list[str] = []
    fingerprint_mismatches: list[str] = []
    for case_id in expected_case_ids:
        path = out_dir / f"{case_id}.json"
        if not path.exists():
            errors.append(f"case_missing:{case_id}")
            continue
        case_files.append(case_id)
        try:
            row = _load(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"case_invalid_json:{case_id}:{exc}")
            continue
        if str((row.get("case") or {}).get("case_id") or "") != case_id:
            errors.append(f"case_id_mismatch:{case_id}")
        if manifest.get("run_fingerprint") and row.get("run_fingerprint") != manifest.get("run_fingerprint"):
            fingerprint_mismatches.append(case_id)
            errors.append(f"run_fingerprint_mismatch:{case_id}")
        if row.get("snapshot_integrity_error"):
            errors.append(f"snapshot_integrity_error:{case_id}")
    for name in ("summary.json", "snapshot_reuse_audit.json", "00_capabilities.json"):
        if not (out_dir / name).exists():
            errors.append(f"required_artifact_missing:{name}")
    if require_scored:
        for name in ("final_scored.json", "metrics_comparison.txt"):
            if not (out_dir / name).exists():
                errors.append(f"scored_artifact_missing:{name}")
    result = {
        "ok": not errors,
        "out_dir": str(out_dir),
        "golden": str(golden_path),
        "expected_case_count": len(expected_case_ids),
        "present_case_count": len(case_files),
        "manifest_fields_checked": list(REQUIRED_MANIFEST_FIELDS),
        "fingerprint_mismatches": fingerprint_mismatches,
        "errors": errors,
    }
    (out_dir / "artifacts_integrity.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--golden", default="evals/golden_set_hit_rate.json")
    parser.add_argument("--require-scored", action="store_true")
    args = parser.parse_args()
    result = verify(Path(args.out), Path(args.golden), require_scored=args.require_scored)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
