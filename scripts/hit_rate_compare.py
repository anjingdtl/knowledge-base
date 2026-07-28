"""Compare hit-rate metrics across baseline / round-1 / round-2 / round-3 artifacts.

Usage:
    python scripts/hit_rate_compare.py \
        --baseline artifacts/hit_rate_test \
        --round1 artifacts/hit_rate_test_after_fix \
        --round2 artifacts/hit_rate_test_v2 \
        --round3 artifacts/hit_rate_test_v3
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
    ap.add_argument("--round3", default="artifacts/hit_rate_test_v3")
    ap.add_argument("--out", default=None, help="optional path to write text report")
    args = ap.parse_args()

    rounds = []
    for label, path in [
        ("Baseline", args.baseline),
        ("Round1", args.round1),
        ("Round2", args.round2),
        ("Round3", args.round3),
    ]:
        p = Path(path)
        if (p / "final_scored.json").exists():
            rounds.append((label, path, _load(p)))
        else:
            rounds.append((label, path, None))

    lines: list[str] = []
    lines.append("=" * 100)
    lines.append("METRIC COMPARISON (baseline / round-1 / round-2 v2 / round-3 v3)")
    lines.append("=" * 100)
    header = f"{'Metric':<26}"
    for label, _, _ in rounds:
        header += f"{label:>12}"
    header += f"{'MinGate':>10}{'R3Pass':>8}"
    lines.append(header)

    last = next((r for r in reversed(rounds) if r[2] is not None), None)
    last_metrics = last[2]["metrics"] if last and last[2] else {}

    for key, higher in METRIC_KEYS:
        row = f"{key:<26}"
        for _, _, data in rounds:
            if data is None:
                row += f"{'n/a':>12}"
            else:
                m = data.get("metrics") or {}
                row += f"{_pct(m.get(key)):>12}"
        thr = GOALS.get(key)
        row += f"{_pct(thr):>10}"
        val = last_metrics.get(key)
        if val is None or thr is None:
            ok = "   n/a"
        else:
            ok = "       ✓" if ((val >= thr) if higher else (val <= thr)) else "       ✗"
        row += ok
        lines.append(row)

    lines.append("")
    lines.append("RELEASE VERDICTS")
    for label, path, data in rounds:
        if data is None:
            lines.append(f"  {label}: (missing {path})")
            continue
        verdict = data.get("verdict") or data.get("release_verdict") or "(no verdict field)"
        lines.append(f"  {label}: {verdict}")

    lines.append("")
    lines.append("DEFECT COUNTS")
    for label, path, data in rounds:
        if data is None:
            continue
        defects = data.get("defects") or {}
        p0 = defects.get("P0") or []
        p1 = defects.get("P1") or []
        p2 = defects.get("P2") or []
        p3 = defects.get("P3") or []
        lines.append(
            f"  {label}: P0={len(p0)} P1={len(p1)} P2={len(p2)} P3={len(p3)}"
        )
        if p1:
            lines.append(f"    P1: {', '.join(p1)}")
        if p2:
            lines.append(f"    P2: {', '.join(p2)}")

    # Passage health if present on round3
    if last and last[2]:
        ph = (last[2].get("passage_health")
              or (last[2].get("diagnostics") or {}).get("passage_index"))
        if ph:
            lines.append("")
            lines.append("PASSAGE INDEX HEALTH (round3)")
            for k in (
                "retrieval_index_unit", "passages", "embedded", "fts",
                "vector_coverage", "fts_coverage", "avg_char_count",
                "p50_char_count", "p95_char_count", "short_passage_count",
                "length_gate_ok",
            ):
                if k in ph:
                    lines.append(f"  {k}: {ph[k]}")

    lines.append("")
    if last and last[2]:
        gates = last[2].get("gates") or last[2].get("gate_results") or {}
        all_pass = last[2].get("all_gates_pass")
        if all_pass is None:
            # Derive from metrics
            all_pass = True
            for key, higher in METRIC_KEYS:
                if key not in GOALS:
                    continue
                val = last_metrics.get(key)
                thr = GOALS[key]
                if val is None:
                    continue
                ok = (val >= thr) if higher else (val <= thr)
                if not ok:
                    all_pass = False
        verdict = "通过放行" if all_pass else "不通过放行"
        lines.append(f"ROUND-3 OVERALL GATE: {'PASS' if all_pass else 'FAIL'} / verdict={verdict}")
    else:
        lines.append("ROUND-3 OVERALL GATE: n/a")

    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
