from __future__ import annotations

import pytest

from src.services.deadline import DeadlineTimeout
from src.services.rerankers.api import ApiReranker


def test_api_reranker_timeout_from_process_is_not_swallowed(monkeypatch) -> None:
    calls = []

    def fake_run(operation, request, *, isolation_mode, timeout):
        calls.append((operation, request, isolation_mode, timeout))
        raise DeadlineTimeout(
            "terminated",
            cancelled=True,
            background_work_may_continue=False,
            configured_timeout=timeout,
            worker_terminated=True,
            provider_operation=operation,
        )

    monkeypatch.setattr("src.services.rerankers.api.run_provider_operation", fake_run)
    reranker = ApiReranker(
        base_url="https://provider.invalid/v1",
        model="reranker-model",
        api_key="not-serialized",
        timeout=3,
    )
    with pytest.raises(DeadlineTimeout):
        reranker.rerank("q", [{"text": "candidate"}])
    assert calls[0][0] == "reranker"
    assert calls[0][2] == "process"

