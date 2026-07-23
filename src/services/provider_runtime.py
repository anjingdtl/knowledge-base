"""Serializable, terminable execution boundary for production providers."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

from src.services.deadline import DeadlineTimeout, run_in_terminable_process, run_with_deadline

IsolationMode = Literal["process", "async", "thread_cooperative"]


@dataclass
class ProviderRequest:
    provider_type: str
    base_url: str
    model: str
    payload: dict[str, Any]
    timeout_seconds: float
    secret_env_key: str
    # Connection tests can use credentials that the user has entered but has not
    # saved yet.  The value is sent only to the short-lived worker process and is
    # redacted from any error returned to the UI.
    credential: str = ""


@dataclass
class ProviderResponse:
    ok: bool
    data: dict[str, Any] | list[Any] | str | None = None
    error_type: str | None = None
    error_message: str | None = None
    elapsed_ms: int = 0
    worker_pid: int | None = None


def _resolve_secret(env_key: str) -> str:
    value = os.environ.get(env_key, "")
    if value:
        return value
    try:
        from src.utils.config import Config

        config_keys = {
            "SHINEHE_LLM_API_KEY": ("llm.api_key",),
            "SHINEHE_EMBEDDING_API_KEY": ("embedding.api_key", "llm.api_key"),
            "SHINEHE_RERANKER_API_KEY": (
                "reranker.api_key",
                "embedding.api_key",
                "llm.api_key",
            ),
        }.get(env_key, ())
        for config_key in config_keys:
            value = str(Config.get(config_key, "") or "")
            if value:
                return value
    except Exception:  # noqa: BLE001
        pass
    return ""


def _safe_error(error: BaseException, secret: str) -> str:
    message = f"{type(error).__name__}: {str(error)[:500]}"
    if secret:
        message = message.replace(secret, "[REDACTED]")
    return message


def _bounded_data(data: Any, max_bytes: int) -> Any:
    encoded = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"provider response exceeds {max_bytes} bytes")
    return data


def _openai_base_url(base_url: str) -> str | None:
    """Accept both an OpenAI API root and a copied endpoint URL from provider docs."""
    normalized = base_url.rstrip("/")
    for endpoint in ("/chat/completions", "/embeddings"):
        if normalized.lower().endswith(endpoint):
            normalized = normalized[:-len(endpoint)]
            break
    return normalized or None


def _rerank_url(base_url: str) -> str:
    """Avoid appending /rerank twice when a provider's full endpoint was pasted."""
    normalized = base_url.rstrip("/")
    return normalized if normalized.lower().endswith("/rerank") else f"{normalized}/rerank"


def _execute_provider_request(request: ProviderRequest) -> ProviderResponse:
    """Child-process entry.  Never logs request payloads or secrets."""
    started = time.monotonic()
    secret = request.credential or _resolve_secret(request.secret_env_key)
    try:
        if request.provider_type == "test_control":
            action = request.payload.get("action")
            if action == "hang":
                time.sleep(float(request.payload.get("seconds", 60)))
                data: Any = "finished"
            elif action == "error_with_secret":
                raise RuntimeError(f"synthetic provider failure: {secret}")
            elif action == "echo":
                data = request.payload.get("data")
            else:
                raise ValueError(f"unsupported test action: {action}")
        elif request.provider_type == "openai_compatible_chat":
            from openai import OpenAI

            if not secret:
                raise RuntimeError(f"provider credential unavailable via {request.secret_env_key}")
            openai_client = OpenAI(
                api_key=secret,
                base_url=_openai_base_url(request.base_url),
                timeout=float(request.timeout_seconds),
                max_retries=0,
            )
            response = openai_client.chat.completions.create(
                model=request.model,
                **request.payload,
                timeout=float(request.timeout_seconds),
            )
            usage_obj = getattr(response, "usage", None)
            usage = {}
            if usage_obj is not None:
                usage = {
                    "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
                }
            data = {
                "content": response.choices[0].message.content or "",
                "usage": usage,
            }
        elif request.provider_type == "openai_compatible_embedding":
            from openai import OpenAI

            if not secret:
                raise RuntimeError(f"provider credential unavailable via {request.secret_env_key}")
            embedding_client = OpenAI(
                api_key=secret,
                base_url=_openai_base_url(request.base_url),
                timeout=float(request.timeout_seconds),
                max_retries=0,
            )
            response = embedding_client.embeddings.create(
                input=request.payload.get("input") or [],
                model=request.model,
                timeout=float(request.timeout_seconds),
            )
            data = [item.embedding for item in response.data]
        elif request.provider_type == "reranker_api":
            import httpx

            if not secret:
                raise RuntimeError(f"provider credential unavailable via {request.secret_env_key}")
            timeout = float(request.timeout_seconds)
            limits = httpx.Limits(max_connections=1, max_keepalive_connections=0)
            with httpx.Client(
                timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
                limits=limits,
                follow_redirects=False,
            ) as http_client:
                response = http_client.post(
                    _rerank_url(request.base_url),
                    headers={
                        "Authorization": f"Bearer {secret}",
                        "Content-Type": "application/json",
                    },
                    json={"model": request.model, **request.payload},
                )
                response.raise_for_status()
                if len(response.content) > int(request.payload.get("max_response_bytes", 10_000_000)):
                    raise ValueError("provider response exceeds configured byte limit")
                data = response.json()
        elif request.provider_type == "local_cross_encoder":
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(request.model)
            data = [float(score) for score in model.predict(request.payload.get("pairs") or [])]
        else:
            raise ValueError(f"unsupported provider_type: {request.provider_type}")

        data = _bounded_data(data, int(request.payload.get("max_response_bytes", 10_000_000)))
        return ProviderResponse(
            ok=True,
            data=data,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            worker_pid=os.getpid(),
        )
    except BaseException as error:  # noqa: BLE001
        return ProviderResponse(
            ok=False,
            error_type=type(error).__name__,
            error_message=_safe_error(error, secret),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            worker_pid=os.getpid(),
        )


def run_provider_operation(
    operation: str,
    request: ProviderRequest,
    *,
    isolation_mode: IsolationMode,
    timeout: float,
) -> ProviderResponse:
    """Run one provider call with an explicit, auditable isolation policy."""
    timeout = max(0.01, min(float(timeout), float(request.timeout_seconds)))
    if isolation_mode == "process":
        return run_in_terminable_process(
            _execute_provider_request,
            args=(request,),
            timeout=timeout,
            operation=operation,
        )
    if isolation_mode == "thread_cooperative":
        return run_with_deadline(
            lambda: _execute_provider_request(request),
            timeout,
            isolate="thread",
        )
    if isolation_mode == "async":
        return _execute_provider_request(request)
    raise ValueError(f"unsupported isolation_mode: {isolation_mode}")


def provider_timeout_envelope(error: DeadlineTimeout) -> dict[str, Any]:
    return {
        "cancelled": bool(error.cancelled),
        "background_work_may_continue": bool(error.background_work_may_continue),
        "worker_terminated": bool(error.worker_terminated),
        "worker_pid": error.worker_pid,
        "worker_exit_code": error.worker_exit_code,
        "provider_operation": error.provider_operation or "",
    }
