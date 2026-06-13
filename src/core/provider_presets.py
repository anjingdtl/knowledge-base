"""AI 服务商预设模板 — GUI、CLI、测试共享

提供不可变的 ProviderPreset 数据类及查找函数，
供 setup_wizard（GUI）、shinehe init（CLI）、单元测试三方复用。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderPreset:
    """AI 服务商预设配置（不可变）"""

    canonical_name: str
    display_name: str
    embedding_base_url: str
    embedding_model: str
    llm_base_url: str
    llm_model: str
    reranker_base_url: str = ""
    reranker_model: str = ""
    requires_api_key: bool = True
    api_key_placeholder: str | None = None


# ---------------------------------------------------------------------------
# 预设服务商列表
# ---------------------------------------------------------------------------
PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "siliconflow": ProviderPreset(
        canonical_name="siliconflow",
        display_name="SiliconFlow（硅基流动）",
        embedding_base_url="https://api.siliconflow.cn/v1",
        embedding_model="BAAI/bge-m3",
        llm_base_url="https://api.siliconflow.cn/v1",
        llm_model="Qwen/Qwen3-8B",
        reranker_base_url="https://api.siliconflow.cn/v1",
        reranker_model="BAAI/bge-reranker-v2-m3",
    ),
    "minimax": ProviderPreset(
        canonical_name="minimax",
        display_name="MiniMax",
        embedding_base_url="https://api.minimaxi.com/v1",
        embedding_model="minimax-embedding",
        llm_base_url="https://api.minimaxi.com/v1",
        llm_model="MiniMax-M2.7",
    ),
    "openai": ProviderPreset(
        canonical_name="openai",
        display_name="OpenAI",
        embedding_base_url="https://api.openai.com/v1",
        embedding_model="text-embedding-3-small",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
    ),
    "deepseek": ProviderPreset(
        canonical_name="deepseek",
        display_name="DeepSeek",
        embedding_base_url="https://api.deepseek.com/v1",
        embedding_model="",
        llm_base_url="https://api.deepseek.com/v1",
        llm_model="deepseek-chat",
    ),
    "zhipu": ProviderPreset(
        canonical_name="zhipu",
        display_name="智谱 AI（GLM）",
        embedding_base_url="https://open.bigmodel.cn/api/paas/v4",
        embedding_model="embedding-3",
        llm_base_url="https://open.bigmodel.cn/api/paas/v4",
        llm_model="glm-4-flash",
    ),
    "moonshot": ProviderPreset(
        canonical_name="moonshot",
        display_name="Moonshot（Kimi）",
        embedding_base_url="https://api.moonshot.cn/v1",
        embedding_model="",
        llm_base_url="https://api.moonshot.cn/v1",
        llm_model="moonshot-v1-8k",
    ),
    "ollama": ProviderPreset(
        canonical_name="ollama",
        display_name="Ollama（本地模型）",
        embedding_base_url="http://localhost:11434/v1",
        embedding_model="nomic-embed-text",
        llm_base_url="http://localhost:11434/v1",
        llm_model="qwen2.5",
        requires_api_key=False,
        api_key_placeholder="ollama",
    ),
    "custom": ProviderPreset(
        canonical_name="custom",
        display_name="自定义",
        embedding_base_url="",
        embedding_model="",
        llm_base_url="",
        llm_model="",
        requires_api_key=False,
    ),
}

# ---------------------------------------------------------------------------
# 内部查找索引（大小写不敏感 + 中文名映射）
# ---------------------------------------------------------------------------
_LOOKUP: dict[str, str] = {}
for _name, _preset in PROVIDER_PRESETS.items():
    _LOOKUP[_name.lower()] = _name
    _LOOKUP[_preset.display_name.lower()] = _name
    # 额外中文别名，方便用户直接输入中文查找
    _LOOKUP["硅基流动"] = "siliconflow"
    _LOOKUP["智谱"] = "zhipu"
    _LOOKUP["智谱ai"] = "zhipu"
    _LOOKUP["智谱 ai"] = "zhipu"


def get_provider_preset(name: str) -> ProviderPreset:
    """按名称查找服务商预设（大小写不敏感，支持中文显示名）

    Raises:
        KeyError: 未找到匹配的服务商
    """
    key = name.strip().lower()
    canonical = _LOOKUP.get(key)
    if canonical is None:
        raise KeyError(f"Unknown provider: {name!r}")
    return PROVIDER_PRESETS[canonical]


def list_providers() -> list[ProviderPreset]:
    """返回所有预设服务商列表（按定义顺序）"""
    return list(PROVIDER_PRESETS.values())
