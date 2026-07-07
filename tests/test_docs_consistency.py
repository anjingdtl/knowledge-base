"""文档与 config.example.yaml 一致性测试(spec S7)。"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _example_config():
    with open(ROOT / "config.example.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_readme_default_profile_matches_config():
    """README 不再说 'Default core profile'(应为 extended)。"""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Default `core` profile" not in text, "README 仍称默认 core profile"
    assert "extended" in text


def test_readme_profile_mentioned_matches_config():
    """README 提及的默认 profile 与 config.example 一致。"""
    cfg = _example_config()
    expected = cfg["mcp"]["tool_profile"]  # extended
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert expected in text, f"README 未提及默认 profile '{expected}'"


def test_advanced_features_documents_phase2_capabilities():
    """advanced-features.md 含规模自适应 / wiki parent / 中文 lexical 三章(Phase2 W4 4.3)。"""
    text = (ROOT / "docs" / "advanced-features.md").read_text(encoding="utf-8")
    assert "规模自适应" in text or "size-aware" in text.lower()
    assert "wiki parent" in text.lower() or "parent-child" in text.lower() or "父上下文" in text
    assert "中文 lexical" in text or "lexical_zh" in text or "lexical zh" in text.lower()


def test_advanced_features_config_keys_match_example():
    """advanced-features.md 提及的配置键与 config.example.yaml 一致(不漂移)。"""
    cfg = _example_config()
    rag = cfg.get("rag", {})
    text = (ROOT / "docs" / "advanced-features.md").read_text(encoding="utf-8")
    for key in ("size_aware", "wiki_parent_child", "lexical_zh", "wiki_read"):
        assert key in rag, f"config.example.yaml 缺 rag.{key}"
        assert key in text, f"advanced-features.md 未提及 rag.{key}"


def test_advanced_features_documents_dual_track():
    """advanced-features.md 含双轨 wiki 协作章节(双轨收敛 Task 5)。"""
    text = (ROOT / "docs" / "advanced-features.md").read_text(encoding="utf-8")
    assert "双轨" in text and "Wiki 协作" in text
    assert "WikiWriteService" in text
    assert "sqlite_fallback" in text
