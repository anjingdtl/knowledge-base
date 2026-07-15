"""Structured URL/file ingest error taxonomy for MCP diagnostics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Stable taxonomy — keep in sync with MCP stability Spec.
SSRF_BLOCKED = "SSRF_BLOCKED"
DNS_ERROR = "DNS_ERROR"
CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
READ_TIMEOUT = "READ_TIMEOUT"
TLS_ERROR = "TLS_ERROR"
HTTP_ERROR = "HTTP_ERROR"
REDIRECT_ERROR = "REDIRECT_ERROR"
EMPTY_CONTENT = "EMPTY_CONTENT"
PARSE_ERROR = "PARSE_ERROR"
EMBEDDING_ERROR = "EMBEDDING_ERROR"
DATABASE_ERROR = "DATABASE_ERROR"
UNKNOWN = "UNKNOWN"

INGEST_ERROR_CODES = frozenset({
    SSRF_BLOCKED,
    DNS_ERROR,
    CONNECT_TIMEOUT,
    READ_TIMEOUT,
    TLS_ERROR,
    HTTP_ERROR,
    REDIRECT_ERROR,
    EMPTY_CONTENT,
    PARSE_ERROR,
    EMBEDDING_ERROR,
    DATABASE_ERROR,
    UNKNOWN,
})


@dataclass
class IngestError(Exception):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


def classify_http_exception(exc: BaseException, *, url: str, stage: str = "fetch") -> IngestError:
    """Map transport/parser exceptions to a stable ingest error."""
    import httpx

    details: dict[str, Any] = {
        "url": url,
        "stage": stage,
        "retryable": False,
        "redirect_chain": [],
    }

    # Explicit HTTP status RuntimeError from parse_url: "HTTP 404: url"
    msg = str(exc)
    if msg.startswith("HTTP ") and ":" in msg:
        try:
            status = int(msg.split()[1].rstrip(":"))
        except (IndexError, ValueError):
            status = None
        if status is not None:
            details["status_code"] = status
            details["retryable"] = status >= 500 or status == 429
            return IngestError(
                code=HTTP_ERROR,
                message=f"URL returned HTTP {status}",
                details=details,
            )

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        details["status_code"] = status
        details["retryable"] = bool(status and (status >= 500 or status == 429))
        return IngestError(
            code=HTTP_ERROR,
            message=f"URL returned HTTP {status}",
            details=details,
        )

    if isinstance(exc, httpx.ConnectTimeout):
        details["retryable"] = True
        return IngestError(code=CONNECT_TIMEOUT, message="Connect timeout", details=details)

    if isinstance(exc, (httpx.ReadTimeout, httpx.TimeoutException)):
        details["retryable"] = True
        return IngestError(code=READ_TIMEOUT, message="Read timeout", details=details)

    if isinstance(exc, httpx.TooManyRedirects):
        return IngestError(code=REDIRECT_ERROR, message="Too many redirects", details=details)

    if isinstance(exc, httpx.ConnectError):
        lower = msg.lower()
        if "name or service not known" in lower or "getaddrinfo" in lower or "nodename" in lower:
            return IngestError(code=DNS_ERROR, message="DNS resolution failed", details=details)
        if "ssl" in lower or "tls" in lower or "certificate" in lower:
            return IngestError(code=TLS_ERROR, message="TLS handshake failed", details=details)
        details["retryable"] = True
        return IngestError(code=CONNECT_TIMEOUT, message=f"Connect error: {exc}", details=details)

    lower = msg.lower()
    if "ssrf" in lower or "内网" in lower or "不允许" in lower or "private" in lower:
        return IngestError(code=SSRF_BLOCKED, message=msg, details=details)
    if "ssl" in lower or "tls" in lower or "certificate" in lower:
        return IngestError(code=TLS_ERROR, message=msg, details=details)
    if "timeout" in lower:
        details["retryable"] = True
        return IngestError(code=READ_TIMEOUT, message=msg, details=details)
    if "为空" in msg or "empty" in lower or "过短" in msg:
        return IngestError(code=EMPTY_CONTENT, message=msg, details=details)
    if "dns" in lower:
        return IngestError(code=DNS_ERROR, message=msg, details=details)

    return IngestError(code=UNKNOWN, message=msg or type(exc).__name__, details=details)
