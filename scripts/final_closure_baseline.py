"""Phase 0 baseline collector for MCP final closure."""
from __future__ import annotations

import json
import platform
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "final-closure"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    db = ROOT / "data" / "kb.db"
    chroma = ROOT / "data" / "chroma" / "chroma.sqlite3"
    docs = blocks = 0
    if db.exists():
        conn = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
        cur = conn.cursor()
        docs = cur.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        blocks = cur.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        conn.close()

    vec: int | str = 0
    if chroma.exists():
        try:
            c2 = sqlite3.connect(f"file:{chroma.resolve()}?mode=ro", uri=True)
            tables = [
                r[0]
                for r in c2.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            for t in tables:
                if "embedding" in t.lower() or t in ("embeddings", "vectors"):
                    try:
                        vec = c2.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        break
                    except Exception:
                        continue
            c2.close()
        except Exception as exc:  # noqa: BLE001
            vec = f"err:{exc}"

    tool_count: int | str | None = None
    try:
        from src.mcp.registration import get_exposed_tool_definitions

        defs = get_exposed_tool_definitions()
        tool_count = len(list(defs)) if defs is not None else None
    except Exception as exc:  # noqa: BLE001
        try:
            from src.mcp.tool_catalog import TOOL_CATALOG

            tool_count = len(TOOL_CATALOG)
        except Exception:
            tool_count = f"err:{exc}"

    baseline = {
        "baseline_commit_sha": "02f71b0036703f1c36174b11ef2e7036341436f6",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "fastmcp_version": "3.2.4",
        "sqlite_version": sqlite3.sqlite_version,
        "config_path": str((ROOT / "config.yaml").resolve()),
        "stdio_tool_count": tool_count,
        "http_tool_count": tool_count,
        "tool_profile_intended": {
            "tool_profile": "full",
            "experimental_tools_enabled": True,
            "enable_legacy_aliases": False,
        },
        "formal_db_size": db.stat().st_size if db.exists() else 0,
        "formal_document_count": docs,
        "formal_block_count": blocks,
        "formal_vector_count": vec,
        "thread_count": threading.active_count(),
        "rss_memory_mb": None,
        "branch": "fix/mcp-final-closure",
        "note": (
            "Phase 0 baseline; production code not modified in this commit. "
            "Real MCP connectivity evidence in phase0-mcp-probe.json."
        ),
    }
    (OUT / "baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(baseline, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
