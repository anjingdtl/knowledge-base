from __future__ import annotations

from src.services.deadline import DeadlineTimeout
from src.services.provider_runtime import provider_timeout_envelope


def test_timeout_envelope_reports_real_worker_termination() -> None:
    error = DeadlineTimeout(
        "provider timed out",
        cancelled=True,
        background_work_may_continue=False,
        configured_timeout=1,
        worker_terminated=True,
        worker_pid=4321,
        worker_exit_code=-15,
        provider_operation="llm_generate",
    )
    envelope = provider_timeout_envelope(error)
    assert envelope == {
        "cancelled": True,
        "background_work_may_continue": False,
        "worker_terminated": True,
        "worker_pid": 4321,
        "worker_exit_code": -15,
        "provider_operation": "llm_generate",
    }


def test_formal_ask_timeout_envelope_keeps_provider_metadata(monkeypatch) -> None:
    from src.mcp.tools import retrieval
    from src.utils.config import Config

    class _Pipeline:
        def query(self, question, timeout):
            raise DeadlineTimeout(
                "provider timed out",
                cancelled=True,
                background_work_may_continue=False,
                configured_timeout=timeout,
                worker_terminated=True,
                worker_pid=9876,
                worker_exit_code=-15,
                provider_operation="llm_generate",
            )

    class _Container:
        rag_pipeline = _Pipeline()

    original_get = Config.get

    def fake_get(key, default=None):
        if key == "rag.ask.total_timeout":
            return 2
        return original_get(key, default)

    monkeypatch.setattr(Config, "get", fake_get)
    monkeypatch.setattr(retrieval, "_get_container", lambda: _Container())
    monkeypatch.setattr(retrieval, "_should_use_verified_ask", lambda: False)
    result = retrieval._do_ask("ordinary historical question")

    assert result["answer_mode"] == "timeout"
    assert result["route"]["cancelled"] is True
    assert result["route"]["background_work_may_continue"] is False
    assert result["route"]["worker_terminated"] is True
    assert result["route"]["worker_pid"] == 9876
    assert result["route"]["provider_operation"] == "llm_generate"
