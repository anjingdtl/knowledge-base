"""LLM 统一接口 — 基于 OpenAI 兼容协议，支持任意供应商"""
import hashlib
import logging
import threading

from src.utils.config import Config

logger = logging.getLogger(__name__)

# 标记是否已就 "API Key 缺失" 告警过一次，避免循环或多次实例化时刷屏
_api_key_missing_warned = False

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
        self._client_signature = None
        # 标记当前进程是否读取到有效 api_key，用于调用失败时给出精确诊断
        self._api_key_missing = False

    def _cfg(self, key: str, default=None):
        """读取配置，优先使用注入的 config，回退到全局单例"""
        if self._config is not None:
            return self._config.get(key, default)
        return Config.get(key, default)

    def _get_client(self):
        api_key = self._cfg("llm.api_key", "")
        self._api_key_missing = not bool(api_key)
        if self._api_key_missing:
            # 静默兜底为 "no-key" 会让上游返回模糊的 401，这里一次性告警，
            # 指明三条配置路径，便于定位"未读取到 key"而非"key 失效"。
            global _api_key_missing_warned
            if not _api_key_missing_warned:
                logger.warning(
                    "LLM API Key 未配置（llm.api_key 为空），ask/RAG 生成、"
                    "查询改写与 LLM 重排序将失败。请通过以下任一方式配置："
                    "1) GUI 设置 → LLM；2) 环境变量 SHINEHE_LLM_API_KEY；"
                    "3) keyring。Windows Service 需在服务账户下配置或注入"
                    "系统环境变量。"
                )
                _api_key_missing_warned = True
            api_key = "no-key"
        base_url = self._cfg("llm.base_url") or None
        timeout = float(self._cfg("llm.timeout", 60) or 60)
        signature = (
            base_url,
            timeout,
            hashlib.sha256(api_key.encode("utf-8")).digest(),
        )
        if self._client is not None and self._client_signature == signature:
            return self._client
        from openai import OpenAI
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self._client_signature = signature
        return self._client

    @staticmethod
    def format_error(error: Exception) -> str:
        """Convert provider failures into concise, actionable user messages."""
        status = getattr(error, "status_code", None)
        if status in (401, 403):
            return (
                "LLM 认证失败：API Key 无效或已失效。"
                "请打开“设置 → LLM”，填写该供应商的有效 API Secret Key 后保存并重试。"
            )
        if status == 429:
            return (
                "LLM 请求失败：请求过于频繁或额度不足。"
                "请检查供应商账户额度，稍后再试。"
            )
        if status and status >= 500:
            return f"LLM 服务暂时不可用（HTTP {status}），请稍后重试。"

        name = type(error).__name__.lower()
        if "timeout" in name:
            return "LLM 请求超时，请检查网络或在设置中增大超时时间。"
        if "connection" in name:
            return "无法连接 LLM 服务，请检查 API 地址和网络连接。"
        return f"LLM 调用失败：{str(error)[:300]}"

    def _format_error_with_context(self, error: Exception) -> str:
        """包装 format_error，在本进程 api_key 缺失时追加配置指引。

        保留 format_error 的 staticmethod 签名不变（其他调用方依赖），
        仅在 chat/chat_stream 的失败路径上补充诊断信息。
        """
        message = self.format_error(error)
        if self._api_key_missing and "认证失败" in message:
            message += (
                "（当前进程未读取到 llm.api_key：可能 keyring 在本账户下"
                "不可用，或环境变量 SHINEHE_LLM_API_KEY 未注入。"
                "Windows Service 请用 setx /M 或服务 Environment 注册表注入。）"
            )
        return message

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
            message = self._format_error_with_context(e)
            if not silent:
                _notify_status("error", message[:100])
            raise RuntimeError(message) from e
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
            message = self._format_error_with_context(e)
            if not silent:
                _notify_status("error", message[:100])
            raise RuntimeError(message) from e
        finally:
            if not silent:
                _notify_status("idle")

    def reset_client(self):
        self._client = None
        self._client_signature = None
