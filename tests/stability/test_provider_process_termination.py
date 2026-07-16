"""Non-cooperative providers must terminate workers; no abandoned background work."""
from __future__ import annotations

import time

import pytest

from src.services.deadline import (
    DeadlineTimeout,
    abandoned_worker_count,
    run_in_terminable_process,
    run_with_deadline,
)


def _eternal_sleep(seconds: float = 3600) -> str:
    time.sleep(seconds)
    return "done"


def _eternal_sleep_default() -> str:
    """Zero-arg picklable target for run_with_deadline(isolate='process')."""
    return _eternal_sleep(3600)


def _add(a: int, b: int) -> int:
    return a + b


def test_noncooperative_sleep_terminates_process() -> None:
    before = abandoned_worker_count()
    with pytest.raises(DeadlineTimeout) as ei:
        run_in_terminable_process(_eternal_sleep, kwargs={"seconds": 3600}, timeout=0.3)
    exc = ei.value
    assert exc.cancelled is True
    assert exc.background_work_may_continue is False
    # allow reaper to settle
    time.sleep(0.2)
    assert abandoned_worker_count() <= before


def test_run_with_deadline_isolate_process_mode() -> None:
    with pytest.raises(DeadlineTimeout) as ei:
        run_with_deadline(
            _eternal_sleep_default,
            timeout=0.3,
            isolate="process",
        )
    assert ei.value.background_work_may_continue is False
    assert ei.value.cancelled is True


def test_process_success_returns_value() -> None:
    assert run_in_terminable_process(_add, args=(2, 3), timeout=5.0) == 5


def test_fifty_timeouts_no_abandoned_growth() -> None:
    base = abandoned_worker_count()
    for _ in range(50):
        with pytest.raises(DeadlineTimeout):
            run_in_terminable_process(_eternal_sleep, kwargs={"seconds": 60}, timeout=0.05)
    time.sleep(0.5)
    assert abandoned_worker_count() <= base
