"""Phase 1 — deadline remaining must clamp provider timeouts."""
from __future__ import annotations

import time

from src.services.deadline import Deadline, check_cancelled, cooperative_sleep, run_with_deadline


def test_provider_timeout_clamped_to_remaining():
    dl = Deadline.start(1.0)
    time.sleep(0.4)
    rem = dl.remaining()
    budget = dl.provider_timeout(connect=5.0, read=30.0, total=60.0)
    # Allow tiny scheduling skew between remaining() samples.
    assert budget["total_timeout"] <= rem + 0.05
    assert budget["connect_timeout"] <= budget["total_timeout"] + 1e-9
    assert budget["read_timeout"] <= rem + 0.05
    assert budget["retry_limit"] in (0, 1)


def test_expired_deadline_provider_budget_is_tiny():
    dl = Deadline.start(0.05)
    time.sleep(0.08)
    budget = dl.provider_timeout()
    assert budget["total_timeout"] <= 0.02
    assert budget["retry_limit"] == 0


def test_run_with_deadline_propagates_cancel_to_cooperative_work():
    def slow():
        cooperative_sleep(10.0)
        return "done"

    t0 = time.monotonic()
    try:
        run_with_deadline(slow, 0.3)
        raised = False
    except TimeoutError as exc:
        raised = True
        assert getattr(exc, "cancelled", None) is True
        assert getattr(exc, "background_work_may_continue", True) is False
    assert raised
    assert time.monotonic() - t0 <= 1.0


def test_check_cancelled_inside_deadline_worker():
    seen = {"cancelled_checked": False}

    def work():
        cooperative_sleep(0.05)
        try:
            check_cancelled()
        except TimeoutError:
            seen["cancelled_checked"] = True
            raise
        cooperative_sleep(5.0)
        return "x"

    try:
        run_with_deadline(work, 0.2)
    except TimeoutError:
        pass
