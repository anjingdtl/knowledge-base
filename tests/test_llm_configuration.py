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


def test_llm_missing_api_key_is_flagged_and_warned(monkeypatch, caplog):
    """BUG-1: api_key 缺失时不应静默兜底成 'no-key' 撞模糊 401，
    而应设置 _api_key_missing 标志并告警一次（保留 no-key 兜底避免破坏）。"""
    import logging

    import src.services.llm as llm_mod

    # reset module 级告警 flag，确保本次测试能触发 warning
    llm_mod._api_key_missing_warned = False

    config = MutableConfig()
    config.values["llm.api_key"] = ""  # 模拟服务进程未读取到 key

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    service = LLMService(config)

    with caplog.at_level(logging.WARNING, logger="src.services.llm"):
        client = service._get_client()

    assert client is not None  # 保留 no-key 兜底，不破坏纯检索用途
    assert service._api_key_missing is True
    assert any("SHINEHE_LLM_API_KEY" in r.message for r in caplog.records)


def test_llm_error_appends_key_missing_hint_when_unset(monkeypatch):
    """BUG-1: api_key 缺失时，认证失败错误应追加配置指引，
    帮助区分'未读取到 key'与'key 失效'。"""
    config = MutableConfig()
    config.values["llm.api_key"] = ""

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    service = LLMService(config)
    service._get_client()  # 触发 _api_key_missing 标志

    class ProviderError(Exception):
        pass

    error = ProviderError("raw")
    error.status_code = 401

    message = service._format_error_with_context(error)
    # 原文本保留（不破坏 test_llm_errors_are_converted 的契约）
    assert "API Key 无效或已失效" in message
    # 追加配置指引
    assert "SHINEHE_LLM_API_KEY" in message
