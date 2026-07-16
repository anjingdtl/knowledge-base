"""Cost-controlled real-provider wiring smoke with secret-safe artifacts."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.services.deadline import DeadlineTimeout, provider_isolation_status
from src.services.embedding import EmbeddingService
from src.services.llm import LLMService
from src.services.rerankers.api import ApiReranker
from src.utils.config import Config

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "foundation-three-fixes" / "provider-wiring-smoke.jsonl"


def _db_sha() -> str:
    digest = hashlib.sha256()
    with (ROOT / "data" / "kb.db").open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(operation: str, index: int, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {"operation": operation, "index": index, "started_at": _now()}
    try:
        row.update(call())
        row["ok"] = True
    except DeadlineTimeout as error:
        row.update(
            ok=False,
            timed_out=True,
            cancelled=error.cancelled,
            background_work_may_continue=error.background_work_may_continue,
            worker_terminated=error.worker_terminated,
            worker_pid=error.worker_pid,
            worker_exit_code=error.worker_exit_code,
            provider_operation=error.provider_operation,
        )
    except Exception as error:  # noqa: BLE001
        row.update(ok=False, timed_out=False, error_type=type(error).__name__)
    row["finished_at"] = _now()
    row["provider_isolation"] = provider_isolation_status()
    return row


def main() -> int:
    config = Config()
    config.load()
    llm = LLMService(config)
    embedding = EmbeddingService(config)
    reranker = ApiReranker(
        base_url=str(config.get("reranker.base_url", "") or config.get("embedding.base_url", "")),
        model=str(config.get("reranker.model", "") or ""),
        api_key="configured" if config.get("reranker.api_key", "") or config.get("embedding.api_key", "") else "",
        config=config,
        timeout=float(config.get("reranker.timeout", 30) or 30),
    )
    rows: list[dict[str, Any]] = []
    db_before = _db_sha()

    for index in range(1, 4):
        rows.append(
            _record(
                "llm_generate_normal",
                index,
                lambda: {
                    "content_non_empty": bool(
                        llm.chat(
                            [{"role": "user", "content": "Reply with OK only."}],
                            silent=True,
                            max_tokens_override=8,
                            timeout=60,
                        ).strip()
                    )
                },
            )
        )
    for index in range(1, 3):
        rows.append(
            _record(
                "llm_generate_short_timeout",
                index,
                lambda: {
                    "unexpected_content": bool(
                        llm.chat(
                            [{"role": "user", "content": "Reply with OK only."}],
                            silent=True,
                            max_tokens_override=8,
                            timeout=0.01,
                        ).strip()
                    )
                },
            )
        )
    for index in range(1, 4):
        rows.append(
            _record(
                "embedding_normal",
                index,
                lambda index=index: {
                    "vector_dimension": len(embedding.embed(f"provider wiring smoke {index}"))
                },
            )
        )
    for index in range(1, 4):
        rows.append(
            _record(
                "reranker_normal",
                index,
                lambda: {
                    "scored": "rerank_score"
                    in reranker.rerank(
                        "企业微信运营",
                        [
                            {"text": "企业微信运营管理办法"},
                            {"text": "无关的餐饮菜单"},
                        ],
                        top_n=2,
                    )[0]
                },
            )
        )

    db_after = _db_sha()
    for row in rows:
        row["formal_db_sha_before"] = db_before
        row["formal_db_sha_after"] = db_after
        row["formal_db_unchanged"] = db_before == db_after
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "rows": len(rows),
                "ok": sum(row.get("ok") is True for row in rows),
                "expected_short_timeouts": sum(
                    row.get("operation") == "llm_generate_short_timeout"
                    and row.get("timed_out") is True
                    for row in rows
                ),
                "formal_db_unchanged": db_before == db_after,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
