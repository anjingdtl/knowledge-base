"""Finalize scoring: produce per-case verdicts + metrics for the remediation report.

Thin aggregation/report CLI over ``evals.hit_rate_v2.scoring``
(metric_contract_version=2.0). Core scoring is not duplicated here.

可通过环境变量 ``HIT_RATE_ARTIFACTS_DIR`` 指定 artifacts 目录（默认
``artifacts/hit_rate_test`` 为基线，复测应指向独立目录以免覆盖基线）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.hit_rate_v2.scoring import (  # noqa: E402, F401, I001
    classify_defect,
    score_answerable_case as score_answerable,
    score_artifact_dir,
    score_case,
    score_no_answer_case as score_no_answer,
)

OUT = Path(os.environ.get("HIT_RATE_ARTIFACTS_DIR", "artifacts/hit_rate_test"))


def load(cid: str):
    return json.loads((OUT / f"{cid}.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None):
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=os.environ.get("HIT_RATE_ARTIFACTS_DIR", "artifacts/hit_rate_test"),
    )
    parser.add_argument("--golden", default="evals/golden_set_hit_rate.json")
    parser.add_argument("--cases", help="Optional comma-separated case IDs")
    parser.add_argument(
        "--write-name",
        default="final_scored.json",
        help="Output filename inside --out (default final_scored.json)",
    )
    args = parser.parse_args(argv)
    OUT = Path(args.out)
    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))["cases"]
    selected = set(args.cases.split(",")) if args.cases else None

    scores, metrics, gate = score_artifact_dir(golden, OUT, selected=selected)

    defects: dict[str, list[str]] = {"P0": [], "P1": [], "P2": [], "P3": []}
    details = []
    for sc in scores:
        d = sc.to_dict()
        details.append(d)
        sev = sc.defect_severity
        if sev:
            defects.setdefault(sev, []).append(sc.case_id)

    report_metrics = metrics.to_report_dict()
    # Compatibility: some dashboards still read Hallucination Rate as the
    # forbidden-substring proxy. V2 report keeps both:
    # - Hallucination Rate = null (not fully measurable)
    # - Forbidden Assertion Rate = proxy
    # For compare scripts that only know the old key, also emit the proxy under
    # a clearly named legacy field already included in to_report_dict.

    out = {
        "metrics": report_metrics,
        "gates": gate["gates"],
        "release_verdict": gate["release_verdict"],
        "defects": defects,
        "detail": details,
        "metric_contract_version": report_metrics["metric_contract_version"],
    }
    target = OUT / args.write_name
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report_metrics, ensure_ascii=False, indent=2))
    print("verdict:", gate["release_verdict"])
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
