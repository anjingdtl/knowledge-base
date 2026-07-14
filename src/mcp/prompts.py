"""MCP prompt registrations."""
from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(name="kb_agent_research", description="Standard MCP-first research workflow.")
    def kb_agent_research(question: str) -> str:
        return (
            "Use the knowledge base through MCP tools only. Treat every tool result as an envelope.\n\n"
            f"Research question: {question}\n\n"
            "Recommended flow:\n"
            "1) Call kb_capabilities first.\n"
            "2) Call route_query to choose hybrid / structured / graph.\n"
            "3) Use execute_query or ask to gather evidence.\n"
            "4) Call get_source_graph and read for provenance.\n"
            "5) Answer only from retrieved evidence; disclose conflicts and fallbacks.\n"
        )

    @mcp.prompt(name="kb_safe_update", description="Safe audited update workflow.")
    def kb_safe_update(item_id: str, fields: dict) -> str:
        return (
            "Perform a safe audited knowledge update.\n\n"
            f"item_id: {item_id}\n"
            f"fields: {fields}\n\n"
            "Steps:\n"
            "1) read the current item.\n"
            "2) preview_operation with the intended change.\n"
            "3) update with dry_run=true.\n"
            "4) update for real only after preview looks correct.\n"
            "5) get_operation_log for the returned operation_id.\n"
            "6) If rollback is needed, use undo_operation with that operation_id.\n"
        )

    @mcp.prompt(name="kb_import_and_verify", description="Import a file and verify indexed evidence.")
    def kb_import_and_verify(file_path: str) -> str:
        return (
            "Import a local file and verify it is searchable.\n\n"
            f"file_path: {file_path}\n\n"
            "Steps:\n"
            "1) kb_capabilities\n"
            "2) create_ingest_job or ingest_file\n"
            "3) get_job until completed\n"
            "4) structured_query / search / ask to verify content\n"
        )

    @mcp.prompt(
        name="kb_query_with_sources",
        description="Answer with block-level sources and graph provenance.",
    )
    def kb_query_with_sources(question: str) -> str:
        return (
            "Answer the question using knowledge-base tools and always cite sources.\n\n"
            f"Question: {question}\n\n"
            "Steps:\n"
            "1) route_query to choose hybrid / structured / graph.\n"
            "2) ask(include_graph=true, include_context=true) or execute_query.\n"
            "3) get_source_graph and read for provenance.\n"
        )

    @mcp.prompt(name="kb_qa", description="知识库问答提示模板")
    def knowledge_qa_prompt(question: str) -> str:
        return (
            "你是一个专业的知识库助手。请基于知识库中的内容准确回答用户问题。"
            "回答时请标注引用的知识来源，如果知识库中没有相关信息请明确说明。\n\n"
            f"用户问题：{question}"
        )

    # Keep names on function objects for tests that call them via server re-exports
    register_prompts.kb_agent_research = kb_agent_research  # type: ignore[attr-defined]
    register_prompts.kb_safe_update = kb_safe_update  # type: ignore[attr-defined]
    register_prompts.kb_import_and_verify = kb_import_and_verify  # type: ignore[attr-defined]
    register_prompts.kb_query_with_sources = kb_query_with_sources  # type: ignore[attr-defined]
    register_prompts.knowledge_qa_prompt = knowledge_qa_prompt  # type: ignore[attr-defined]
