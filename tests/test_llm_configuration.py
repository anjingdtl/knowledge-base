import pytest

from src.services.llm import LLMService


class MutableConfig:
    def __init__(self):
        self.values = {
            "llm.api_key": "old-key",
            "llm.base_url": "https://example.invalid/v1",
            "llm.timeout": 10,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_llm_client_is_rebuilt_when_credentials_change(monkeypatch):
    config = MutableConfig()
    created = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    service = LLMService(config)

    first = service._get_client()
    config.values["llm.api_key"] = "new-key"
    second = service._get_client()

    assert first is not second
    assert created[0]["api_key"] == "old-key"
    assert created[1]["api_key"] == "new-key"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "API Key 无效或已失效"),
        (429, "请求过于频繁或额度不足"),
    ],
)
def test_llm_errors_are_converted_to_actionable_messages(status_code, expected):
    class ProviderError(Exception):
        pass

    error = ProviderError("raw provider payload")
    error.status_code = status_code

    friendly = LLMService.format_error(error)

    assert expected in friendly
    assert "raw provider payload" not in friendly
