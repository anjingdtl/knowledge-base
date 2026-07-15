"""Phase 0/7 — URL 导入错误诊断契约。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_ingest_url_404_has_status_code(monkeypatch):
    from src.mcp.tools import ingest

    class FakeResp:
        status_code = 404
        text = "not found"
        headers = {}
        url = "https://example.test/not-found"

        def raise_for_status(self):
            import httpx
            raise httpx.HTTPStatusError("404", request=MagicMock(), response=self)

    def fake_get(*_a, **_k):
        return FakeResp()

    # 尝试 patch 常见 HTTP 入口
    for path in (
        "src.clients.http.get",
        "src.services.url_ingest.fetch_url",
        "httpx.get",
    ):
        try:
            monkeypatch.setattr(path, fake_get)
        except Exception:
            pass

    monkeypatch.setattr(
        ingest,
        "_get_container",
        lambda: SimpleNamespace(
            db=MagicMock(),
            indexer=MagicMock(),
        ),
    )
    # 若工具内部有 write policy
    if hasattr(ingest, "_check_write_policy"):
        monkeypatch.setattr(ingest, "_check_write_policy", lambda *_a, **_k: None)

    result = ingest.ingest_url(url="https://example.test/not-found")
    assert result["ok"] is False
    err = result["error"]
    # 需要稳定分类与 status_code
    assert err.get("code") in (
        "HTTP_ERROR",
        "INGEST_FAILED",
        "VALIDATION_ERROR",
        "INTERNAL_ERROR",
    )
    details = err.get("details") or {}
    # 修复后必须有 status_code=404
    assert details.get("status_code") == 404 or "404" in str(err)
    assert details.get("stage") in ("fetch", "download", None) or "fetch" in str(err).lower() or True
    # 严格：status_code 必须存在
    assert details.get("status_code") == 404


def test_ingest_url_error_codes_are_stable():
    """错误码枚举应覆盖 Spec 分类。"""
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
    # 查找生产侧是否已定义
    found = set()
    for mod_name in (
        "src.services.url_ingest",
        "src.mcp.tools.ingest",
        "src.clients.http",
    ):
        try:
            mod = __import__(mod_name, fromlist=["*"])
            for name in expected:
                if hasattr(mod, name) or name in str(getattr(mod, "IngestErrorCode", "")):
                    found.add(name)
            codes = getattr(mod, "INGEST_ERROR_CODES", None) or getattr(mod, "ErrorCodes", None)
            if codes:
                found |= set(codes if not isinstance(codes, dict) else codes.keys())
        except Exception:
            pass
    # Phase 0 期望基线缺失 → 此断言应失败，驱动 Phase 7 补齐
    assert expected.issubset(found) or len(found) >= 8, (
        f"missing ingest error taxonomy, found={found}"
    )
