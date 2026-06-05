from tests.conftest import insert_test_knowledge


def test_stream_context_sources_resolve_title_from_page_id_metadata():
    from src.services.rag_pipeline import RAGService

    kid = insert_test_knowledge(
        title="Q3 Operating Review",
        content="Project Alpha revenue grew 20%",
        item_id="source-title-page",
    )

    _, sources = RAGService()._build_context([
        {
            "id": "block-1",
            "text": "Project Alpha revenue grew 20%",
            "metadata": {"page_id": kid, "block_id": "block-1"},
            "rerank_score": 0.8,
        }
    ])

    assert sources[0]["knowledge_id"] == kid
    assert sources[0]["title"] == "Q3 Operating Review"
    assert sources[0]["block_id"] == "block-1"
