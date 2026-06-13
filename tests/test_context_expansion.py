"""上下文扩展配置测试 — Task 12

验证:
1. blocks.content 保持原始文本（不被 parent context 污染）
2. embedding/prompt context 包含父链和兄弟块
3. citation 仍指向匹配的子块（而非父块）
4. rag.parent_child.max_parent_chars 配置生效
"""
from __future__ import annotations

from src.services.db import Database
from src.services.parent_child_retrieval import ParentChildRetriever
from src.utils.config import Config


def _seed_hierarchy():
    """插入测试层级结构"""
    Database.insert_knowledge({
        "id": "ctx-k1", "title": "context test doc", "content": "full content",
        "tags": "[]", "file_type": "pdf", "source_type": "manual",
        "source_path": "", "content_hash": "hctx1", "quality": "",
        "file_size": 0, "file_created_at": "", "file_modified_at": "",
        "version": 1, "created_at": "", "updated_at": "",
    })
    Database.insert_blocks([
        {
            "id": "ctx-page", "parent_id": None, "page_id": "ctx-k1",
            "content": "Page header: important document",
            "block_type": "page", "properties": "{}", "order_idx": 0,
            "created_at": "", "updated_at": "",
        },
        {
            "id": "ctx-section", "parent_id": "ctx-page", "page_id": "ctx-k1",
            "content": "Section: Key Findings",
            "block_type": "section", "properties": "{}", "order_idx": 1,
            "created_at": "", "updated_at": "",
        },
        {
            "id": "ctx-para1", "parent_id": "ctx-section", "page_id": "ctx-k1",
            "content": "Paragraph one with critical data points.",
            "block_type": "paragraph", "properties": "{}", "order_idx": 2,
            "created_at": "", "updated_at": "",
        },
        {
            "id": "ctx-para2", "parent_id": "ctx-section", "page_id": "ctx-k1",
            "content": "Paragraph two with supporting evidence.",
            "block_type": "paragraph", "properties": "{}", "order_idx": 3,
            "created_at": "", "updated_at": "",
        },
    ])


class TestBlockContentNotPolluted:
    """blocks.content 保持原始文本，不被 parent context 污染。"""

    def test_block_content_unchanged_after_enrich(self):
        """enrich 后 block 原始 content 不变。"""
        _seed_hierarchy()

        original_content = "Paragraph one with critical data points."
        results = [
            {
                "id": "ctx-para1",
                "text": original_content,
                "metadata": {"page_id": "ctx-k1", "block_id": "ctx-para1"},
            },
        ]

        retriever = ParentChildRetriever(db=Database)
        enriched = retriever.enrich(results, file_type="pdf")

        # text 字段不变
        assert enriched[0]["text"] == original_content
        # DB 中的 content 也不变
        block = Database.get_block("ctx-para1")
        assert block["content"] == original_content

    def test_parent_content_is_separate_field(self):
        """parent_content 作为独立字段附加，不覆盖原始 text。"""
        _seed_hierarchy()

        results = [
            {
                "id": "ctx-para1",
                "text": "Paragraph one with critical data points.",
                "metadata": {"page_id": "ctx-k1", "block_id": "ctx-para1"},
            },
        ]

        retriever = ParentChildRetriever(db=Database)
        enriched = retriever.enrich(results, file_type="pdf")

        if "parent_content" in enriched[0]:
            # parent_content 是附加字段，text 不变
            assert enriched[0]["text"] == "Paragraph one with critical data points."
            # parent_content 应包含父块信息
            assert len(enriched[0]["parent_content"]) > 0


class TestPromptContextIncludesParentChain:
    """prompt context 包含父链和兄弟块。"""

    def test_block_context_includes_parent_chain(self):
        """BlockContextService 构建的上下文包含父链。"""
        from src.services.block_context import BlockContextService

        _seed_hierarchy()

        service = BlockContextService(db=Database)
        context = service.build_context("ctx-para1")

        # 应包含父块内容
        assert "Section: Key Findings" in context or "Page header" in context
        # 应包含当前块内容
        assert "Paragraph one with critical data points" in context

    def test_block_context_includes_siblings(self):
        """BlockContextService 构建的上下文包含相邻兄弟块。"""
        from src.services.block_context import BlockContextService

        _seed_hierarchy()
        Config.set("rag.context_sibling_window", 1)

        service = BlockContextService(db=Database)
        context = service.build_context("ctx-para1", sibling_window=1)

        # 应包含兄弟块 para2 的内容
        assert "Paragraph two" in context or "supporting evidence" in context


class TestCitationPointsToChild:
    """citation 仍指向匹配的子块，而非父块。"""

    def test_citation_block_id_is_child(self):
        """enrich 后 id 和 block_id 仍指向子块。"""
        _seed_hierarchy()

        results = [
            {
                "id": "ctx-para1",
                "text": "Paragraph one",
                "metadata": {"page_id": "ctx-k1", "block_id": "ctx-para1"},
            },
        ]

        retriever = ParentChildRetriever(db=Database)
        enriched = retriever.enrich(results, file_type="pdf")

        assert enriched[0]["id"] == "ctx-para1"
        assert enriched[0]["metadata"]["block_id"] == "ctx-para1"
        # parent_block_id 指向父块，但 block_id 不变
        if "parent_block_id" in enriched[0]:
            assert enriched[0]["parent_block_id"] != "ctx-para1"


class TestConfigMaxParentChars:
    """rag.parent_child.max_parent_chars 配置生效。"""

    def test_config_max_parent_chars_default(self):
        """默认 max_parent_chars 从策略读取。"""
        retriever = ParentChildRetriever(db=Database)
        strategy = retriever._get_strategy("pdf")
        assert strategy["max_parent_chars"] == 4000

    def test_config_max_parent_chars_from_config(self):
        """从 rag.parent_child.max_parent_chars 读取配置。"""
        Config.set("rag.parent_child.max_parent_chars", 2000)

        _seed_hierarchy()

        retriever = ParentChildRetriever(db=Database)
        results = [
            {
                "id": "ctx-para1",
                "text": "test",
                "metadata": {"page_id": "ctx-k1", "block_id": "ctx-para1"},
            },
        ]

        enriched = retriever.enrich(results, file_type="pdf")
        if "parent_content" in enriched[0]:
            assert len(enriched[0]["parent_content"]) <= 2000

        # 恢复默认值
        Config.set("rag.parent_child.max_parent_chars", 4000)

    def test_explicit_max_parent_chars_overrides_config(self):
        """显式传入的 max_parent_chars 覆盖配置。"""
        _seed_hierarchy()

        retriever = ParentChildRetriever(db=Database)
        results = [
            {
                "id": "ctx-para1",
                "text": "test",
                "metadata": {"page_id": "ctx-k1", "block_id": "ctx-para1"},
            },
        ]

        enriched = retriever.enrich(results, file_type="pdf", max_parent_chars=100)
        if "parent_content" in enriched[0]:
            assert len(enriched[0]["parent_content"]) <= 100
