"""Score the hit-rate test results strictly from MCP-returned content.

Thin CLI over ``evals.hit_rate_v2.scoring`` (metric_contract_version=2.0).
Core scoring logic lives only in the V2 scoring authority module.

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
    contains_fact as contains,
    extract_ask_payload,
    extract_candidates,
    normalize_text as norm,
    score_answerable_case as score_answerable,
    score_artifact_dir,
    score_case,
    score_no_answer_case as score_no_answer,
)

OUT = Path(os.environ.get("HIT_RATE_ARTIFACTS_DIR", "artifacts/hit_rate_test"))


def load_case(cid: str):
    return json.loads((OUT / f"{cid}.json").read_text(encoding="utf-8"))


def get_search_candidates(d):
    return extract_candidates(d)


def get_ask_answer(d):
    ask = extract_ask_payload(d)
    return ask["answer"], ask["sources"], ask["raw_ev"], ask["snap"]


def main(argv: list[str] | None = None):
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=os.environ.get("HIT_RATE_ARTIFACTS_DIR", "artifacts/hit_rate_test"),
    )
    parser.add_argument("--golden", default="evals/golden_set_hit_rate.json")
    parser.add_argument("--cases", help="Optional comma-separated case IDs")
    args = parser.parse_args(argv)
    OUT = Path(args.out)
    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))["cases"]
    selected = set(args.cases.split(",")) if args.cases else None
    scores, _metrics, _gate = score_artifact_dir(golden, OUT, selected=selected)
    rows = [s.to_dict() for s in scores]
    (OUT / "scored.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"scored {len(rows)} cases -> {OUT / 'scored.json'} (metric_contract_version=2.0)")


if __name__ == "__main__":
    main()
