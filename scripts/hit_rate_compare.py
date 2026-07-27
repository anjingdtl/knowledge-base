"""Compare hit-rate metrics between baseline and after-fix artifacts.

Reads ``final_scored.json`` from two artifact directories and prints a
side-by-side metric table plus the per-case delta. Used to populate the
after-fix remediation report.

Usage:
    python scripts/hit_rate_compare.py \
        --baseline artifacts/hit_rate_test \
        --after artifacts/hit_rate_test_after_fix
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

METRIC_KEYS = [
    ("Top-1 Accuracy", "Top-1 Accuracy", True),
    ("Recall@5", "Recall@5", True),
    ("Answer Groundedness", "Answer Groundedness", True),
    ("Citation Validity", "Citation Validity", True),
    ("Hallucination Rate", "Hallucination Rate", False),  # lower is better
    ("False Positive Rate", "False Positive Rate", False),
]


def _load(d: Path) -> dict:
    return json.loads((d / "final_scored.json").read_text(encoding="utf-8"))


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="artifacts/hit_rate_test")
    ap.add_argument("--after", default="artifacts/hit_rate_test_after_fix")
    args = ap.parse_args()

    base = _load(Path(args.baseline))
    after = _load(Path(args.after))
    bm = base["metrics"]
    am = after["metrics"]

    print("=" * 70)
    print("METRIC COMPARISON (baseline -> after-fix)")
    print("=" * 70)
    print(f"{'Metric':<24}{'Baseline':>12}{'After':>12}{'Delta':>12}{'Goal':>10}")
    goals = {
        "Top-1 Accuracy": 0.85,
        "Recall@5": 0.95,
        "Answer Groundedness": 0.96,
        "Citation Validity": 0.98,
        "Hallucination Rate": 0.02,
        "False Positive Rate": 0.05,
    }
    for key, _, higher_better in METRIC_KEYS:
        b = bm.get(key, 0.0)
        a = am.get(key, 0.0)
        delta = a - b
        sign = "+" if delta >= 0 else ""
        good = (delta > 0) if higher_better else (delta < 0)
        mark = "✓" if (a == goals[key] or (higher_better and a >= goals[key]) or (not higher_better and a <= goals[key])) else " "
        flag = "↑" if good else ("↓" if delta != 0 else "=")
        print(
            f"{key:<24}{_pct(b):>12}{_pct(a):>12}"
            f"{sign}{_pct(delta):>11}{mark}{_pct(goals[key]):>9}"
        )

    # Defect delta
    print()
    print("DEFECT DELTA")
    bd = base.get("defects", {})
    ad = after.get("defects", {})
    for sev in ("P0", "P1", "P2", "P3"):
        bset = set(bd.get(sev, []))
        aset = set(ad.get(sev, []))
        resolved = sorted(bset - aset)
        new = sorted(aset - bset)
        print(f"  {sev}: baseline={len(bset)} after={len(aset)}")
        if resolved:
            print(f"    resolved: {', '.join(resolved)}")
        if new:
            print(f"    NEW regressions: {', '.join(new)}")

    # Per-case detail for previously-failing cases
    print()
    print("PER-CASE DETAIL (previously P1)")
    base_detail = {d["case_id"]: d for d in base.get("detail", [])}
    after_detail = {d["case_id"]: d for d in after.get("detail", [])}
    p1_cases = sorted(set(bd.get("P1", [])))
    for cid in p1_cases:
        b = base_detail.get(cid, {})
        a = after_detail.get(cid, {})
        b_top1 = b.get("top1_hit")
        a_top1 = a.get("top1_hit")
        b_grounded = b.get("grounded")
        a_grounded = a.get("grounded")
        b_hall = b.get("no_hallucination")
        a_hall = a.get("no_hallucination")
        print(
            f"  {cid}: top1 {b_top1}->{a_top1}  grounded {b_grounded}->{a_grounded}  "
            f"no_halluc {b_hall}->{a_hall}  severity {b.get('defect_severity')}->{a.get('defect_severity') or 'OK'}"
        )


if __name__ == "__main__":
    main()
