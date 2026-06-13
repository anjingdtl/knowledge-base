"""CitationBuilder 单元测试"""
from __future__ import annotations

from unittest.mock import Mock

from src.services.citation_builder import CitationBuilder


class TestCitationBasic:
    """Basic citation from candidate with metadata."""

    def test_citation_basic(self):
        """基本引用构建：包含文档标题、block_id、分数。"""
        db = Mock()
        db.get_knowledge.return_value = {"title": "设计文档"}

        builder = CitationBuilder(db)
        candidate = {
            "id": "block-001",
            "text": "这是检索到的文本片段",
            "metadata": {"page_id": "k1", "block_id": "block-001"},
            "rerank_score": 0.92,
            "rrf_score": 0.85,
            "vector_score": 0.80,
            "keyword_score": 0.60,
            "match_channels": ["semantic", "keyword"],
        }

        citation = builder.build(candidate, item={"title": "设计文档"})

        assert citation.document == "设计文档"
        assert citation.block_id == "block-001"
        assert citation.knowledge_id == "k1"
        assert citation.score == 0.92  # rerank_score 优先
        assert citation.text == "这是检索到的文本片段"
        assert "semantic" in citation.match_channels
        assert "keyword" in citation.match_channels

    def test_citation_to_dict(self):
        """to_dict 序列化包含所有必需字段。"""
        builder = CitationBuilder(Mock())
        candidate = {
            "id": "block-002",
            "text": "序列化测试",
            "metadata": {"page_id": "k2"},
            "rrf_score": 0.7,
        }
        citation = builder.build(candidate)
        d = citation.to_dict()

        assert "document" in d
        assert "path" in d
        assert "knowledge_id" in d
        assert "block_id" in d
        assert "location" in d
        assert "score" in d
        assert "score_breakdown" in d
        assert "match_channels" in d
        assert "reason" in d
        assert "text" in d


class TestCitationLocationPDF:
    """PDF citation has page number."""

    def test_citation_location_pdf(self):
        """PDF 引用包含 page 编号。"""
        builder = CitationBuilder(Mock())
        candidate = {
            "id": "block-pdf-1",
            "text": "PDF 文本",
            "metadata": {"page_id": "k-pdf", "page": 42},
            "rrf_score": 0.5,
        }
        citation = builder.build(candidate)

        assert citation.location.page == 42
        assert citation.location.sheet is None
        assert citation.location.slide is None


class TestCitationLocationMarkdown:
    """Markdown citation has heading_path and paragraph_index."""

    def test_citation_location_markdown(self):
        """Markdown 引用包含 heading_path 和 paragraph_index。"""
        builder = CitationBuilder(Mock())
        candidate = {
            "id": "block-md-1",
            "text": "Markdown 文本",
            "metadata": {
                "page_id": "k-md",
                "heading_path": ["# 概述", "## 架构"],
                "paragraph_index": 3,
            },
            "rrf_score": 0.6,
        }
        citation = builder.build(candidate)

        assert citation.location.heading_path == ["# 概述", "## 架构"]
        assert citation.location.paragraph_index == 3


class TestCitationLocationExcel:
    """Excel citation has sheet name."""

    def test_citation_location_excel(self):
        """Excel 引用包含 sheet 名称。"""
        builder = CitationBuilder(Mock())
        candidate = {
            "id": "block-xlsx-1",
            "text": "表格数据",
            "metadata": {"page_id": "k-xlsx", "sheet": "Q3营收"},
            "keyword_score": 0.75,
        }
        citation = builder.build(candidate)

        assert citation.location.sheet == "Q3营收"
        assert citation.location.page is None


class TestCitationScoreBreakdown:
    """Citation includes all score components."""

    def test_citation_score_breakdown(self):
        """引用包含各阶段分数细分。"""
        builder = CitationBuilder(Mock())
        candidate = {
            "id": "block-score-1",
            "text": "分数测试",
            "metadata": {"page_id": "k-score"},
            "vector_score": 0.85,
            "keyword_score": 0.60,
            "rrf_score": 0.90,
            "rerank_score": 0.95,
        }
        citation = builder.build(candidate)

        assert citation.score_breakdown["vector"] == 0.85
        assert citation.score_breakdown["keyword"] == 0.60
        assert citation.score_breakdown["rrf"] == 0.90
        assert citation.score_breakdown["rerank"] == 0.95
        # final score = rerank_score (highest priority)
        assert citation.score == 0.95

    def test_score_breakdown_with_none(self):
        """部分分数缺失时，breakdown 中为 None。"""
        builder = CitationBuilder(Mock())
        candidate = {
            "id": "block-partial",
            "text": "部分分数",
            "metadata": {"page_id": "k-partial"},
            "vector_score": 0.8,
        }
        citation = builder.build(candidate)

        assert citation.score_breakdown["vector"] == 0.8
        assert citation.score_breakdown["keyword"] is None
        assert citation.score_breakdown["rrf"] is None
        assert citation.score_breakdown["rerank"] is None
        assert citation.score == 0.8  # 使用 vector_score


class TestCitationDedup:
    """build_many deduplicates by block_id."""

    def test_citation_dedup_by_block_id(self):
        """build_many 按 block_id 去重。"""
        db = Mock()
        db.get_knowledge.return_value = {"title": "Test"}

        builder = CitationBuilder(db)
        candidates = [
            {"id": "block-A", "text": "text A", "metadata": {"page_id": "k1"}, "rrf_score": 0.9},
            {"id": "block-A", "text": "text A dup", "metadata": {"page_id": "k1"}, "rrf_score": 0.85},
            {"id": "block-B", "text": "text B", "metadata": {"page_id": "k1"}, "rrf_score": 0.8},
        ]

        citations = builder.build_many(candidates, max_per_doc=10)

        block_ids = [c.block_id for c in citations]
        assert block_ids.count("block-A") == 1
        assert "block-B" in block_ids


class TestCitationPerDocumentLimit:
    """build_many respects max_per_doc."""

    def test_citation_per_document_limit(self):
        """build_many 遵守每文档最大引用数限制。"""
        db = Mock()
        db.get_knowledge.return_value = {"title": "Test"}

        builder = CitationBuilder(db)
        candidates = [
            {"id": f"block-{i}", "text": f"text {i}", "metadata": {"page_id": "same-doc"}, "rrf_score": 0.9 - i * 0.1}
            for i in range(5)
        ]

        citations = builder.build_many(candidates, max_per_doc=3)

        assert len(citations) == 3
        # 前 3 个（分数最高的）被保留
        assert citations[0].block_id == "block-0"
        assert citations[1].block_id == "block-1"
        assert citations[2].block_id == "block-2"

    def test_per_document_limit_different_docs(self):
        """不同文档分别计数。"""
        db = Mock()
        db.get_knowledge.return_value = {"title": "Test"}

        builder = CitationBuilder(db)
        candidates = [
            {"id": "a1", "text": "a1", "metadata": {"page_id": "doc-A"}, "rrf_score": 0.9},
            {"id": "a2", "text": "a2", "metadata": {"page_id": "doc-A"}, "rrf_score": 0.8},
            {"id": "b1", "text": "b1", "metadata": {"page_id": "doc-B"}, "rrf_score": 0.85},
            {"id": "b2", "text": "b2", "metadata": {"page_id": "doc-B"}, "rrf_score": 0.75},
        ]

        citations = builder.build_many(candidates, max_per_doc=1)

        docs = [c.knowledge_id for c in citations]
        assert docs.count("doc-A") == 1
        assert docs.count("doc-B") == 1
        assert len(citations) == 2


class TestCitationMatchReason:
    """Citation reason reflects match channels and reranking."""

    def test_reason_semantic_keyword_reranked(self):
        """semantic + keyword match; reranked."""
        builder = CitationBuilder(Mock())
        candidate = {
            "id": "b1",
            "text": "test",
            "metadata": {"page_id": "k1"},
            "vector_score": 0.8,
            "keyword_score": 0.6,
            "rerank_score": 0.95,
            "match_channels": ["semantic", "keyword"],
        }
        citation = builder.build(candidate)
        assert "semantic" in citation.reason
        assert "keyword" in citation.reason
        assert "reranked" in citation.reason

    def test_reason_semantic_only(self):
        """仅 semantic match。"""
        builder = CitationBuilder(Mock())
        candidate = {
            "id": "b2",
            "text": "test",
            "metadata": {"page_id": "k2"},
            "vector_score": 0.8,
            "match_channels": ["semantic"],
        }
        citation = builder.build(candidate)
        assert "semantic" in citation.reason
        assert "keyword" not in citation.reason
