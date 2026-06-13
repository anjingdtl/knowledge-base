"""Provider 预设单元测试

可直接运行，无需 PySide6。
"""
import pytest

from src.core.provider_presets import (
    PROVIDER_PRESETS,
    ProviderPreset,
    get_provider_preset,
    list_providers,
)


# ---------------------------------------------------------------------------
# 基础查找测试
# ---------------------------------------------------------------------------

def test_ollama_preset():
    preset = get_provider_preset("ollama")
    assert preset.embedding_base_url == "http://localhost:11434/v1"
    assert preset.embedding_model == "nomic-embed-text"
    assert preset.requires_api_key is False


def test_all_providers_listed():
    providers = list_providers()
    assert len(providers) == 8
    names = {p.canonical_name for p in providers}
    assert "ollama" in names
    assert "openai" in names


def test_chinese_name_lookup():
    preset = get_provider_preset("SiliconFlow（硅基流动）")
    assert preset.canonical_name == "siliconflow"


def test_unknown_provider_raises():
    with pytest.raises(KeyError):
        get_provider_preset("nonexistent")


# ---------------------------------------------------------------------------
# 大小写与别名测试
# ---------------------------------------------------------------------------

def test_case_insensitive_lookup():
    p1 = get_provider_preset("OpenAI")
    p2 = get_provider_preset("openai")
    p3 = get_provider_preset("OPENAI")
    assert p1.canonical_name == p2.canonical_name == p3.canonical_name == "openai"


def test_chinese_alias_zhipu():
    preset = get_provider_preset("智谱 AI（GLM）")
    assert preset.canonical_name == "zhipu"


def test_chinese_alias_moonshot():
    preset = get_provider_preset("Moonshot（Kimi）")
    assert preset.canonical_name == "moonshot"


def test_chinese_alias_ollama():
    preset = get_provider_preset("Ollama（本地模型）")
    assert preset.canonical_name == "ollama"


# ---------------------------------------------------------------------------
# 数据完整性测试
# ---------------------------------------------------------------------------

def test_preset_is_immutable():
    preset = get_provider_preset("openai")
    with pytest.raises(AttributeError):
        preset.llm_model = "new-model"


def test_siliconflow_has_reranker():
    preset = get_provider_preset("siliconflow")
    assert preset.reranker_base_url == "https://api.siliconflow.cn/v1"
    assert preset.reranker_model == "BAAI/bge-reranker-v2-m3"


def test_custom_preset():
    preset = get_provider_preset("custom")
    assert preset.embedding_base_url == ""
    assert preset.embedding_model == ""
    assert preset.llm_base_url == ""
    assert preset.llm_model == ""
    assert preset.requires_api_key is False


def test_ollama_api_key_placeholder():
    preset = get_provider_preset("ollama")
    assert preset.api_key_placeholder == "ollama"


def test_all_presets_have_required_fields():
    """每个非 custom 预设至少包含 llm_base_url 和 llm_model"""
    for name, preset in PROVIDER_PRESETS.items():
        if name == "custom":
            continue
        assert preset.llm_base_url.startswith("http"), f"{name} llm_base_url 无效"
        assert preset.llm_model, f"{name} 缺少 llm_model"


def test_all_providers_are_provider_preset_instances():
    for name, preset in PROVIDER_PRESETS.items():
        assert isinstance(preset, ProviderPreset), f"{name} 不是 ProviderPreset 实例"


def test_provider_count():
    """预设服务商数量为 8"""
    assert len(PROVIDER_PRESETS) == 8


def test_canonical_names():
    """所有预设的 canonical_name 符合预期"""
    expected = {
        "siliconflow", "minimax", "openai", "deepseek",
        "zhipu", "moonshot", "ollama", "custom",
    }
    assert set(PROVIDER_PRESETS.keys()) == expected


def test_deepseek_no_embedding():
    """DeepSeek 没有 embedding 模型"""
    preset = get_provider_preset("deepseek")
    assert preset.embedding_model == ""


def test_minimax_preset():
    preset = get_provider_preset("minimax")
    assert preset.llm_model == "MiniMax-M2.7"
    assert preset.embedding_model == "minimax-embedding"
