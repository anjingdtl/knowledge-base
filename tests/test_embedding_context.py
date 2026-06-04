"""Sprint 5 acceptance tests for embedding-time contextual headers."""
from __future__ import annotations

from datetime import datetime

from src.services.db import Database
from src.utils.config import Config
from tests.conftest import insert_test_block, insert_test_knowledge


def _now() -> str:
    return datetime.now().isoformat()


def _link_block_to_knowledge(block_id: str, target_id: str) -> None:
    Database.get_conn().execute(
        """INSERT INTO entity_refs
           (id, source_type, source_id, target_type, target_id, ref_type, weight,
            auto_discovered, created_at)
           VALUES (?, 'block', ?, 'knowledge', ?, 'link', 1.0, 0, ?)""",
        (f"ref-{block_id}-{target_id}", block_id, target_id, _now()),
    )
    Database.get_conn().commit()


def test_embedding_text_uses_parent_chain_and_links_without_mutating_block_content():
    Config.set("rag.embedding_context.enabled", True)
    Config.set("rag.embedding_context.include_parent_chain", True)
    Config.set("rag.embedding_context.include_links", True)
    Config.set("rag.embedding_context.include_siblings", False)
    Config.set("rag.embedding_context.max_chars", 1200)

    page_id = insert_test_knowledge(
        title="Architecture notes",
        content="Page original content",
        item_id="page-embedding-context",
    )
    linked_id = insert_test_knowledge(
        title="Vector index design",
        content="Linked summary should be visible to embedding.",
        item_id="linked-embedding-context",
    )
    parent_id = insert_test_block(
        page_id,
        content="Parent heading: Retrieval",
        block_id="parent-embedding-context",
        order_idx=0,
    )
    insert_test_block(
        page_id,
        content="Sibling text must not appear",
        block_id="sibling-embedding-context",
        order_idx=1,
    )
    child_id = insert_test_block(
        page_id,
        content="Child block: use contextual headers",
        block_id="child-embedding-context",
        parent_id=parent_id,
        order_idx=0,
    )
    _link_block_to_knowledge(child_id, linked_id)

    from src.services.embedding import EmbeddingService

    original = Database.get_block(child_id)["content"]
    embedding_text = EmbeddingService().build_embedding_text(Database.get_block(child_id))

    assert "Parent heading: Retrieval" in embedding_text
    assert "Child block: use contextual headers" in embedding_text
    assert "Vector index design" in embedding_text
    assert "Linked summary should be visible" in embedding_text
    assert "Sibling text must not appear" not in embedding_text
    assert Database.get_block(child_id)["content"] == original


def test_read_can_include_blocks_and_embedding_preview():
    Config.set("rag.embedding_context.enabled", True)
    Config.set("rag.embedding_context.include_parent_chain", True)
    Config.set("rag.embedding_context.include_links", False)
    Config.set("rag.embedding_context.include_siblings", False)

    page_id = insert_test_knowledge(
        title="Read preview page",
        content="Stored page content",
        item_id="read-preview-page",
    )
    parent_id = insert_test_block(
        page_id,
        content="Preview parent",
        block_id="read-preview-parent",
        order_idx=0,
    )
    child_id = insert_test_block(
        page_id,
        content="Preview child",
        block_id="read-preview-child",
        parent_id=parent_id,
        order_idx=0,
    )

    from src.mcp_server import read

    result = read(
        item_id=page_id,
        include_blocks=True,
        include_embedding_preview=True,
    )

    assert result["ok"] is True
    blocks = result["data"]["blocks"]
    child = next(block for block in blocks if block["id"] == child_id)
    assert child["content"] == "Preview child"
    assert "embedding_preview" in child
    assert "Preview parent" in child["embedding_preview"]["text"]
    assert "Preview child" in child["embedding_preview"]["text"]
    assert child["embedding_preview"]["enabled"] is True


def test_reindex_dry_run_reports_embedding_context_counts_without_writing_vectors(monkeypatch):
    Config.set("rag.embedding_context.enabled", True)

    page_id = insert_test_knowledge(
        title="Dry run page",
        content="Dry run page content",
        item_id="dry-run-page",
    )
    insert_test_block(page_id, content="First existing block", block_id="dry-run-block-1")
    insert_test_block(page_id, content="Second existing block", block_id="dry-run-block-2")

    from src.mcp_server import reindex_all

    def fail_if_embedding_runs(*args, **kwargs):
        raise AssertionError("dry_run must not generate embeddings")

    monkeypatch.setattr(
        "src.services.embedding.EmbeddingService.embed_batch_with_cache",
        fail_if_embedding_runs,
    )

    before_blocks = Database.get_conn().execute(
        "SELECT COUNT(*) AS cnt FROM blocks WHERE page_id = ?", (page_id,)
    ).fetchone()["cnt"]

    result = reindex_all(dry_run=True)

    after_blocks = Database.get_conn().execute(
        "SELECT COUNT(*) AS cnt FROM blocks WHERE page_id = ?", (page_id,)
    ).fetchone()["cnt"]
    would_change = result["data"]["would_change"]
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert would_change["affected_items"] == 1
    assert would_change["affected_blocks"] == 2
    assert would_change["embedding_context_enabled"] is True
    assert would_change["estimated_batches"] >= 1
    assert before_blocks == after_blocks
