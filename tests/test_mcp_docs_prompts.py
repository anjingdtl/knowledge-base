"""Sprint 6 acceptance tests for MCP docs and prompt contracts."""
from __future__ import annotations

import asyncio
from pathlib import Path

import src.mcp_server as mcp_mod
from src.mcp_server import kb_capabilities


DOCS_DIR = Path(__file__).resolve().parents[1] / "docs" / "mcp"


def test_mcp_docs_exist_and_describe_agent_flows():
    expected = {
        "agent-usage.md": ["kb_capabilities", "route_query", "execute_query", "ask", "read"],
        "tool-contract.md": ["ok", "data", "meta", "error", "operation_id", "dry_run"],
        "query-dsl.md": ["structured_query", "execute_query", "QuerySpec", "graph_traverse"],
        "safety-and-undo.md": ["preview_operation", "dry_run", "update", "get_operation_log", "undo_operation"],
        "ingest-jobs.md": ["create_ingest_job", "ingest_file", "get_job", "list_jobs", "cancel_job"],
    }
    for filename, required_terms in expected.items():
        path = DOCS_DIR / filename
        assert path.exists(), f"missing MCP doc: {path}"
        text = path.read_text(encoding="utf-8")
        for term in required_terms:
            assert term in text, f"{filename} should mention {term}"


def test_sprint6_prompts_are_registered():
    prompt_names = {prompt.name for prompt in asyncio.run(mcp_mod.mcp.list_prompts())}
    for name in {
        "kb_agent_research",
        "kb_safe_update",
        "kb_import_and_verify",
        "kb_query_with_sources",
    }:
        assert name in prompt_names


def test_sprint6_prompt_text_encodes_safe_agent_workflows():
    research = mcp_mod.kb_agent_research("How is vector search configured?")
    assert "kb_capabilities" in research
    assert "route_query" in research
    assert "execute_query" in research
    assert "ask" in research
    assert "read" in research

    safe_update = mcp_mod.kb_safe_update("item-1", {"title": "New title"})
    assert "preview_operation" in safe_update
    assert "dry_run=true" in safe_update
    assert "update" in safe_update
    assert "get_operation_log" in safe_update
    assert "undo_operation" in safe_update

    import_prompt = mcp_mod.kb_import_and_verify("F:/docs/big.xlsx")
    assert "create_ingest_job" in import_prompt
    assert "ingest_file" in import_prompt
    assert "get_job" in import_prompt
    assert "structured_query" in import_prompt
    assert "ask" in import_prompt

    sourced_qna = mcp_mod.kb_query_with_sources("What changed in Sprint 5?")
    assert "route_query" in sourced_qna
    assert "ask" in sourced_qna
    assert "include_graph=true" in sourced_qna
    assert "include_context=true" in sourced_qna
    assert "read" in sourced_qna


def test_kb_capabilities_flows_match_sprint6_docs():
    result = kb_capabilities()
    assert result["ok"] is True
    flows = result["data"]["recommended_flows"]
    assert flows["research"] == [
        "kb_capabilities",
        "route_query",
        "execute_query|ask",
        "get_source_graph",
        "read",
    ]
    assert flows["safe_update"] == [
        "read",
        "preview_operation",
        "update(dry_run=true)",
        "update",
        "get_operation_log",
    ]
    assert flows["import"] == [
        "kb_capabilities",
        "create_ingest_job|ingest_file",
        "get_job",
        "structured_query",
        "ask",
    ]
    assert flows["qna"] == [
        "route_query",
        "ask(include_graph=true, include_context=true)",
        "get_source_graph",
        "read",
    ]
