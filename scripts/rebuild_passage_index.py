"""Rebuild retrieval_passages index for the runtime knowledge DB (SPEC v3).

Usage:
    python scripts/rebuild_passage_index.py
    python scripts/rebuild_passage_index.py --no-embed
    python scripts/rebuild_passage_index.py --out artifacts/hit_rate_test_v3/passage_rebuild.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild retrieval passage index")
    parser.add_argument("--no-embed", action="store_true", help="Skip embedding write")
    parser.add_argument(
        "--embed-only",
        action="store_true",
        help="Skip text rebuild; only embed missing passage vectors (resume)",
    )
    parser.add_argument("--batch-size", type=int, default=16, help="Embedding batch size")
    parser.add_argument("--timeout", type=float, default=120.0, help="Embedding timeout seconds")
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON path for rebuild report",
    )
    args = parser.parse_args()

    # Ensure runtime DB/config.
    from src.utils.config import Config
    from src.services.db import Database
    from src.services.indexer import rebuild_passage_index

    # Raise embedding timeout before any EmbeddingService is constructed.
    try:
        Config.load()
        if hasattr(Config, "set"):
            Config.set("embedding.timeout", args.timeout)
        else:
            data = getattr(Config, "_data", None) or getattr(Config, "_config", None)
            if isinstance(data, dict):
                data.setdefault("embedding", {})["timeout"] = args.timeout
        print(f"embedding.timeout -> {Config.get('embedding.timeout')}")
    except Exception as e:
        print(f"timeout override note: {e}")

    # Open production runtime db if present.
    data_dir = Config.get_data_dir() if hasattr(Config, "get_data_dir") else Path("data")
    db_path = Path(data_dir) / "kb.db"
    if not db_path.exists():
        db_path = Path("data/kb.db")
    print(f"DB: {db_path} exists={db_path.exists()}")

    # Apply alembic head if needed (best-effort).
    try:
        import subprocess
        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        # Prefer project alembic against runtime URL if configured.
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(ROOT),
            check=False,
        )
    except Exception as e:
        print(f"alembic upgrade skipped: {e}")

    # Ensure Database points at the production file when using legacy open.
    try:
        if hasattr(Database, "open_runtime"):
            Database.open_runtime(str(db_path), readonly=False)
        else:
            Database()
    except Exception as e:
        print(f"Database open note: {e}")

    t0 = time.time()
    last_log = [0.0]

    def progress(cur, tot, kid):
        now = time.time()
        if now - last_log[0] >= 2.0 or cur == tot:
            print(f"  passages rebuild {cur}/{tot} last={kid[:12] if kid else ''}")
            last_log[0] = now

    result = rebuild_passage_index(
        progress_callback=progress,
        embed=not args.no_embed,
        embed_batch_size=args.batch_size,
        embed_timeout=args.timeout,
        rebuild_text=not args.embed_only,
    )
    result["elapsed_sec"] = round(time.time() - t0, 1)
    print(json.dumps(result.get("health") or result, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
    health = result.get("health") or {}
    cov = float(health.get("vector_coverage") or 0)
    if args.no_embed:
        return 0
    # Non-zero if vector coverage incomplete after embed rebuild.
    if health.get("passages", 0) and cov < 0.99:
        print(f"WARNING: vector coverage={cov}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
