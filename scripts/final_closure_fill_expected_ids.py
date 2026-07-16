"""Fill expected_ids for golden rows using formal DB FTS (readonly)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "eval" / "datasets" / "stability_round2_queries.jsonl"
DB = ROOT / "data" / "kb.db"


def fts_ids(conn: sqlite3.Connection, query: str, limit: int = 5) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    # simple LIKE fallback for robustness
    rows = conn.execute(
        "SELECT id FROM knowledge_items WHERE deleted_at IS NULL AND "
        "(title LIKE ? OR content LIKE ?) LIMIT ?",
        (f"%{q}%", f"%{q}%", limit),
    ).fetchall()
    return [r[0] for r in rows]


def main() -> None:
    conn = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    lines = GOLDEN.read_text(encoding="utf-8").strip().splitlines()
    out = []
    for L in lines:
        row = json.loads(L)
        if row.get("expected_no_answer"):
            row["expected_ids"] = []
        elif not row.get("expected_ids"):
            if row.get("expect") in (
                "hit",
                "hit_meters_not_beads",
                "hit_beads",
                "hit_wecom",
                "hit_probation",
                "context_sensitive",
            ) or row.get("type") in ("keyword", "numeric_unit", "distractor"):
                row["expected_ids"] = fts_ids(conn, row["query"], 5)
        out.append(row)
    GOLDEN.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n",
        encoding="utf-8",
    )
    filled = sum(1 for r in out if r.get("expected_ids"))
    print(f"filled expected_ids for {filled}/{len(out)} rows")
    conn.close()


if __name__ == "__main__":
    main()
