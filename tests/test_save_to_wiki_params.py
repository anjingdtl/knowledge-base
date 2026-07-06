"""第6轮 BUG#10 / BUG#11 回归测试：save_answer 的 auto_publish 与 enhance 参数。

BUG#10：auto_publish=False 时页面应为 draft（而非 published），可走审核流。
BUG#11：enhance=False 时跳过 LLM，直接用原始 answer 存储。
"""
import json

import pytest

from src.services.wiki_compiler import WikiCompiler


class _FakeLLM:
    """假 LLM：返回固定的增强 JSON。"""

    def chat(self, messages, silent=False):
        return json.dumps({
            "title": "增强标题",
            "content": "这是增强后的内容",
            "tags": ["增强"],
            "summary": "增强摘要",
        })


@pytest.fixture
def wiki_compiler(monkeypatch):
    """WikiCompiler with mocked LLM."""
    monkeypatch.setattr("src.services.wiki_compiler.LLMService", lambda: _FakeLLM())
    return WikiCompiler()


def _long_answer():
    """长度超过 wiki.query_save_min_length(默认100) 的回答。"""
    return "这是一段足够长的回答内容用于测试保存。" * 10


def test_save_to_wiki_with_auto_publish_false_creates_draft(wiki_compiler):
    """BUG#10：auto_publish=False → 页面 status='draft'。"""
    from src.services.db import Database

    page_id = wiki_compiler.save_answer(
        "测试问题",
        _long_answer(),
        auto_publish=False,
    )
    assert page_id is not None

    page = Database.get_wiki_page(page_id)
    assert page is not None
    assert page["status"] == "draft", (
        f"auto_publish=False 应创建 draft，实际 status={page['status']!r}"
    )


def test_save_to_wiki_with_auto_publish_true_creates_published(wiki_compiler):
    """BUG#10：auto_publish=True → 页面 status='published'（向后兼容）。"""
    from src.services.db import Database

    page_id = wiki_compiler.save_answer(
        "测试问题",
        _long_answer(),
        auto_publish=True,
    )
    assert page_id is not None

    page = Database.get_wiki_page(page_id)
    assert page is not None
    assert page["status"] == "published"


def test_save_to_wiki_auto_publish_none_follows_config(wiki_compiler, monkeypatch):
    """BUG#10：auto_publish=None（默认）→ 沿用 Config 'wiki.auto_publish'。"""
    from src.services.db import Database
    from src.utils.config import Config

    Config.set("wiki.auto_publish", False)
    try:
        page_id = wiki_compiler.save_answer("测试问题", _long_answer())
        assert page_id is not None
        page = Database.get_wiki_page(page_id)
        assert page["status"] == "draft"
    finally:
        Config.set("wiki.auto_publish", True)


def test_save_to_wiki_skip_enhance_stores_raw(wiki_compiler):
    """BUG#11：enhance=False → 跳过 LLM，存原始 answer，title 取 question 前 N 字。"""
    from src.services.db import Database

    raw_answer = _long_answer()
    question = "如何配置 CDN 加速"
    page_id = wiki_compiler.save_answer(
        question,
        raw_answer,
        enhance=False,
    )
    assert page_id is not None

    page = Database.get_wiki_page(page_id)
    assert page is not None
    # 内容应为原始 answer，而非 LLM 增强后的
    assert page["content"] == raw_answer
    # title 应来自 question（截断），而非 LLM 的 "增强标题"
    assert question in page["title"] or page["title"].startswith(question[:5])
    assert page["title"] != "增强标题"


def test_save_to_wiki_skip_enhance_auto_publish_false(wiki_compiler):
    """BUG#10+#11 联用：enhance=False + auto_publish=False → draft + 原始内容。"""
    from src.services.db import Database

    raw_answer = _long_answer()
    page_id = wiki_compiler.save_answer(
        "原始问题",
        raw_answer,
        enhance=False,
        auto_publish=False,
    )
    assert page_id is not None

    page = Database.get_wiki_page(page_id)
    assert page["status"] == "draft"
    assert page["content"] == raw_answer
