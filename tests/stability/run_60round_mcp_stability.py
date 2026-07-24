"""60 轮 MCP 长程稳定性测试 harness。

目标：通过 MCP 工具直接调用 ShineHeKnowledge 知识库，连续执行 60 轮，
覆盖所有暴露的 MCP 工具（core / extended / admin / experimental），
集中暴露稳定性缺陷（未捕获异常 + ok=false 信封），并把报告写入
``artifacts/stability-60round/report.json``。

设计要点：
- 临时 DB（tempfile），永不触碰 data/kb.db；
- mock 外部依赖（Embedding / LLM / VectorStore / BlockStore），避免网络与 sqlite-vec 依赖；
- 通过真实 ``create_container()`` 构建完整容器，保证所有 lazy 服务可用；
- 每轮调用 ~45 个工具，覆盖检索 / 问答 / 写入 / 删除恢复 / 撤销 / 异步任务 /
  Agent Memory / Wiki 工作流 / 图谱遍历 / 运维体检；
- 任何未捕获异常或预期成功却返回 ok=false 的调用记为 issue。

运行：
    python tests/stability/run_60round_mcp_stability.py
    STABILITY_ROUNDS=30 python tests/stability/run_60round_mcp_stability.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
ART = ROOT / "artifacts" / "stability-60round"

QUERIES = [
    "采购管理办法", "供应商准入评估", "企微运营完整服务率", "知识库检索测试",
    "合同审批流程", "项目里程碑计划", "数据安全规范", "用户权限管理",
    "系统架构设计", "接口文档说明", "绩效考核标准", "培训计划安排",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _StubPipeline:
    """快速 stub：替 container.rag_pipeline，避免 ask 走真实 RAG。"""

    def query(self, question, timeout=None):  # noqa: ANN001
        return {
            "answer": "",
            "sources": [],
            "source_graph": {"nodes": [], "edges": [], "truncated": False, "node_count": 0},
            "route": {"mode": "hybrid", "explanation": "stub"},
            "query_plan": {},
            "block_contexts": {},
            "warnings": [],
            "wiki_context": "",
            "trace_id": "",
            "answer_mode": "no_answer",
            "conflict_disclosed": False,
            "claims_used": [],
            "raw_evidence_used": [],
            "conflicts": [],
            "fallbacks": [],
        }


def setup_env():
    """构建临时环境 + 真实容器，返回 (container, tmp_dir, seed_ids)。"""
    tmp = tempfile.mkdtemp(prefix="shinehe-60round-")
    os.environ["SHINEHE_HOME"] = tmp

    from src.utils.config import Config

    # 注意：Config.set 必须放在 create_container() 之后。
    # create_container() 内部会 Config() + load() 从磁盘重新加载，
    # 覆盖 _default_instance，导致之前的 in-memory 设置全部丢失。
    # 此处仅做首次 load 以初始化默认实例（会自动生成最小 config.yaml）。
    Config.load()

    # 重置单例
    from src.services.block_store import BlockStore
    from src.services.db import Database
    from src.services.vectorstore import VectorStore

    Database._instance = None
    VectorStore._instance = None
    VectorStore._initialized = False
    BlockStore._instance = None
    BlockStore._initialized = False

    # mock 外部 AI 服务（类级别）
    from src.services.embedding import EmbeddingService
    from src.services.llm import LLMService

    EmbeddingService.embed = lambda self, text: [0.0] * 8  # type: ignore[assignment]
    EmbeddingService.embed_batch = lambda self, texts, batch_size=20: []  # type: ignore[assignment]
    EmbeddingService.embed_batch_with_cache = lambda self, texts, batch_size=20: []  # type: ignore[assignment]
    LLMService.chat = lambda self, messages, **kw: "mocked LLM answer"  # type: ignore[assignment]
    LLMService.chat_with_usage = lambda self, messages, **kw: ("mocked LLM answer", {"tokens": 10})  # type: ignore[assignment]

    # 用 mock 类替换 VectorStore / BlockStore，规避 sqlite-vec 依赖
    class _MockVS:
        def __init__(self, db=None):
            self.db = db

        def search(self, query, top_k=5):
            return [{"id": "c1", "text": query, "metadata": {"knowledge_id": "", "page_id": ""}, "distance": 0.9}]

        def add_chunks(self, chunks):
            pass

        def delete_by_knowledge(self, kid):
            pass

        def count(self):
            return 0

    class _MockBS:
        def __init__(self, db=None):
            self.db = db

        def search(self, query, top_k=5):
            return [{"id": "b1", "text": query, "page_id": "", "distance": 0.9}]

        def add_block_embedding(self, block_id, embedding):
            pass

        def delete_by_page(self, page_id):
            pass

        def count(self):
            return 0

    import src.services.block_store as bs_mod
    import src.services.vectorstore as vs_mod

    vs_mod.VectorStore = _MockVS  # type: ignore[assignment]
    bs_mod.BlockStore = _MockBS  # type: ignore[assignment]

    # Database 先 connect（建全 schema），create_container 会复用该实例
    Database.connect(os.path.join(tmp, "test.db"))

    from src.core.container import create_container

    container = create_container()

    # ── create_container() 之后设置覆盖配置 ──
    # 此时 _default_instance 已被 create_container 设置为新加载的实例，
    # 后续 Config.set 会正确写入该实例，工具调用时 Config.get 也能读到。
    Config.set("storage.data_dir", tmp)
    Config.set("storage.db_name", "test.db")
    Config.set("knowledge_workflow.wiki_dir", os.path.join(tmp, "wiki"))
    Config.set("security.allowed_ingest_dirs", [tmp, tempfile.gettempdir()])
    Config.set("rag.ask.total_timeout", 5)
    Config.set("rag.ask_with_query.total_timeout", 5)
    Config.set("mcp.enable_legacy_aliases", False)
    Config.set("mcp.write_policy", "")
    # 使用 evidence_only（legacy 别名）— 最简单的检索模式；
    # ask 实际走 _StubPipeline，但 bootstrap_registration 仍会校验 mode 合法性。
    Config.set("knowledge_workflow.mode", "evidence_only")
    # Wiki 启用以覆盖 wiki 域工具，但关闭自动编译/发布避免后台线程
    Config.set("wiki.enabled", True)
    Config.set("wiki.auto_compile", False)
    Config.set("wiki.auto_publish", False)

    # 让 ask 走快速 stub
    container._rag_pipeline = _StubPipeline()  # type: ignore[attr-defined]

    # 安装容器到所有 _get_container 探测点
    import src.mcp.runtime as rt
    import src.mcp.server as server_mod
    import src.mcp_server as compat_mod
    from src.compatibility.container_access import set_active_container

    server_mod._container = container
    compat_mod._container = container
    rt.set_container(container)
    set_active_container(container)

    seed_ids = seed_data(container)
    return container, tmp, seed_ids


def seed_data(container) -> list[str]:
    """插入 12 条知识 + 链式 entity_refs 图，供 search/read/graph 使用。"""
    from src.services.db import Database

    ids: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for i in range(12):
        kid = str(uuid.uuid4())
        title = f"知识节点 {i} {QUERIES[i % len(QUERIES)]}"
        content = f"{QUERIES[i % len(QUERIES)]} 的详细说明：这是第 {i} 条测试知识，用于稳定性验证。包含关键词与上下文。"
        Database.insert_knowledge({
            "id": kid,
            "title": title,
            "content": content,
            "source_type": "manual",
            "source_path": "",
            "file_type": "md",
            "file_size": 0,
            "content_hash": f"seed-{i}-{kid[:8]}",
            "file_created_at": "",
            "file_modified_at": "",
            "tags": json.dumps([QUERIES[i % len(QUERIES)], "seed"], ensure_ascii=False),
            "version": 1,
            "created_at": now,
            "updated_at": now,
        })
        Database.insert_blocks([{
            "id": f"b-{kid[:8]}",
            "parent_id": None,
            "page_id": kid,
            "content": content,
            "block_type": "text",
            "properties": "{}",
            "order_idx": 0,
            "created_at": now,
            "updated_at": now,
        }])
        ids.append(kid)

    # 链式引用图：b-i -> knowledge-(i+1)
    conn = Database.get_conn()
    for i in range(len(ids) - 1):
        conn.execute(
            "INSERT OR REPLACE INTO entity_refs "
            "(id, source_type, source_id, target_type, target_id, ref_type, weight) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"ref-{i}", "block", f"b-{ids[i][:8]}", "knowledge", ids[i + 1], "references", 1.0),
        )
    conn.commit()
    return ids


def call_tool(name, fn, issues, round_num, args=(), kwargs=None, expect_ok=True):
    """调用一个工具，记录异常 / 预期外的 ok=false。返回工具结果。"""
    kwargs = kwargs or {}
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        issues.append({
            "round": round_num, "tool": name, "kind": "exception",
            "error": f"{type(e).__name__}: {e}",
            "tb": traceback.format_exc(limit=4),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        })
        return None
    lat = round((time.perf_counter() - t0) * 1000, 2)
    if expect_ok and isinstance(result, dict) and result.get("ok") is False:
        issues.append({
            "round": round_num, "tool": name, "kind": "envelope_fail",
            "error": json.dumps(result.get("error", {}), ensure_ascii=False),
            "latency_ms": lat,
        })
    return result


def _data(result, *path, default=None):
    """从 envelope result["data"] 取嵌套字段。"""
    if not isinstance(result, dict):
        return default
    node = result.get("data", result)
    for key in path:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
        if node is None:
            return default
    return node


def run_round(round_num, container, seed_ids, tmp, tools):
    """执行一轮：覆盖全部 MCP 工具。返回该轮 issues 列表。"""
    issues: list[dict] = []
    q = QUERIES[round_num % len(QUERIES)]
    seed_id = seed_ids[round_num % len(seed_ids)]

    tmpfile = os.path.join(tmp, f"round_{round_num}.md")
    Path(tmpfile).write_text(
        f"# 第 {round_num} 轮测试文档\n\n关于 {q} 的内容说明，用于 ingest_file 验证。\n",
        encoding="utf-8",
    )

    # ── 检索 / 问答 / 只读 ops ──
    call_tool("ping", tools["ping"], issues, round_num)
    call_tool("kb_capabilities", tools["kb_capabilities"], issues, round_num)
    call_tool("kb_health_check", tools["kb_health_check"], issues, round_num)
    call_tool("list_knowledge", tools["list_knowledge"], issues, round_num, kwargs={"limit": 5})
    call_tool("tags", tools["tags"], issues, round_num, kwargs={"limit": 10})
    call_tool("search", tools["search"], issues, round_num, kwargs={"query": q, "top_k": 3})
    call_tool("search_fulltext", tools["search_fulltext"], issues, round_num, kwargs={"query": q, "limit": 5})
    call_tool("route_query", tools["route_query"], issues, round_num, kwargs={"question": q})
    call_tool("structured_query", tools["structured_query"], issues, round_num,
              kwargs={"query_dsl": {"filter": {"fulltext": q}}, "limit": 5})
    call_tool("explain_query", tools["explain_query"], issues, round_num,
              kwargs={"query_dsl": {"filter": {"fulltext": q}}})
    call_tool("execute_query", tools["execute_query"], issues, round_num,
              kwargs={"query_spec": {"filter": {"fulltext": q}}, "type": "structured", "limit": 5})
    ask_res = call_tool("ask", tools["ask"], issues, round_num,
                        kwargs={"question": q, "include_graph": False})
    call_tool("ask_with_query", tools["ask_with_query"], issues, round_num,
              kwargs={"search_query": q, "top_k": 3})
    call_tool("read", tools["read"], issues, round_num, kwargs={"item_id": seed_id})
    call_tool("get_source_graph", tools["get_source_graph"], issues, round_num,
              kwargs={"knowledge_ids": [seed_id], "max_nodes": 20})
    call_tool("graph_traverse", tools["graph_traverse"], issues, round_num,
              kwargs={"start_ids": [seed_id], "max_depth": 1, "limit": 10})

    # get_trace：trace_id 一般为空，调用即覆盖工具；NOT_FOUND 可接受
    trace_id = _data(ask_res, "trace_id", default="")
    if trace_id:
        call_tool("get_trace", tools["get_trace"], issues, round_num, kwargs={"trace_id": trace_id})
    else:
        call_tool("get_trace", tools["get_trace"], issues, round_num,
                  kwargs={"trace_id": "nonexistent-trace"}, expect_ok=False)

    # ── 写入 / 删除 / 恢复 / 撤销 ──
    create_res = call_tool("create", tools["create"], issues, round_num,
                           kwargs={"title": f"轮{round_num} 新建 {q}", "content": f"{q} 自动创建内容", "tags": [q]})
    new_id = _data(create_res, "id")

    if new_id:
        call_tool("read", tools["read"], issues, round_num, kwargs={"item_id": new_id})
        update_res = call_tool("update", tools["update"], issues, round_num,
                               kwargs={"item_id": new_id, "title": f"轮{round_num} 更新标题"})
        op_update = update_res.get("operation_id") if isinstance(update_res, dict) else None
        call_tool("preview_operation", tools["preview_operation"], issues, round_num,
                  kwargs={"operation": "update", "item_id": new_id, "title": "preview"})
        if op_update:
            call_tool("get_operation_log", tools["get_operation_log"], issues, round_num,
                      kwargs={"operation_id": op_update})
        call_tool("query_operation_logs", tools["query_operation_logs"], issues, round_num,
                  kwargs={"limit": 5})
        call_tool("list_recent_operations", tools["list_recent_operations"], issues, round_num,
                  kwargs={"limit": 5})
        call_tool("delete", tools["delete"], issues, round_num, kwargs={"item_id": new_id})
        call_tool("restore_knowledge", tools["restore_knowledge"], issues, round_num,
                  kwargs={"item_id": new_id})
        if op_update:
            call_tool("undo_operation", tools["undo_operation"], issues, round_num,
                      kwargs={"operation_id": op_update})

    # ── 异步任务 ──
    job_res = call_tool("create_ingest_job", tools["create_ingest_job"], issues, round_num,
                        kwargs={"file_path": tmpfile})
    job_id = _data(job_res, "job_id")
    if job_id:
        call_tool("get_job", tools["get_job"], issues, round_num, kwargs={"job_id": job_id})
        call_tool("list_jobs", tools["list_jobs"], issues, round_num, kwargs={"limit": 5})
        call_tool("cancel_job", tools["cancel_job"], issues, round_num, kwargs={"job_id": job_id})
    async_res = call_tool("create_async_job", tools["create_async_job"], issues, round_num,
                          kwargs={"job_type": "test_round", "params": {"round": round_num}})
    async_job_id = _data(async_res, "job_id")
    if async_job_id:
        call_tool("get_async_job", tools["get_async_job"], issues, round_num, kwargs={"job_id": async_job_id})
        call_tool("list_async_jobs", tools["list_async_jobs"], issues, round_num, kwargs={"limit": 5})
        call_tool("cancel_async_job", tools["cancel_async_job"], issues, round_num, kwargs={"job_id": async_job_id})

    # ── 导入 / 索引 / 运维 ──
    call_tool("ingest_file", tools["ingest_file"], issues, round_num, kwargs={"file_path": tmpfile})
    call_tool("ingest_url", tools["ingest_url"], issues, round_num,
              kwargs={"url": "https://example.com", "dry_run": True})
    call_tool("index_path", tools["index_path"], issues, round_num,
              kwargs={"path": tmp, "recursive": False, "dry_run": True})
    call_tool("reindex_all", tools["reindex_all"], issues, round_num, kwargs={"dry_run": True})
    call_tool("auto_tag", tools["auto_tag"], issues, round_num, kwargs={"limit": 2})

    # ── Agent Memory ──
    mem_res = call_tool("remember_fact", tools["remember_fact"], issues, round_num,
                        kwargs={"key": f"r{round_num}:decision", "value": f"{q} 决策记录", "category": "decision"})
    mem_id = _data(mem_res, "id")
    call_tool("recall_facts", tools["recall_facts"], issues, round_num, kwargs={"query": q})
    call_tool("update_project_context", tools["update_project_context"], issues, round_num,
              kwargs={"summary": f"第 {round_num} 轮稳定性测试项目上下文"})
    call_tool("search_decisions", tools["search_decisions"], issues, round_num, kwargs={"query": q})
    call_tool("summarize_recent_changes", tools["summarize_recent_changes"], issues, round_num, kwargs={})
    call_tool("extract_tasks_from_doc", tools["extract_tasks_from_doc"], issues, round_num,
              kwargs={"doc_id": seed_id})
    if mem_id:
        call_tool("delete_memory", tools["delete_memory"], issues, round_num, kwargs={"item_id": mem_id})

    # ── Wiki 工作流 ──
    long_answer = f"{q} 的标准回答：根据知识库记录，{q} 涉及多个环节，需要协同处理。详细说明不少于阈值。" * 2
    wiki_res = call_tool("save_to_wiki", tools["save_to_wiki"], issues, round_num,
                         kwargs={"question": q, "answer": long_answer, "source_ids": [seed_id],
                                 "enhance": False, "auto_publish": False})
    page_id = _data(wiki_res, "page_id")
    call_tool("wiki_lint", tools["wiki_lint"], issues, round_num, kwargs={})
    if page_id:
        call_tool("wiki_list_versions", tools["wiki_list_versions"], issues, round_num, kwargs={"page_id": page_id})
        call_tool("wiki_workflow_history", tools["wiki_workflow_history"], issues, round_num, kwargs={"page_id": page_id})
        call_tool("wiki_submit_review", tools["wiki_submit_review"], issues, round_num, kwargs={"page_id": page_id})
        call_tool("wiki_approve", tools["wiki_approve"], issues, round_num, kwargs={"page_id": page_id})
        call_tool("fix_dead_references", tools["fix_dead_references"], issues, round_num, kwargs={"dry_run": True})
        call_tool("delete_wiki_page", tools["delete_wiki_page"], issues, round_num, kwargs={"page_id": page_id})

    return issues


def _load_tools() -> dict:
    """从 src.mcp.tools.exports 统一加载全部工具 callable。"""
    from src.mcp.tools import exports as t

    return {
        "ping": t.ping, "kb_capabilities": t.kb_capabilities, "kb_health_check": t.kb_health_check,
        "list_knowledge": t.list_knowledge, "tags": t.tags, "search": t.search,
        "search_fulltext": t.search_fulltext, "route_query": t.route_query,
        "structured_query": t.structured_query, "explain_query": t.explain_query,
        "execute_query": t.execute_query, "ask": t.ask, "ask_with_query": t.ask_with_query,
        "read": t.read, "get_source_graph": t.get_source_graph, "graph_traverse": t.graph_traverse,
        "get_trace": t.get_trace,
        "create": t.create, "update": t.update, "delete": t.delete,
        "restore_knowledge": t.restore_knowledge, "preview_operation": t.preview_operation,
        "get_operation_log": t.get_operation_log, "query_operation_logs": t.query_operation_logs,
        "list_recent_operations": t.list_recent_operations, "undo_operation": t.undo_operation,
        "create_ingest_job": t.create_ingest_job, "get_job": t.get_job, "list_jobs": t.list_jobs,
        "cancel_job": t.cancel_job, "create_async_job": t.create_async_job,
        "get_async_job": t.get_async_job, "list_async_jobs": t.list_async_jobs,
        "cancel_async_job": t.cancel_async_job,
        "ingest_file": t.ingest_file, "ingest_url": t.ingest_url, "index_path": t.index_path,
        "reindex_all": t.reindex_all, "auto_tag": t.auto_tag,
        "remember_fact": t.remember_fact, "recall_facts": t.recall_facts,
        "update_project_context": t.update_project_context, "search_decisions": t.search_decisions,
        "summarize_recent_changes": t.summarize_recent_changes,
        "extract_tasks_from_doc": t.extract_tasks_from_doc, "delete_memory": t.delete_memory,
        "save_to_wiki": t.save_to_wiki, "wiki_lint": t.wiki_lint,
        "wiki_list_versions": t.wiki_list_versions, "wiki_workflow_history": t.wiki_workflow_history,
        "wiki_submit_review": t.wiki_submit_review, "wiki_approve": t.wiki_approve,
        "fix_dead_references": t.fix_dead_references, "delete_wiki_page": t.delete_wiki_page,
    }


def main(rounds: int = 60) -> dict:
    ART.mkdir(parents=True, exist_ok=True)
    container, tmp, seed_ids = setup_env()
    tools = _load_tools()

    all_issues: list[dict] = []
    per_round: list[dict] = []
    started = time.perf_counter()

    for r in range(rounds):
        t0 = time.perf_counter()
        iss = run_round(r, container, seed_ids, tmp, tools)
        dur = round(time.perf_counter() - t0, 2)
        all_issues.extend(iss)
        per_round.append({"round": r, "issues": len(iss), "duration_s": dur})
        if iss:
            tools_hit = sorted({i["tool"] for i in iss})
            print(f"[round {r + 1}/{rounds}] {len(iss)} issues ({dur}s) tools={tools_hit}")
        elif (r + 1) % 10 == 0:
            print(f"[round {r + 1}/{rounds}] ok ({dur}s)")

    total_dur = round(time.perf_counter() - started, 2)
    summary = {
        "ts": _now(),
        "rounds": rounds,
        "total_duration_s": total_dur,
        "total_issues": len(all_issues),
        "exception_count": sum(1 for i in all_issues if i["kind"] == "exception"),
        "envelope_fail_count": sum(1 for i in all_issues if i["kind"] == "envelope_fail"),
        "issue_tools": sorted({i["tool"] for i in all_issues}),
        "tool_count_covered": len(tools),
        "per_round": per_round,
        "issues": all_issues,
    }
    (ART / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "rounds": rounds,
                "total_issues": summary["total_issues"],
                "exceptions": summary["exception_count"],
                "envelope_fails": summary["envelope_fail_count"],
                "issue_tools": summary["issue_tools"],
                "duration_s": total_dur,
                "report": str(ART / "report.json"),
            },
            ensure_ascii=False,
        )
    )
    return summary


if __name__ == "__main__":
    main(int(os.environ.get("STABILITY_ROUNDS", "60")))
