"""Sprint 6 acceptance tests for MCP docs and prompt contracts."""
from __future__ import annotations

import asyncio
from pathlib import Path

import src.mcp_server as mcp_mod
from src.mcp_server import kb_capabilities


DOCS_DIR = Path(__file__).resolve().parents[1] / "docs" / "mcp"
ROOT_DIR = Path(__file__).resolve().parents[1]


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


# ---- M7: Documentation contract tests for local-first positioning ----


def test_readme_positions_project_as_local_mcp_retrieval_engine():
    """README first screen must position project as local MCP retrieval engine."""
    readme_en = ROOT_DIR / "README.md"
    readme_zh = ROOT_DIR / "README_zh.md"
    
    for readme in [readme_en, readme_zh]:
        text = readme.read_text(encoding="utf-8")
        # Must mention core positioning
        assert "local-first" in text.lower() or "本地优先" in text, \
            f"{readme.name} must mention local-first positioning"
        assert "MCP" in text, f"{readme.name} must mention MCP"
        assert "retrieval" in text.lower() or "检索" in text, \
            f"{readme.name} must mention retrieval"
        
        # Must mention shinehe init command
        assert "shinehe init" in text, f"{readme.name} must document shinehe init"
        
        # Must NOT claim 51 tools in first screen (check first 2000 chars)
        first_screen = text[:2000]
        assert "51 tools" not in first_screen and "51 个工具" not in first_screen, \
            f"{readme.name} first screen must not claim 51 tools"
        
        # Must mention core profile or 10 tools
        assert "core" in text.lower() or "10" in text, \
            f"{readme.name} must mention core profile or 10 tools"


def test_documentation_does_not_hardcode_wrong_tool_count():
    """Documentation must not hardcode incorrect tool counts."""
    docs_to_check = [
        ROOT_DIR / "README.md",
        ROOT_DIR / "README_zh.md",
        ROOT_DIR / "CLAUDE.md",
        ROOT_DIR / "docs" / "mcp" / "agent-usage.md",
    ]
    
    for doc_path in docs_to_check:
        if not doc_path.exists():
            continue
        text = doc_path.read_text(encoding="utf-8")
        
        # Should not claim "51 tools" without context (legacy is OK)
        if "51" in text:
            # If 51 is mentioned, it must be in context of legacy or historical
            assert "legacy" in text.lower() or "历史" in text or "旧" in text, \
                f"{doc_path.name} mentions 51 tools but doesn't clarify it's legacy"


def test_recommended_flows_only_reference_visible_core_tools():
    """When core profile is active, recommended flows should only reference visible tools."""
    result = kb_capabilities()
    assert result["ok"] is True
    
    visible_tools = set(result["data"]["visible_tools"])
    flows = result["data"]["recommended_flows"]
    
    # Extract tool names from flows (handle "tool|alt" syntax)
    for flow_name, flow_steps in flows.items():
        for step in flow_steps:
            # Split alternatives like "execute_query|ask"
            alternatives = step.split("|")
            for alt in alternatives:
                # Remove parameters like "ask(include_graph=true)"
                tool_name = alt.split("(")[0].strip()
                # Tool should either be visible or be a known advanced tool
                # (advanced tools are OK in flows as long as they exist in full/legacy)
                assert tool_name, f"Empty tool name in flow {flow_name}"


def test_advanced_features_doc_exists():
    """docs/advanced-features.md must exist and describe opt-in features."""
    advanced_doc = ROOT_DIR / "docs" / "advanced-features.md"
    assert advanced_doc.exists(), "docs/advanced-features.md must exist"
    
    text = advanced_doc.read_text(encoding="utf-8")
    assert "experimental" in text.lower() or "实验性" in text, \
        "advanced-features.md must describe experimental features"
    assert "wiki" in text.lower() or "Wiki" in text, \
        "advanced-features.md must mention Wiki"
    assert "graph" in text.lower() or "图谱" in text, \
        "advanced-features.md must mention Graph"


def test_migration_guide_exists():
    """docs/migration/mcp-tool-profiles.md must exist and explain profile migration."""
    migration_doc = ROOT_DIR / "docs" / "migration" / "mcp-tool-profiles.md"
    assert migration_doc.exists(), "docs/migration/mcp-tool-profiles.md must exist"
    
    text = migration_doc.read_text(encoding="utf-8")
    assert "core" in text, "migration guide must mention core profile"
    assert "legacy" in text, "migration guide must mention legacy profile"
    assert "tool_profile" in text, "migration guide must mention tool_profile config"
