"""Service group view tests."""
from unittest.mock import MagicMock

from src.core.service_groups import ServiceGroups


def test_service_groups_views_delegate_to_container():
    c = MagicMock()
    c.config = "cfg"
    c.db = "db"
    c.vectorstore = "vs"
    c.block_store = "bs"
    c.embedding = "emb"
    c.llm = "llm"
    c.knowledge_repo = "kr"
    c.block_repo = "br"
    c.search_service = "ss"
    c.path_indexer = "pi"
    c.wiki_repository = "wr"
    c.wiki_serving_gate = "wg"
    c.wiki_query_service = "wq"
    c.wiki_write_service = "ww"
    c.wiki_claim_extractor = "wce"
    c.wiki_projection = "wp"
    c.wiki_maintenance_service = "wm"
    c.wiki_rebuild_service = "wrb"
    c.maintenance_policy = "mp"
    c.graph_backend = "gb"
    c.graph_builder = "gbuild"
    c.unified_graph = "ug"
    c.agent_memory = "am"

    g = ServiceGroups(c)
    assert g.core.search_service == "ss"
    assert g.verified.wiki_serving_gate == "wg"
    assert g.authoring.wiki_write_service == "ww"
    assert g.experimental.agent_memory == "am"
