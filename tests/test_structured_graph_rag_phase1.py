import json

from src.models.knowledge import KnowledgeItem
from src.models.parsed_content import StructuredBlock
from src.services.db import Database


def _insert_item(title: str, content: str, tags=None, item_id: str | None = None) -> KnowledgeItem:
    if item_id is None:
        item = KnowledgeItem(title=title, content=content, tags=tags or [])
    else:
        item = KnowledgeItem(id=item_id, title=title, content=content, tags=tags or [])
    Database.insert_knowledge(item.to_row())
    return item


def _insert_block(
    block_id: str,
    page_id: str,
    content: str,
    parent_id: str | None = None,
    order_idx: int = 0,
    properties: dict | None = None,
):
    Database.insert_blocks([
        {
            "id": block_id,
            "parent_id": parent_id,
            "page_id": page_id,
            "content": content,
            "block_type": "text",
            "properties": json.dumps(properties or {"knowledge_id": page_id}, ensure_ascii=False),
            "order_idx": order_idx,
            "created_at": "2026-06-04T00:00:00",
            "updated_at": "2026-06-04T00:00:00",
        }
    ])
    # Phase 2: keep effective_property_index in sync so query_router finds properties
    try:
        from src.services.effective_properties import EffectivePropertyService
        EffectivePropertyService(db=Database).refresh_block(block_id)
    except Exception:
        pass


def test_mcp_ingest_file_uses_structured_blocks_when_parser_provides_them(tmp_path, monkeypatch):
    import src.mcp_server as mcp_mod
    from src.services.file_parser import ParsedFile

    file_path = tmp_path / "tasks.csv"
    file_path.write_text("ignored", encoding="utf-8")
    structured = [
        StructuredBlock(
            content="任务 A",
            block_type="table_row",
            properties={"columns": "任务 | 状态", "sheet": "Sheet1"},
            children=[
                StructuredBlock(
                    content="状态:: unresolved",
                    block_type="property",
                    properties={"column": "状态", "value": "unresolved"},
                )
            ],
        )
    ]
    parsed = ParsedFile(
        title="Tasks",
        content="flat text should not become the only block",
        file_type="csv",
        source_path=str(file_path),
        metadata={},
        structured=structured,
    )

    monkeypatch.setattr(mcp_mod, "parse_file", lambda _: [parsed])
    monkeypatch.setattr(mcp_mod, "_try_wiki_compile", lambda item_id: None)
    monkeypatch.setattr(mcp_mod, "_container", None)

    result = mcp_mod._do_ingest_file(str(file_path), tags=["bug"])

    rows = Database.get_conn().execute(
        "SELECT id, parent_id, content, properties FROM blocks WHERE page_id = ? ORDER BY order_idx",
        (result["id"],),
    ).fetchall()
    assert [row["content"] for row in rows] == ["任务 A", "状态:: unresolved"]
    assert rows[1]["parent_id"] == rows[0]["id"]
    assert json.loads(rows[0]["properties"])["columns"] == "任务 | 状态"


def test_hybrid_keyword_search_preserves_block_id_and_expands_parent_and_siblings():
    from src.services.hybrid_search import HybridSearcher
    from src.utils.config import Config

    Config.set("rag.search_mode", "keywords")
    Config.set("rag.context_trace_depth", 2)
    Config.set("rag.context_sibling_window", 1)
    page = _insert_item("Q3 复盘", "outline")
    _insert_block("parent", page.id, "2025 年 Q3 整体表现", order_idx=0)
    _insert_block("before", page.id, "背景：华东区域", parent_id="parent", order_idx=1)
    _insert_block("hit", page.id, "结论：营收增长 20%，unique-needle", parent_id="parent", order_idx=2)
    _insert_block("after", page.id, "原因：续费率提升", parent_id="parent", order_idx=3)
    Database.insert_blocks_fts([
        {
            "id": "hit",
            "page_id": page.id,
            "content": "结论：营收增长 20%，unique-needle",
        }
    ])

    results = HybridSearcher().search(["unique-needle"], top_k=3)

    hit = next(r for r in results if r.get("id") == "hit")
    assert hit["metadata"]["page_id"] == page.id
    assert "2025 年 Q3 整体表现" in hit["block_context"]
    assert "背景：华东区域" in hit["block_context"]
    assert "原因：续费率提升" in hit["block_context"]


def test_block_context_expands_entity_links_to_target_summary():
    from src.models.block import EntityRef
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.services.block_context import BlockContextService

    source_page = _insert_item("源页面", "参考 [[项目X]]")
    target_page = _insert_item("项目X", "项目X 关键进展摘要：已完成灰度发布")
    _insert_block("source-parent", source_page.id, "会议纪要", order_idx=0)
    _insert_block("source-hit", source_page.id, "参考 [[项目X]] 的进度", parent_id="source-parent", order_idx=1)
    EntityRefRepository().upsert(EntityRef(
        id="ref-1",
        source_type="block",
        source_id="source-hit",
        target_type="knowledge",
        target_id=target_page.id,
        ref_type="link",
    ))

    context = BlockContextService().build_context("source-hit")

    assert "会议纪要" in context
    assert "参考 [[项目X]] 的进度" in context
    assert "项目X" in context
    assert "关键进展摘要" in context


def test_link_discovery_creates_block_entity_ref_for_wiki_links():
    from src.services.link_discovery import LinkDiscoveryService

    source_page = _insert_item("任务页", "参考 [[前端重构]]")
    target_page = _insert_item("前端重构", "项目说明")
    _insert_block("source-block", source_page.id, "属于 [[前端重构]] 的任务", order_idx=0)

    count = LinkDiscoveryService().discover_links(source_page.id)

    refs = Database.get_conn().execute(
        "SELECT source_type, source_id, target_type, target_id, ref_type FROM entity_refs WHERE source_id = ?",
        ("source-block",),
    ).fetchall()
    assert count == 1
    assert dict(refs[0]) == {
        "source_type": "block",
        "source_id": "source-block",
        "target_type": "knowledge",
        "target_id": target_page.id,
        "ref_type": "link",
    }


def test_query_router_handles_logic_query_without_hybrid_search():
    from src.models.block import EntityRef
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.services.query_router import QueryRouter

    project = _insert_item("前端重构", "项目")
    task = _insert_item("Bug 任务", "任务", tags=["bug"])
    _insert_block(
        "task-block",
        task.id,
        "修复登录页错误",
        properties={"knowledge_id": task.id, "status": "unresolved"},
    )
    EntityRefRepository().upsert(EntityRef(
        id="ref-task-project",
        source_type="block",
        source_id="task-block",
        target_type="knowledge",
        target_id=project.id,
        ref_type="link",
    ))

    class ExplodingHybrid:
        def search(self, *args, **kwargs):
            raise AssertionError("hybrid search should not be called for logic queries")

    results = QueryRouter(db=Database, hybrid_searcher=ExplodingHybrid()).search(
        "帮我找出所有 #bug 且 ::status unresolved 的，属于 [[前端重构]] 项目的任务",
        top_k=5,
    )

    assert len(results) == 1
    assert results[0]["id"] == "task-block"
    assert results[0]["metadata"]["page_id"] == task.id


def test_rag_service_adds_source_graph_payload(monkeypatch):
    from src.services.rag_pipeline import RAGService

    page = _insert_item("来源页", "内容")
    _insert_block("parent", page.id, "父节点", order_idx=0)
    _insert_block("child", page.id, "命中内容", parent_id="parent", order_idx=1)

    class FakePipeline:
        async def execute(self, question, conversation_history=None, **kwargs):
            return {
                "answer": "回答",
                "sources": [{"chunk_id": "child", "knowledge_id": page.id, "title": "来源页"}],
                "wiki_context": "",
            }

    service = RAGService()
    service._pipeline = FakePipeline()

    result = service.query("问题")

    assert "source_graph" in result
    assert any(node["id"] == "child" for node in result["source_graph"]["nodes"])
    assert any(edge["source"] == "parent" and edge["target"] == "child" for edge in result["source_graph"]["edges"])


def test_link_discovery_rerun_removes_stale_auto_refs_and_preserves_manual_refs():
    from src.models.block import EntityRef
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.services.link_discovery import LinkDiscoveryService

    source_page = _insert_item("Task Page", "References [[Project Alpha]]")
    old_target = _insert_item("Project Alpha", "Old project")
    new_target = _insert_item("Project Beta", "New project")
    manual_target = _insert_item("Manual Target", "Curated relationship")
    _insert_block("linked-block", source_page.id, "See [[Project Alpha]]", order_idx=0)

    service = LinkDiscoveryService()
    assert service.discover_links(source_page.id) == 1
    EntityRefRepository().upsert(EntityRef(
        id="manual-ref",
        source_type="block",
        source_id="linked-block",
        target_type="knowledge",
        target_id=manual_target.id,
        ref_type="manual",
        auto_discovered=0,
    ))

    Database.get_conn().execute(
        "UPDATE blocks SET content = ? WHERE id = ?",
        ("See [[Project Beta]]", "linked-block"),
    )
    Database.get_conn().commit()

    assert service.discover_links(source_page.id) == 1
    rows = Database.get_conn().execute(
        """SELECT target_id, ref_type, auto_discovered
           FROM entity_refs
           WHERE source_type = 'block' AND source_id = ?
           ORDER BY target_id""",
        ("linked-block",),
    ).fetchall()
    refs = {(row["target_id"], row["ref_type"]): row["auto_discovered"] for row in rows}
    assert (old_target.id, "link") not in refs
    assert refs[(new_target.id, "link")] == 1
    assert refs[(manual_target.id, "manual")] == 0


def test_query_router_unknown_link_title_does_not_broaden_property_results():
    from src.services.query_router import QueryRouter

    task = _insert_item("Bug Task", "Task", tags=["bug"])
    _insert_block(
        "task-with-status",
        task.id,
        "Fix login page",
        properties={"knowledge_id": task.id, "status": "unresolved"},
    )

    class ExplodingHybrid:
        def search(self, *args, **kwargs):
            raise AssertionError("hybrid search should not be called for logic queries")

    results = QueryRouter(db=Database, hybrid_searcher=ExplodingHybrid()).search(
        "#bug ::status unresolved [[Missing Project]]",
        top_k=5,
    )

    assert results == []


def test_rag_query_stream_routes_logic_queries_and_returns_source_graph(monkeypatch):
    import src.services.rag_pipeline as rag_mod
    from src.models.block import EntityRef
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.services.rag_pipeline import RAGService

    project = _insert_item("Project Alpha", "Project summary")
    task = _insert_item("Bug Task", "Task", tags=["bug"])
    _insert_block(
        "stream-task",
        task.id,
        "Fix login page",
        properties={"knowledge_id": task.id, "status": "unresolved"},
    )
    EntityRefRepository().upsert(EntityRef(
        id="stream-ref",
        source_type="block",
        source_id="stream-task",
        target_type="knowledge",
        target_id=project.id,
        ref_type="link",
    ))

    class ExplodingHybrid:
        def search(self, *args, **kwargs):
            raise AssertionError("hybrid search should not run for logic stream queries")

    class PassThroughReranker:
        def rerank(self, question, candidates, top_n=None):
            return candidates

    class FakeLLM:
        def chat_stream(self, messages, silent=True, max_tokens_override=None):
            yield "answer"

    class FixedRewriter:
        def rewrite(self, question):
            return [question]

    monkeypatch.setattr(rag_mod.RAGService, "_get_wiki_context", lambda self, query: "")

    stream, sources, source_graph = RAGService(deps={
        "db": Database._instance,
        "query_rewriter": FixedRewriter(),
        "hybrid_search": ExplodingHybrid(),
        "reranker": PassThroughReranker(),
        "llm": FakeLLM(),
    }).query_stream(
        "#bug ::status unresolved [[Project Alpha]]"
    )

    assert "".join(stream) == "answer"
    assert sources[0]["chunk_id"] == "stream-task"
    assert any(node["id"] == "stream-task" for node in source_graph["nodes"])
    assert any(edge["source"] == "stream-task" and edge["target"] == project.id for edge in source_graph["edges"])


def test_chat_message_round_trips_source_graph_payload():
    from src.models.chat import ChatMessage, Conversation

    conv = Conversation(id="conv-source-graph", title="Graph Chat")
    Database.insert_conversation(conv.to_row())
    graph = {
        "nodes": [{"id": "block-1", "type": "block", "label": "Hit block"}],
        "edges": [{"source": "page-1", "target": "block-1", "type": "contains"}],
    }
    msg = ChatMessage(
        conversation_id=conv.id,
        role="assistant",
        content="answer",
        source_graph=graph,
    )

    Database.insert_message(msg.to_row())

    stored = Database.get_messages(conv.id)[0]
    assert json.loads(stored["source_graph"]) == graph


def test_chat_source_graph_summary_is_reader_facing():
    from src.gui.chat_view import _source_graph_summary

    summary = _source_graph_summary({
        "nodes": [
            {"id": "page-1", "type": "knowledge", "label": "Project Alpha"},
            {"id": "block-1", "type": "block", "label": "Fix login page"},
        ],
        "edges": [{"source": "page-1", "target": "block-1", "type": "contains"}],
    })

    assert "2" in summary
    assert "1" in summary
    assert "Project Alpha" in summary
    assert "Fix login page" in summary
