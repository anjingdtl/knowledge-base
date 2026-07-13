"""Verified Hybrid eval — Raw / Wiki / Hybrid ablation (Phase 7).

Offline deterministic golden set (>=150 cases) covering telecom, conflict,
freshness, no-answer, location, and fallback. Optional --json / --markdown.

Usage:
    python evals/run_hybrid_eval.py
    python evals/run_hybrid_eval.py --json
    python evals/run_hybrid_eval.py --markdown --output artifacts/eval/hybrid-report.md
    python evals/run_hybrid_eval.py --strict   # non-zero exit if gates fail
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.hybrid_eval.cases import build_hybrid_cases, category_counts  # noqa: E402
from evals.hybrid_eval.scoring import score_case, summarize_report  # noqa: E402


def run() -> dict:
    t0 = time.perf_counter()
    cases = build_hybrid_cases()
    scores = [score_case(c) for c in cases]
    report = summarize_report(scores)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    payload = report.to_dict()
    payload["elapsed_ms"] = round(elapsed_ms, 2)
    payload["category_counts"] = category_counts(cases)
    payload["telecom_cases"] = sum(1 for c in cases if c.get("telecom"))
    return payload


def to_markdown(report: dict) -> str:
    lines = [
        "# Verified Hybrid Eval Report",
        "",
        f"- Total cases: **{report['total']}**",
        f"- Telecom cases: **{report.get('telecom_cases', 0)}**",
        f"- Elapsed: **{report.get('elapsed_ms', 0)} ms**",
        f"- Overall: **{'PASS' if report['overall_pass'] else 'FAIL'}**",
        "",
        "## Core metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Raw correct | {report['raw_correct']:.4f} |",
        f"| Wiki correct | {report['wiki_correct']:.4f} |",
        f"| Hybrid correct | {report['hybrid_correct']:.4f} |",
        f"| Hybrid ≥ Raw | {report['hybrid_ge_raw']} |",
        f"| Citation correctness | {report['citation_correctness']:.4f} |",
        f"| Stale serving rate | {report['stale_serving_rate']:.4f} |",
        f"| Unsupported serving rate | {report['unsupported_serving_rate']:.4f} |",
        f"| Conflict detection recall | {report['conflict_detection_recall']:.4f} |",
        f"| Raw fallback success | {report['raw_fallback_success']:.4f} |",
        f"| Evidence resolvability | {report['evidence_resolvability']:.4f} |",
        "",
        "## Gates",
        "",
    ]
    for k, v in (report.get("gates") or {}).items():
        lines.append(f"- `{'PASS' if v else 'FAIL'}` {k}")
    lines += ["", "## By category", ""]
    lines.append("| Category | N | Raw | Wiki | Hybrid |")
    lines.append("|---|---:|---:|---:|---:|")
    for cat, m in sorted((report.get("by_category") or {}).items()):
        lines.append(
            f"| {cat} | {int(m['count'])} | {m['raw_correct']:.3f} | "
            f"{m['wiki_correct']:.3f} | {m['hybrid_correct']:.3f} |"
        )
    fails = report.get("failures") or []
    lines += ["", f"## Failures (showing {len(fails)})", ""]
    if not fails:
        lines.append("_None_")
    else:
        for f in fails[:30]:
            lines.append(
                f"- `{f['case_id']}` ({f['category']}): {f.get('class')} "
                f"{','.join(f.get('details') or [])}"
            )
    lines += [
        "",
        "## Notes",
        "",
        "- Offline deterministic eval (no embedding / LLM).",
        "- Real-model optional eval can wrap the same cases later.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verified Hybrid Raw/Wiki/Hybrid eval")
    parser.add_argument("--json", action="store_true", help="print JSON report")
    parser.add_argument("--markdown", action="store_true", help="print markdown report")
    parser.add_argument("--output", type=str, default="", help="write report to path")
    parser.add_argument("--strict", action="store_true", help="exit 1 if gates fail")
    args = parser.parse_args(argv)

    report = run()
    if args.markdown:
        text = to_markdown(report)
    elif args.json:
        text = json.dumps(report, ensure_ascii=False, indent=2)
    else:
        status = "PASS" if report["overall_pass"] else "FAIL"
        text = (
            f"Hybrid Eval {status}: cases={report['total']} "
            f"raw={report['raw_correct']:.3f} wiki={report['wiki_correct']:.3f} "
            f"hybrid={report['hybrid_correct']:.3f} "
            f"stale={report['stale_serving_rate']} unsup={report['unsupported_serving_rate']} "
            f"cite={report['citation_correctness']:.3f} "
            f"conflict_recall={report['conflict_detection_recall']:.3f}"
        )

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        if args.markdown or out.suffix in (".md", ".markdown"):
            out.write_text(to_markdown(report), encoding="utf-8")
        else:
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
    else:
        print(text)

    if args.strict and not report["overall_pass"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
