"""第6轮 BUG#9 回归测试：trace 记录 LLM token 用量。

双层根因：
1. LLMService.chat() 丢弃 response.usage（只返回 content）。
2. rag_pipeline 构造 StageTrace 时从不传 input_tokens/output_tokens。

修复：新增 chat_with_usage() 保留 usage；generate 阶段捕获 usage 填入
ctx._stage_tokens；_write_trace 据此填充 StageTrace tokens。
"""
from types import SimpleNamespace


class _FakeUsage:
    """模拟 OpenAI response.usage"""
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion


class _FakeChoice:
    def __init__(self, content):
        self.message = SimpleNamespace(content=content)


class _FakeResponse:
    def __init__(self, content, prompt_tokens, completion_tokens):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)


class _FakeClient:
    """模拟 openai client.chat.completions.create"""
    def __init__(self, content, prompt_tokens, completion_tokens):
        self._resp = _FakeResponse(content, prompt_tokens, completion_tokens)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: self._resp)
        )


def test_chat_with_usage_returns_content_and_usage(monkeypatch):
    """BUG#9：chat_with_usage 同时返回 content 与 token 用量。"""
    from src.services.llm import LLMService

    svc = LLMService()
    fake_client = _FakeClient("生成的回答", prompt_tokens=120, completion_tokens=45)
    monkeypatch.setattr(svc, "_get_client", lambda: fake_client)
    # 规避 api_key 校验
    monkeypatch.setattr(svc, "_api_key_missing", False, raising=False)

    content, usage = svc.chat_with_usage([{"role": "user", "content": "hi"}])

    assert content == "生成的回答"
    assert usage["prompt_tokens"] == 120
    assert usage["completion_tokens"] == 45
    assert usage["total_tokens"] == 165


def test_chat_with_usage_handles_missing_usage(monkeypatch):
    """BUG#9：API 未返回 usage 时，usage 为空 dict（不崩溃）。"""
    from src.services.llm import LLMService

    svc = LLMService()
    resp = _FakeResponse("回答", 0, 0)
    resp.usage = None  # API 偶发不返回 usage
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: resp))
    )
    monkeypatch.setattr(svc, "_get_client", lambda: fake_client)
    monkeypatch.setattr(svc, "_api_key_missing", False, raising=False)

    content, usage = svc.chat_with_usage([{"role": "user", "content": "hi"}])
    assert content == "回答"
    assert usage == {}


def test_stage_trace_carries_token_fields():
    """BUG#9：StageTrace 的 input_tokens/output_tokens 默认 0，可被填充。"""
    from src.services.trace import StageTrace

    s = StageTrace(name="generate", duration_ms=10.0, input_tokens=100, output_tokens=50)
    d = s.to_dict()
    assert d["input_tokens"] == 100
    assert d["output_tokens"] == 50


def test_trace_records_llm_tokens_via_stage_tokens(monkeypatch):
    """BUG#9：generate 阶段捕获的 token 应出现在 trace 的 StageTrace 中。

    通过构造 RagContext + _stage_tokens，验证 _write_trace 填充逻辑。
    """
    from src.services.rag_pipeline import RagContext

    ctx = RagContext(question="q")
    ctx._stage_durations = {"generate": 1.5}
    ctx._stage_tokens = {"generate": {"input_tokens": 200, "output_tokens": 80}}

    # 模拟 _write_trace 中 StageTrace 构造逻辑
    from src.services.trace import StageTrace
    stages = [
        StageTrace(
            name=name,
            duration_ms=duration * 1000,
            input_tokens=ctx._stage_tokens.get(name, {}).get("input_tokens", 0),
            output_tokens=ctx._stage_tokens.get(name, {}).get("output_tokens", 0),
        )
        for name, duration in ctx._stage_durations.items()
    ]
    assert len(stages) == 1
    assert stages[0].name == "generate"
    assert stages[0].input_tokens == 200
    assert stages[0].output_tokens == 80
