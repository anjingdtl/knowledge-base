"""Compare hit-rate metrics across baseline / round-1 / round-2 artifacts.

Usage:
    python scripts/hit_rate_compare.py \
        --baseline artifacts/hit_rate_test \
        --round1 artifacts/hit_rate_test_after_fix \
        --round2 artifacts/hit_rate_test_v2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

METRIC_KEYS = [
    ("Top-1 Accuracy", True),
    ("Recall@5", True),
    ("Ask Fact Correctness", True),
    ("Answer Groundedness", True),
    ("Ask Citation Validity", True),
    ("Citation Validity", True),
    ("E2E Pass Rate", True),
    ("Hallucination Rate", False),
    ("False Positive Rate", False),
]

GOALS = {
    "Top-1 Accuracy": 0.75,
    "Recall@5": 0.88,
    "Ask Fact Correctness": 0.90,
    "Answer Groundedness": 0.90,
    "Ask Citation Validity": 0.95,
    "Citation Validity": 0.95,
    "E2E Pass Rate": 0.90,
    "Hallucination Rate": 0.05,
    "False Positive Rate": 0.05,
}


def _load(d: Path) -> dict:
    return json.loads((d / "final_scored.json").read_text(encoding="utf-8"))


def _pct(x) -> str:
    if x is None:
        return "   n/a"
    return f"{float(x) * 100:.2f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="artifacts/hit_rate_test")
    ap.add_argument("--round1", default="artifacts/hit_rate_test_after_fix")
    ap.add_argument("--round2", default="artifacts/hit_rate_test_v2")
    ap.add_argument("--out", default=None, help="optional path to write text report")
    args = ap.parse_args()

    rounds = []
    for label, path in [
        ("Baseline", args.baseline),
        ("Round1", args.round1),
        ("Round2", args.round2),
    ]:
        p = Path(path)
        if (p / "final_scored.json").exists():
            rounds.append((label, path, _load(p)))
        else:
            rounds.append((label, path, None))

    lines: list[str] = []
    lines.append("=" * 88)
    lines.append("METRIC COMPARISON (baseline / round-1 after-fix / round-2 v2)")
    lines.append("=" * 88)
    header = f"{'Metric':<26}"
    for label, _, _ in rounds:
        header += f"{label:>12}"
    header += f"{'MinGate':>10}{'R2Pass':>8}"
    lines.append(header)

    r2_metrics = rounds[-1][2]["metrics"] if rounds[-1][2] else {}
    all_pass = True
    for key, higher in METRIC_KEYS:
        row = f"{key:<26}"
        for _, _, data in rounds:
            if data is None:
                row += f"{'n/a':>12}"
            else:
                row += f"{_pct(data['metrics'].get(key)):>12}"
        thr = GOALS.get(key)
        row += f"{_pct(thr):>10}"
        val = r2_metrics.get(key)
        if val is None or thr is None:
            mark = " n/a"
        else:
            ok = (val >= thr) if higher else (val <= thr)
            mark = "  ✓" if ok else "  ✗"
            if rounds[-1][2] is not None and not ok:
                all_pass = False
        row += f"{mark:>8}"
        lines.append(row)

    lines.append("")
    lines.append("RELEASE VERDICTS")
    for label, path, data in rounds:
        if data is None:
            lines.append(f"  {label} ({path}): missing final_scored.json")
            continue
        verdict = data.get("release_verdict") or "(legacy, no verdict field)"
        lines.append(f"  {label}: {verdict}")

    lines.append("")
    lines.append("DEFECT COUNTS")
    for label, _, data in rounds:
        if data is None:
            continue
        d = data.get("defects", {})
        lines.append(
            f"  {label}: P0={len(d.get('P0', []))} P1={len(d.get('P1', []))} "
            f"P2={len(d.get('P2', []))} P3={len(d.get('P3', []))}"
        )
        for sev in ("P0", "P1", "P2"):
            items = d.get(sev) or []
            if items:
                lines.append(f"    {sev}: {', '.join(items)}")

    # Per-case hard-acceptance cases from SPEC v2 §9.1
    hard = ["KB-007", "KB-009", "KB-017", "KB-019", "KB-021", "KB-023", "KB-037"]
    lines.append("")
    lines.append("HARD ACCEPTANCE CASES (round2 detail if present)")
    if rounds[-1][2]:
        detail = {x["case_id"]: x for x in rounds[-1][2].get("detail", [])}
        for cid in hard:
            row = detail.get(cid, {})
            lines.append(
                f"  {cid}: top1={row.get('top1_hit')} recall5={row.get('recall5')} "
                f"ask_fact={row.get('ask_fact_correct', row.get('facts_correct'))} "
                f"ask_cite={row.get('ask_citation_valid', row.get('citation_valid'))} "
                f"e2e={row.get('e2e_pass')} sev={row.get('defect_severity')}"
            )

    lines.append("")
    lines.append(
        f"ROUND-2 OVERALL GATE: {'PASS' if all_pass and rounds[-1][2] else 'FAIL'} "
        f"/ verdict={rounds[-1][2].get('release_verdict') if rounds[-1][2] else 'n/a'}"
    )

    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    elif rounds[-1][2] is not None:
        out_path = Path(args.round2) / "metrics_comparison.txt"
        out_path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
