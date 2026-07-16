"""Unified request deadline + cancellable execution helpers.

Goals (final-closure Spec Phase 1):
- One monotonic deadline shared across retrieval / LLM / embedding stages
- Wall-clock timeout that returns promptly
- Honest ``cancelled`` semantics (true only when work actually stopped)
- No permanent slot occupation by abandoned workers
- Provider timeouts must not exceed remaining deadline
"""
from __future__ import annotations

import contextvars
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)
R = TypeVar("R")

_cancel_event: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "shinehe_deadline_cancel", default=None
)
_deadline_mono: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "shinehe_deadline_mono", default=None
)

# Abandoned non-cooperative workers are unbounded in theory (threads cannot be
# killed safely on Windows). Cap how many we track for diagnostics only.
_abandoned_lock = threading.Lock()
_abandoned_count = 0


@dataclass
class Deadline:
    """Monotonic deadline for a single request."""

    total_timeout: float
    started: float
    deadline: float

    @classmethod
    def start(cls, total_timeout: float) -> Deadline:
        total = max(0.0, float(total_timeout))
        started = time.monotonic()
        return cls(total_timeout=total, started=started, deadline=started + total)

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self.started)

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def provider_timeout(
        self,
        *,
        connect: float = 5.0,
        read: float | None = None,
        total: float | None = None,
    ) -> dict[str, float]:
        """Clamp provider timeout budget to remaining deadline."""
        rem = self.remaining()
        if rem <= 0:
            return {
                "connect_timeout": 0.01,
                "read_timeout": 0.01,
                "write_timeout": 0.01,
                "pool_timeout": 0.01,
                "total_timeout": 0.01,
                "retry_limit": 0,
            }
        tot = min(float(total if total is not None else rem), rem)
        rd = min(float(read if read is not None else tot), rem)
        cn = min(float(connect), rem, tot)
        return {
            "connect_timeout": cn,
            "read_timeout": rd,
            "write_timeout": min(tot, rem),
            "pool_timeout": min(tot, rem),
            "total_timeout": tot,
            "retry_limit": 0 if rem < 1.0 else 1,
        }


class DeadlineTimeout(TimeoutError):
    """Timeout with honest cancellation metadata."""

    def __init__(
        self,
        message: str,
        *,
        cancelled: bool,
        background_work_may_continue: bool,
        configured_timeout: float | None = None,
    ) -> None:
        super().__init__(message)
        self.cancelled = cancelled
        self.background_work_may_continue = background_work_may_continue
        self.configured_timeout = configured_timeout


def get_cancel_event() -> threading.Event | None:
    return _cancel_event.get()


def get_deadline_mono() -> float | None:
    return _deadline_mono.get()


def remaining_deadline() -> float | None:
    dl = _deadline_mono.get()
    if dl is None:
        return None
    return max(0.0, dl - time.monotonic())


def check_cancelled() -> None:
    """Raise if the current deadline was cancelled."""
    ev = _cancel_event.get()
    if ev is not None and ev.is_set():
        raise DeadlineTimeout(
            "operation cancelled by deadline",
            cancelled=True,
            background_work_may_continue=False,
        )
    rem = remaining_deadline()
    if rem is not None and rem <= 0:
        raise DeadlineTimeout(
            "operation exceeded deadline",
            cancelled=True,
            background_work_may_continue=False,
        )


def cooperative_sleep(seconds: float) -> None:
    """Sleep that exits promptly when the deadline cancel event is set."""
    seconds = max(0.0, float(seconds))
    ev = _cancel_event.get()
    if ev is None:
        time.sleep(seconds)
        check_cancelled()
        return
    if ev.wait(seconds):
        raise DeadlineTimeout(
            "operation cancelled by deadline during sleep",
            cancelled=True,
            background_work_may_continue=False,
        )
    check_cancelled()


def abandoned_worker_count() -> int:
    with _abandoned_lock:
        return _abandoned_count


# Process isolation pool limits (Spec Phase 4)
_MAX_PROVIDER_WORKERS = 8
_active_provider_workers = 0
_provider_worker_lock = threading.Lock()
_circuit_failures = 0
_circuit_open_until = 0.0
_CIRCUIT_FAILURE_THRESHOLD = 20
_CIRCUIT_COOLDOWN_SEC = 30.0


def _process_worker_entry(
    fn: Callable[..., R],
    args: tuple,
    kwargs: dict,
    out_q: "queue.Queue[tuple[str, object]]",
) -> None:
    try:
        out_q.put(("ok", fn(*args, **kwargs)))
    except BaseException as exc:  # noqa: BLE001
        out_q.put(("err", exc))


def run_in_terminable_process(
    fn: Callable[..., R],
    *,
    args: tuple = (),
    kwargs: dict | None = None,
    timeout: float = 30.0,
) -> R:
    """Run a picklable callable in a child process; terminate on timeout.

    Guarantees:
    - On timeout the child is terminated and joined
    - ``background_work_may_continue`` is always False
    - Does not pass DB connections, API keys, or containers
    """
    global _active_provider_workers, _circuit_failures, _circuit_open_until

    import multiprocessing as mp

    timeout = max(0.0, float(timeout))
    kwargs = dict(kwargs or {})

    now = time.monotonic()
    if now < _circuit_open_until:
        raise DeadlineTimeout(
            "provider circuit breaker open",
            cancelled=True,
            background_work_may_continue=False,
            configured_timeout=timeout,
        )

    with _provider_worker_lock:
        if _active_provider_workers >= _MAX_PROVIDER_WORKERS:
            raise DeadlineTimeout(
                "max_provider_workers exceeded",
                cancelled=True,
                background_work_may_continue=False,
                configured_timeout=timeout,
            )
        _active_provider_workers += 1

    ctx = mp.get_context("spawn")
    out_q: mp.Queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(
        target=_process_worker_entry,
        args=(fn, args, kwargs, out_q),
        name="ShineHeProviderWorker",
        daemon=True,
    )
    try:
        proc.start()
        proc.join(timeout=timeout)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2.0)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=1.0)
            _circuit_failures += 1
            if _circuit_failures >= _CIRCUIT_FAILURE_THRESHOLD:
                _circuit_open_until = time.monotonic() + _CIRCUIT_COOLDOWN_SEC
                _circuit_failures = 0
            raise DeadlineTimeout(
                f"operation exceeded deadline of {timeout:g}s (process terminated)",
                cancelled=True,
                background_work_may_continue=False,
                configured_timeout=timeout,
            )

        # Child exited — collect result
        try:
            status, payload = out_q.get_nowait()
        except Exception as exc:  # noqa: BLE001
            if proc.exitcode not in (0, None):
                raise DeadlineTimeout(
                    f"provider worker crashed rc={proc.exitcode}",
                    cancelled=True,
                    background_work_may_continue=False,
                    configured_timeout=timeout,
                ) from exc
            raise RuntimeError("provider worker returned no result") from exc

        if status == "ok":
            _circuit_failures = 0
            return payload  # type: ignore[return-value]
        if isinstance(payload, BaseException):
            raise payload
        raise RuntimeError(str(payload))
    finally:
        with _provider_worker_lock:
            _active_provider_workers = max(0, _active_provider_workers - 1)
        try:
            out_q.close()
        except Exception:  # noqa: BLE001
            pass


def run_with_deadline(
    fn: Callable[[], R],
    timeout: float,
    *,
    isolate: str = "thread",
) -> R:
    """Run ``fn`` under a wall-clock deadline.

    isolate:
      - ``thread`` (default): cooperative cancel event; non-coop may set
        background_work_may_continue=True (legacy honest semantics)
      - ``process``: terminable process isolation; background always false
    """
    global _abandoned_count

    if isolate == "process":
        return run_in_terminable_process(fn, timeout=timeout)

    timeout = max(0.0, float(timeout))
    deadline = Deadline.start(timeout)
    cancel = threading.Event()
    result_q: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    token_cancel = _cancel_event.set(cancel)
    token_deadline = _deadline_mono.set(deadline.deadline)

    def _runner() -> None:
        try:
            # Re-bind context in worker thread
            _cancel_event.set(cancel)
            _deadline_mono.set(deadline.deadline)
            try:
                result_q.put((True, fn()))
            except BaseException as exc:  # noqa: BLE001 - marshal to caller
                result_q.put((False, exc))
        finally:
            _cancel_event.set(None)
            _deadline_mono.set(None)

    thread = threading.Thread(target=_runner, name="ShineHeDeadlineWorker", daemon=True)
    thread.start()
    thread.join(timeout=max(0.0, deadline.remaining()))

    if not thread.is_alive():
        _cancel_event.reset(token_cancel)
        _deadline_mono.reset(token_deadline)
        ok_flag, payload = result_q.get_nowait()
        if ok_flag:
            return payload  # type: ignore[return-value]
        if isinstance(payload, BaseException):
            raise payload
        raise RuntimeError(str(payload))

    # Deadline hit — signal cooperative cancellation and wait briefly.
    cancel.set()
    thread.join(timeout=0.15)

    _cancel_event.reset(token_cancel)
    _deadline_mono.reset(token_deadline)

    if not thread.is_alive():
        # Worker observed cancel / finished during grace.
        try:
            ok_flag, payload = result_q.get_nowait()
            if ok_flag:
                return payload  # type: ignore[return-value]
            if isinstance(payload, DeadlineTimeout):
                raise payload
            if isinstance(payload, BaseException):
                # Treat cancel-induced errors as cancelled timeout
                raise DeadlineTimeout(
                    f"operation exceeded deadline of {timeout:g}s",
                    cancelled=True,
                    background_work_may_continue=False,
                    configured_timeout=timeout,
                ) from payload
        except queue.Empty:
            pass
        raise DeadlineTimeout(
            f"operation exceeded deadline of {timeout:g}s",
            cancelled=True,
            background_work_may_continue=False,
            configured_timeout=timeout,
        )

    # Non-cooperative thread worker still running.
    # Prefer process isolation for production providers (isolate="process").
    # Thread mode remains for cooperative callables only.
    with _abandoned_lock:
        _abandoned_count += 1

    def _track_finish() -> None:
        global _abandoned_count
        thread.join()
        with _abandoned_lock:
            _abandoned_count = max(0, _abandoned_count - 1)

    threading.Thread(target=_track_finish, name="ShineHeDeadlineReaper", daemon=True).start()

    raise DeadlineTimeout(
        f"operation exceeded deadline of {timeout:g}s",
        cancelled=False,
        background_work_may_continue=True,
        configured_timeout=timeout,
    )
