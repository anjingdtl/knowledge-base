"""CLI: sanitize raw hit-rate run artifacts into Git-safe summaries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.hit_rate_v2.sanitize import (  # noqa: E402
    sanitize_case_result,
    sanitize_metrics_report,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument(
        "--mode",
        choices=["case", "report", "auto"],
        default="auto",
    )
    args = ap.parse_args(argv)

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    mode = args.mode
    if mode == "auto":
        if isinstance(payload, dict) and (
            "metrics" in payload or "detail" in payload or "gates" in payload
        ):
            mode = "report"
        else:
            mode = "case"

    if mode == "report":
        out = sanitize_metrics_report(payload)
    else:
        out = sanitize_case_result(payload)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote sanitized {mode} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
