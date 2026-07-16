from __future__ import annotations

import threading
import time

import pytest

from src.services import deadline
from src.services.deadline import DeadlineTimeout, reset_provider_isolation_state
from src.services.provider_runtime import ProviderRequest, run_provider_operation


def test_provider_worker_limit_rejects_excess_work(monkeypatch) -> None:
    reset_provider_isolation_state()
    monkeypatch.setattr(deadline, "_MAX_PROVIDER_WORKERS", 1)
    request = ProviderRequest(
        provider_type="test_control",
        base_url="",
        model="",
        payload={"action": "hang", "seconds": 2},
        timeout_seconds=1,
        secret_env_key="SHINEHE_TEST_SECRET",
    )
    started = threading.Event()

    def occupy() -> None:
        started.set()
        try:
            run_provider_operation("embedding", request, isolation_mode="process", timeout=1)
        except DeadlineTimeout:
            pass

    thread = threading.Thread(target=occupy)
    thread.start()
    started.wait(1)
    time.sleep(0.2)
    try:
        with pytest.raises(DeadlineTimeout, match="max_provider_workers") as error:
            run_provider_operation("embedding", request, isolation_mode="process", timeout=0.2)
        assert error.value.background_work_may_continue is False
    finally:
        thread.join(timeout=3)
        reset_provider_isolation_state()

