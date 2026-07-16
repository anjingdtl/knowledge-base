"""After process-isolated timeouts, subsequent lightweight work still runs."""
from __future__ import annotations

import time

import pytest

from src.services.deadline import (
    DeadlineTimeout,
    reset_provider_isolation_state,
    run_in_terminable_process,
)


def _hang(seconds: float = 30) -> str:
    time.sleep(seconds)
    return "never"


def _quick_ok() -> str:
    return "ok"


def test_timeout_then_third_request_succeeds() -> None:
    reset_provider_isolation_state()
    for _ in range(2):
        with pytest.raises(DeadlineTimeout) as ei:
            run_in_terminable_process(_hang, kwargs={"seconds": 30}, timeout=0.15)
        assert ei.value.background_work_may_continue is False
        assert ei.value.cancelled is True
    assert run_in_terminable_process(_quick_ok, timeout=5.0) == "ok"


def test_process_exits_after_timeout() -> None:
    t0 = time.monotonic()
    with pytest.raises(DeadlineTimeout):
        run_in_terminable_process(_hang, kwargs={"seconds": 60}, timeout=0.2)
    elapsed = time.monotonic() - t0
    # Should not wait the full sleep
    assert elapsed < 5.0
