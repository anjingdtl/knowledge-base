"""LLM 统一接口 — 基于 OpenAI 兼容协议，支持任意供应商"""
import threading

from src.utils.config import Config

_status_callbacks: set = set()
_status_lock = threading.Lock()


def register_llm_status_callback(callback):
    if callback not in _status_callbacks:
        with _status_lock:
            _status_callbacks.add(callback)


def unregister_llm_status_callback(callback):
    """反注册 LLM 状态回调函数"""
    try:
        _status_callbacks.remove(callback)
    except ValueError:
        pass


def _notify_status(status: str, detail: str = ""):
    with _status_lock:
        callbacks = list(_status_callbacks)
    for cb in callbacks:
        try:
            cb(status, detail)
        except Exception:
            pass


class LLMService:
    def __init__(self, config=None):
        """初始化 LLM 服务

        Args:
            config: Config 实例（DI 注入），为 None 时回退到全局单例（兼容旧代码）
        """
        self._config = config
        self._client = None

    def _cfg(self, key: str, default=None):
        """读取配置，优先使用注入的 config，回退到全局单例"""
        if self._config is not None:
            return self._config.get(key, default)
        return Config.get(key, default)

    def _get_client(self):
        if self._client is not None:
            return self._client
        from openai import OpenAI
        timeout = float(self._cfg("llm.timeout", 60) or 60)
        self._client = OpenAI(
            api_key=self._cfg("llm.api_key", "") or "no-key",
            base_url=self._cfg("llm.base_url") or None,
            timeout=timeout,
        )
        return self._client

    def chat(self, messages: list[dict], silent: bool = False, max_tokens_override: int | None = None) -> str:
        if not silent:
            _notify_status("running", "LLM 推理")
        try:
            client = self._get_client()
            model = self._cfg("llm.model", "")
            temperature = self._cfg("llm.temperature", 0.7)
            max_tokens = max_tokens_override or self._cfg("llm.max_tokens", 2048)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            if not silent:
                _notify_status("error", str(e)[:100])
            raise
        finally:
            if not silent:
                _notify_status("idle")

    def chat_stream(self, messages: list[dict], silent: bool = False, max_tokens_override: int | None = None):
        if not silent:
            _notify_status("running", "LLM 流式推理")
        try:
            client = self._get_client()
            model = self._cfg("llm.model", "")
            temperature = self._cfg("llm.temperature", 0.7)
            max_tokens = max_tokens_override or self._cfg("llm.max_tokens", 2048)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            if not silent:
                _notify_status("error", str(e)[:100])
            raise
        finally:
            if not silent:
                _notify_status("idle")

    def reset_client(self):
        self._client = None
