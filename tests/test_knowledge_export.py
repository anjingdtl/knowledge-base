"""Tests for knowledge Markdown export helpers."""

from src.gui.knowledge_view import _knowledge_to_markdown, _safe_md_filename


def test_safe_md_filename_removes_windows_invalid_characters():
    name = _safe_md_filename('第七届:创智杯/团队奖?* "规则"', "abc123456")

    assert name == "第七届_创智杯_团队奖_ _规则.md"


def test_safe_md_filename_uses_short_id_for_empty_title():
    assert _safe_md_filename("   ???   ", "abcdef123") == "untitled-abcdef12.md"


def test_knowledge_to_markdown_includes_metadata_and_content():
    md = _knowledge_to_markdown({
        "title": "创智杯规则",
        "tags": '["竞赛", "团队奖"]',
        "source_type": "file",
        "source_path": "D:/docs/rules.pdf",
        "file_type": "pdf",
        "created_at": "2026-06-01T08:00:00",
        "content": "省份最高团队奖个数为 3 个。",
    })

    assert "# 创智杯规则" in md
    assert "- 标签: 竞赛, 团队奖" in md
    assert "- 来源: D:/docs/rules.pdf" in md
    assert "省份最高团队奖个数为 3 个。" in md
