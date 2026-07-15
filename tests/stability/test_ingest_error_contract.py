"""Phase 0/7 — URL 导入错误诊断契约。"""
from __future__ import annotations


def test_ingest_url_404_has_status_code(monkeypatch):
    from src.mcp.tools import ingest

    monkeypatch.setattr(ingest, "_check_write_policy", lambda *_a, **_k: None)
    monkeypatch.setattr(
        ingest,
        "parse_url",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("HTTP 404: https://example.test/not-found")),
    )

    result = ingest.ingest_url(url="https://example.test/not-found")
    assert result["ok"] is False
    err = result["error"]
    assert err.get("code") == "HTTP_ERROR"
    details = err.get("details") or {}
    assert details.get("status_code") == 404
    assert details.get("stage") == "fetch"
    assert details.get("retryable") is False


def test_ingest_url_error_codes_are_stable():
    """错误码枚举应覆盖 Spec 分类。"""
    from src.services import ingest_errors

    expected = {
        "SSRF_BLOCKED",
        "DNS_ERROR",
        "CONNECT_TIMEOUT",
        "READ_TIMEOUT",
        "TLS_ERROR",
        "HTTP_ERROR",
        "REDIRECT_ERROR",
        "EMPTY_CONTENT",
        "PARSE_ERROR",
        "EMBEDDING_ERROR",
        "DATABASE_ERROR",
        "UNKNOWN",
    }
    found = set(ingest_errors.INGEST_ERROR_CODES)
    assert expected.issubset(found), f"missing ingest error taxonomy, found={found}"
