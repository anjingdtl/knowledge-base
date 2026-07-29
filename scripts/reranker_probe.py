"""Safe diagnostics for the configured reranker provider.

It intentionally prints configuration presence and sanitized provider outcomes,
never credentials or request text.  ``--isolation`` distinguishes endpoint
latency from Windows child-process startup/termination behavior.
"""
from __future__ import annotations

import argparse
import json
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--isolation", choices=("async", "process"), default="async")
    parser.add_argument("--documents", type=int, default=1)
    parser.add_argument("--document-chars", type=int, default=80)
    args = parser.parse_args()
    from src.services.provider_runtime import ProviderRequest, run_provider_operation
    from src.utils.config import Config

    request = ProviderRequest(
        provider_type="reranker_api",
        base_url=str(Config.get("reranker.base_url", "") or ""),
        model=str(Config.get("reranker.model", "") or ""),
        payload={
            "query": "测试检索排序",
            "documents": ["用于探测排序服务的短文本" * max(1, args.document_chars // 12)
                          for _ in range(max(1, args.documents))],
            "top_n": min(max(1, args.documents), 5),
        },
        timeout_seconds=args.timeout,
        secret_env_key="SHINEHE_RERANKER_API_KEY",
        credential=str(Config.get("reranker.api_key", "") or Config.get("embedding.api_key", "") or ""),
    )
    started = time.perf_counter()
    try:
        response = run_provider_operation(
            "reranker_probe", request, isolation_mode=args.isolation, timeout=args.timeout,
        )
        result = {
            "ok": bool(response.ok), "error_type": response.error_type,
            "error_message": response.error_message, "provider_elapsed_ms": response.elapsed_ms,
            "wall_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "worker_pid": response.worker_pid, "isolation": args.isolation,
        }
    except Exception as exc:  # DeadlineTimeout carries safe structural attributes only
        result = {
            "ok": False, "error_type": type(exc).__name__, "error_message": str(exc)[:500],
            "wall_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "isolation": args.isolation,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
