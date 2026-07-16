from __future__ import annotations

import psutil
import pytest

from src.services.deadline import DeadlineTimeout, reset_provider_isolation_state
from src.services.provider_runtime import ProviderRequest, run_provider_operation


def _pid_exists(pid: int) -> bool:
    return psutil.pid_exists(pid)


def test_timed_out_provider_worker_pid_is_gone() -> None:
    reset_provider_isolation_state()
    request = ProviderRequest(
        provider_type="test_control",
        base_url="",
        model="",
        payload={"action": "hang", "seconds": 60},
        timeout_seconds=0.15,
        secret_env_key="SHINEHE_TEST_SECRET",
    )
    with pytest.raises(DeadlineTimeout) as error:
        run_provider_operation(
            "llm_generate", request, isolation_mode="process", timeout=0.15
        )
    assert error.value.worker_terminated is True
    assert error.value.worker_pid is not None
    assert not _pid_exists(error.value.worker_pid)
