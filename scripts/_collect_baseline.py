"""One-shot baseline collector for production pilot final validation."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    baseline: dict = {}
    baseline["baseline_commit_sha"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    baseline["branch"] = "fix/mcp-production-pilot-final-validation"
    baseline["version"] = "1.10.3"
    baseline["python_version"] = sys.version.split()[0]

    try:
        import fastmcp

        baseline["fastmcp_version"] = getattr(fastmcp, "__version__", "unknown")
    except Exception as e:  # noqa: BLE001
        baseline["fastmcp_version"] = f"unavailable: {e}"

    import yaml

    cfg_path = root / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    db_path = root / "data" / "kb.db"
    baseline["formal_db_path"] = str(db_path)
    baseline["formal_db_exists"] = db_path.exists()
    if db_path.exists():
        st = db_path.stat()
        baseline["formal_db_size"] = st.st_size
        baseline["formal_db_sha256"] = sha256_file(db_path)
        baseline["formal_db_mtime"] = st.st_mtime
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        baseline["db_tables"] = tables

        def count(sql: str):
            try:
                return cur.execute(sql).fetchone()[0]
            except Exception as e:  # noqa: BLE001
                return f"err:{e}"

        table_map = {
            "knowledge": "formal_document_count",
            "documents": "formal_document_count",
            "pages": "formal_document_count",
            "blocks": "formal_block_count",
            "embeddings": "formal_embedding_count",
            "vectors": "formal_vector_index_count",
            "knowledge_blocks": "formal_block_count",
        }
        for table, key in table_map.items():
            if table in tables and key not in baseline:
                baseline[key] = count(f"SELECT COUNT(*) FROM {table}")

        # try extra common counts
        for table in tables:
            if "embed" in table.lower() and "formal_embedding_count" not in baseline:
                baseline["formal_embedding_count"] = count(
                    f"SELECT COUNT(*) FROM {table}"
                )
            if "vector" in table.lower() and "formal_vector_index_count" not in baseline:
                baseline["formal_vector_index_count"] = count(
                    f"SELECT COUNT(*) FROM {table}"
                )
        con.close()
    else:
        baseline["formal_db_size"] = 0
        baseline["formal_db_sha256"] = None

    emb = cfg.get("embedding") or cfg.get("embeddings") or {}
    llm = cfg.get("llm") or {}
    rerank = cfg.get("reranker") or cfg.get("rerank") or {}
    storage = cfg.get("storage") or {}
    vec = cfg.get("vector_store") or cfg.get("vector") or storage

    baseline["embedding_model"] = emb.get("model") or emb.get("model_name")
    baseline["llm_model"] = llm.get("model") or llm.get("model_name")
    baseline["reranker_model"] = rerank.get("model") or rerank.get("model_name")
    baseline["vector_backend"] = (
        vec.get("backend")
        or vec.get("type")
        or storage.get("vector_backend")
        or storage.get("backend")
    )
    baseline["storage_config"] = storage
    baseline["retrieval_config_keys"] = [
        k for k in ("retrieval", "vector", "embedding", "llm", "reranker", "rag") if k in cfg
    ]

    # MCP tool counts via registry if importable
    try:
        from src.mcp.tool_profiles import resolve_profile

        for profile_name in ("core", "extended", "admin", "full"):
            try:
                tools = resolve_profile(profile_name)
                baseline[f"{profile_name}_tool_count"] = (
                    len(tools) if tools is not None else None
                )
            except Exception as e:  # noqa: BLE001
                baseline[f"{profile_name}_tool_count"] = f"err:{e}"
    except Exception as e:  # noqa: BLE001
        baseline["tool_profile_import"] = str(e)

    reports_dir = root / "docs" / "reports"
    if reports_dir.exists():
        baseline["existing_reports"] = sorted(
            str(p.relative_to(root)).replace("\\", "/")
            for p in reports_dir.rglob("*.md")
        )
    else:
        baseline["existing_reports"] = []

    # detect prior decision phrasing
    decision_hits = []
    for rel in baseline["existing_reports"]:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        if "达到生产试点门槛" in text:
            decision_hits.append(rel)
    baseline["reports_claiming_pilot_ready"] = decision_hits
    baseline["current_report_decision"] = (
        "生产试点验收进行中 — 旧结论不作为最终验收依据"
    )
    baseline["stdio_tool_count"] = baseline.get("extended_tool_count")
    baseline["http_tool_count"] = baseline.get("extended_tool_count")
    baseline["note"] = (
        "Historical metric scores are deprecated until Phase 1–2 rebuild "
        "human ground truth and metric denominators."
    )

    out = root / "artifacts" / "production-pilot-final-validation" / "baseline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(baseline, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
