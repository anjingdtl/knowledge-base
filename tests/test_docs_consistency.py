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
