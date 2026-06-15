"""Tool profile definitions — which tools belong to which profile."""
from __future__ import annotations

CORE_TOOLS = frozenset({
    "ping", "kb_capabilities", "search", "ask", "read",
    "list_knowledge", "index_path", "get_job", "list_jobs", "reindex_all",
})

EXTENDED_TOOLS = CORE_TOOLS | frozenset({
    "search_fulltext", "tags", "route_query", "execute_query",
    "structured_query", "explain_query", "ask_with_query",
    "get_source_graph", "create_ingest_job", "cancel_job",
})

ADMIN_TOOLS = EXTENDED_TOOLS | frozenset({
    "create", "update", "delete", "restore_knowledge", "ingest_url",
    "preview_operation", "get_operation_log", "undo_operation",
    "list_recent_operations", "query_operation_logs",
})

# Groups that are considered "experimental" (wiki, graph, memory)
EXPERIMENTAL_GROUPS = frozenset({"wiki", "graph", "memory"})

# All known profiles
PROFILES = frozenset({"core", "extended", "admin", "full", "legacy"})


# Human-readable profile metadata, shared by GUI / kb_capabilities / migration docs.
# Keep titles and summaries short — UI cards rely on them rendering compactly.
PROFILE_INFO: dict[str, dict[str, str]] = {
    "core": {
        "label": "core — 最小核心档（10 个工具）",
        "summary": "10 个稳定的只读检索工具，最适合纯 AI Agent 检索场景。",
        "scope": "ping / kb_capabilities / search / ask / read / list_knowledge / index_path / get_job / list_jobs / reindex_all",
        "use_case": "AI Agent 仅做检索问答；想给 LLM 一个清爽工具面。",
        "writes": "仅 index_path / reindex_all 触发写，并受 write_policy 控制。",
    },
    "extended": {
        "label": "extended — 扩展档(20 个工具,推荐 / 默认)",
        "summary": "core + 高级查询能力(Query DSL、来源图谱、异步任务)。",
        "scope": "在 core 基础上增加 search_fulltext / tags / route_query / execute_query / structured_query / explain_query / ask_with_query / get_source_graph / create_ingest_job / cancel_job",
        "use_case": "通用 AI 助手的推荐档:既保留只读检索的稳定面,又提供结构化查询、证据链追溯、异步导入大文件等研究型能力。",
        "writes": "仅 index_path / reindex_all / create_ingest_job 触发写,受 write_policy 控制;不含增删改与撤销。",
    },
    "admin": {
        "label": "admin — 管理档（30 个工具）",
        "summary": "extended + 增删改、操作审计、撤销恢复等本地维护工具。",
        "scope": "在 extended 基础上增加 create / update / delete / restore_knowledge / ingest_url / preview_operation / get_operation_log / undo_operation / list_recent_operations / query_operation_logs",
        "use_case": "本地人工运维、需要在 AI 助手里直接增删改知识。",
        "writes": "包含写工具，建议同时设 write_policy = preview_only 或 local_confirm。",
    },
    "full": {
        "label": "full — 完整档（所有非 experimental 工具）",
        "summary": "暴露所有非 experimental 工具,功能最全。",
        "scope": "包含 core + extended + admin 的全部工具；Wiki / 图谱 / Agent Memory 工具仍需打开下方「实验性工具」开关才会出现。",
        "use_case": "需要在 AI 助手里直接增删改知识、跑操作审计的本地维护场景。",
        "writes": "包含全部写工具,写入仍受 write_policy 与 HTTP 模式 allow_http_write 限制。",
    },
    "legacy": {
        "label": "legacy — 旧版兼容档（含命名空间别名）",
        "summary": "v1.2 行为：所有工具 + kb.* 命名空间别名都注册，便于老客户端无缝迁移。",
        "scope": "所有工具（含 experimental）+ 所有别名；与 v1.2 一致。",
        "use_case": "已有依赖 kb.search / kb.ask 等别名的客户端；不想动现有集成。",
        "writes": "全部工具暴露，请务必设置合理的 write_policy 与 auth_token。",
    },
}
