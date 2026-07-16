from __future__ import annotations

import pytest

from src.services.deadline import DeadlineTimeout
from src.services.embedding import EmbeddingService


class _Config:
    values = {
        "embedding.base_url": "https://provider.invalid/v1",
        "embedding.model": "embedding-model",
        "embedding.timeout": 2,
        "embedding.max_concurrent_batches": 1,
    }

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_embedding_timeout_from_process_is_not_swallowed(monkeypatch) -> None:
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

    monkeypatch.setattr("src.services.embedding.run_provider_operation", fake_run)
    with pytest.raises(DeadlineTimeout) as error:
        EmbeddingService(_Config()).embed("text")
    assert error.value.worker_terminated is True
    assert calls[0][0] == "embedding"
    assert calls[0][2] == "process"

