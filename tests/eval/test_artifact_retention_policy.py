"""Git-committable eval artifacts must be sanitizer outputs only (Task 2.0.6)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Directories that may be committed and must not hold raw scored detail.
COMMITTABLE_EVAL_DIRS = [
    ROOT / "artifacts" / "eval-summaries",
]

# Filenames that look like raw (unsanitized) scored detail dumps.
_RAW_DETAIL_NAMES = {
    "final_scored_v2.json",
    "final_scored.json",
    "case_results.json",
    "case_results.jsonl",
    "raw_results.json",
    "raw_results.jsonl",
}


def _is_sanitizer_marked(path: Path) -> bool:
    name = path.name.lower()
    return (
        "_sanitizer" in name
        or ".sanitized." in name
        or name.endswith(".sanitized.json")
        or name.endswith(".sanitized.jsonl")
        or name.endswith("_sanitized.json")
        or name.endswith("_sanitized.jsonl")
    )


def test_committable_eval_summaries_have_no_raw_detail_dumps():
    """Fail when a committable dir holds unsanitized scored detail files.

    Sanitizer outputs are summaries (minimized/redacted), not full anonymization.
    Raw ``final_scored_v2.json`` must live under Git-ignored paths
    (``.local/eval-runs/`` or ``artifacts/eval-runs-raw/``).
    """
    offenders: list[str] = []
    for base in COMMITTABLE_EVAL_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".json", ".jsonl"}:
                continue
            if _is_sanitizer_marked(path):
                continue
            # Metrics-only summaries (no "scored"/"case_result" detail) are OK
            # only when not matching known raw detail names.
            if path.name in _RAW_DETAIL_NAMES:
                offenders.append(str(path.relative_to(ROOT)))
            elif "final_scored" in path.name.lower() and "sanitiz" not in path.name.lower():
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], (
        "Committable eval dirs must not contain unsanitized scored detail; "
        "move to .local/eval-runs/ or rename with .sanitized / _sanitizer mark:\n"
        + "\n".join(offenders)
    )
