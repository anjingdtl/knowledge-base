"""测试 Parent-Child Retrieval"""
import pytest
from src.services.parent_child_retrieval import (
    ParentChildRetriever, ParentBlock,
    BLOCK_STRATEGIES, DEFAULT_STRATEGY,
    enrich_with_parent_context,
)
from src.services.db import Database


def _seed_hierarchy():
    """插入 k1 -> page_block -> section_block -> paragraph_block 的层级结构"""
    Database.insert_knowledge({
        "id": "k1", "title": "test doc", "content": "full content",
        "tags": "[]", "file_type": "pdf", "source_type": "manual",
        "source_path": "", "content_hash": "h1", "quality": "",
        "file_size": 0, "file_created_at": "", "file_modified_at": "",
        "version": 1, "created_at": "", "updated_at": "",
    })
    Database.insert_blocks([
        # page 级父块
        {"id": "b_page1", "parent_id": None, "page_id": "k1",
         "content": "Page 1 of the document", "block_type": "page",
         "properties": "{}", "order_idx": 0, "created_at": "", "updated_at": ""},
        # section 级
        {"id": "b_sec1", "parent_id": "b_page1", "page_id": "k1",
         "content": "Section 1: Introduction", "block_type": "section",
         "properties": "{}", "order_idx": 1, "created_at": "", "updated_at": ""},
        # paragraph 级（子块 — 实际被检索命中的）
        {"id": "b_para1", "parent_id": "b_sec1", "page_id": "k1",
         "content": "This is the first paragraph with important details.",
         "block_type": "paragraph", "properties": "{}", "order_idx": 2,
         "created_at": "", "updated_at": ""},
        {"id": "b_para2", "parent_id": "b_sec1", "page_id": "k1",
         "content": "Second paragraph with more info.",
         "block_type": "paragraph", "properties": "{}", "order_idx": 3,
         "created_at": "", "updated_at": ""},
    ])


class TestBlockStrategies:
    def test_known_file_types(self):
        for ft in ["pdf", "docx", "xlsx", "pptx", "md", "txt"]:
            assert ft in BLOCK_STRATEGIES
            s = BLOCK_STRATEGIES[ft]
            assert "parent" in s
            assert "child" in s
            assert "parent_block_types" in s

    def test_strategy_has_parent_block_types(self):
        for ft, s in BLOCK_STRATEGIES.items():
            assert isinstance(s["parent_block_types"], set)
            assert len(s["parent_block_types"]) > 0

    def test_get_strategy_exact_match(self):
        retriever = ParentChildRetriever(db=None)
        assert retriever._get_strategy("pdf") is BLOCK_STRATEGIES["pdf"]
        assert retriever._get_strategy("docx") is BLOCK_STRATEGIES["docx"]

    def test_get_strategy_unknown(self):
        retriever = ParentChildRetriever(db=None)
        assert retriever._get_strategy("xyz") is DEFAULT_STRATEGY


class TestParentChildRetriever:
    def test_enrich_empty_results(self):
        retriever = ParentChildRetriever(db=Database)
        result = retriever.enrich([])
        assert result == []

    def test_enrich_finds_section_parent(self):
        _seed_hierarchy()
        retriever = ParentChildRetriever(db=Database)
        results = [
            {
                "id": "b_para1",
                "text": "This is the first paragraph",
                "metadata": {"page_id": "k1", "block_id": "b_para1"},
            },
        ]
        enriched = retriever.enrich(results, file_type="pdf")
        assert len(enriched) == 1
        # 应该找到 section 或 page 级父块
        assert "parent_content" in enriched[0]
        assert enriched[0]["parent_content"] != ""

    def test_enrich_no_parent(self):
        """顶级 block（无 parent_id）不应报错"""
        _seed_hierarchy()
        retriever = ParentChildRetriever(db=Database)
        results = [
            {
                "id": "b_page1",
                "text": "Page 1",
                "metadata": {"page_id": "k1", "block_id": "b_page1"},
            },
        ]
        enriched = retriever.enrich(results, file_type="pdf")
        # 顶级块自身可能无父块，不应崩溃
        assert len(enriched) == 1

    def test_enrich_multiple_results(self):
        _seed_hierarchy()
        retriever = ParentChildRetriever(db=Database)
        results = [
            {
                "id": "b_para1",
                "text": "paragraph 1",
                "metadata": {"page_id": "k1", "block_id": "b_para1"},
            },
            {
                "id": "b_para2",
                "text": "paragraph 2",
                "metadata": {"page_id": "k1", "block_id": "b_para2"},
            },
        ]
        enriched = retriever.enrich(results, file_type="pdf")
        assert len(enriched) == 2

    def test_citation_still_points_to_child(self):
        """citation 应定位到子块而非父块"""
        _seed_hierarchy()
        retriever = ParentChildRetriever(db=Database)
        results = [
            {
                "id": "b_para1",
                "text": "paragraph text",
                "metadata": {"page_id": "k1", "block_id": "b_para1"},
            },
        ]
        enriched = retriever.enrich(results, file_type="pdf")
        # id 和 metadata.block_id 仍指向子块
        assert enriched[0]["id"] == "b_para1"
        assert enriched[0]["metadata"]["block_id"] == "b_para1"


class TestEnrichConvenienceFunction:
    def test_function_works(self):
        _seed_hierarchy()
        results = [
            {
                "id": "b_para1",
                "text": "test",
                "metadata": {"page_id": "k1", "block_id": "b_para1"},
            },
        ]
        enriched = enrich_with_parent_context(results, db=Database, file_type="pdf")
        assert len(enriched) == 1
