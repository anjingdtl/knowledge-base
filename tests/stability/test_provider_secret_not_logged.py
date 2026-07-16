from __future__ import annotations

import logging

from src.services.provider_runtime import ProviderRequest, run_provider_operation


def test_provider_secret_is_redacted_from_response_and_logs(monkeypatch, caplog) -> None:
    secret = "super-secret-value-should-never-appear"
    monkeypatch.setenv("SHINEHE_TEST_SECRET", secret)
    request = ProviderRequest(
        provider_type="test_control",
        base_url="",
        model="",
        payload={"action": "error_with_secret"},
        timeout_seconds=2,
        secret_env_key="SHINEHE_TEST_SECRET",
    )
    with caplog.at_level(logging.DEBUG):
        response = run_provider_operation(
            "llm_generate", request, isolation_mode="process", timeout=2
        )
    assert response.ok is False
    assert secret not in str(response.error_message)
    assert secret not in caplog.text

