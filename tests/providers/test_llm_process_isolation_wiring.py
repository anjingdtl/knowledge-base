from __future__ import annotations

from src.services.llm import LLMService
from src.services.provider_runtime import ProviderResponse


class _Config:
    values = {
        "llm.api_key": "do-not-put-this-in-request",
        "llm.base_url": "https://provider.invalid/v1",
        "llm.model": "model",
        "llm.timeout": 12,
        "llm.temperature": 0.2,
        "llm.max_tokens": 100,
    }

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_formal_llm_chat_uses_process_provider_request(monkeypatch) -> None:
    calls = []

    def fake_run(operation, request, *, isolation_mode, timeout):
        calls.append((operation, request, isolation_mode, timeout))
        return ProviderResponse(ok=True, data={"content": "ok", "usage": {}}, elapsed_ms=1)

    monkeypatch.setattr("src.services.llm.run_provider_operation", fake_run)
    service = LLMService(_Config())
    assert service.chat([{"role": "user", "content": "q"}]) == "ok"
    operation, request, isolation_mode, timeout = calls[0]
    assert operation == "llm_generate"
    assert isolation_mode == "process"
    assert request.secret_env_key == "SHINEHE_LLM_API_KEY"
    assert "do-not-put-this-in-request" not in repr(request)
    assert timeout == 12

