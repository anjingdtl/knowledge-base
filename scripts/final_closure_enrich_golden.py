"""Enrich stability_round2_queries.jsonl with Spec-required fields."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tests" / "eval" / "datasets" / "stability_round2_queries.jsonl"


def enrich(row: dict) -> dict:
    expect = row.get("expect") or ""
    qtype = row.get("type") or ""
    out = dict(row)
    out.setdefault("category", qtype)
    out.setdefault("expected_ids", [])
    out.setdefault("expected_units", [])
    out.setdefault("notes", "")

    no_answer = expect == "no_match" or qtype == "no_answer"
    out["expected_no_answer"] = bool(no_answer)

    if "meters" in expect:
        out["expected_units"] = ["米"]
    elif "beads" in expect:
        out["expected_units"] = ["珠/米"]
    elif "probation" in expect:
        out["expected_units"] = ["个月"]
    elif "wecom" in expect:
        out["expected_units"] = ["个月"]

    if not out["notes"]:
        out["notes"] = f"type={qtype}; expect={expect}"
    return out


def main() -> None:
    lines = SRC.read_text(encoding="utf-8").strip().splitlines()
    rows = [enrich(json.loads(L)) for L in lines if L.strip()]
    SRC.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    print(f"enriched {len(rows)} rows -> {SRC}")


if __name__ == "__main__":
    main()
