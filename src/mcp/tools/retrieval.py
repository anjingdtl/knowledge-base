"""retrieval domain MCP tools (WP2 round-2 extraction from server.py).

Implementations registered via tool_definition side-effect on import.
"""
from __future__ import annotations

import json
import logging
import time
from typing import ParamSpec, TypeVar

from src.core.container import AppContainer
from src.mcp.envelopes import (
    ErrorCode,
    fail,
    ok,
)
from src.mcp.tools.support import (
    check_write_policy as _check_write_policy,
)
from src.mcp.tools.support import (
    define_tool as _define_tool,
)
from src.mcp.tools.support import (
    get_container as _get_container,
)
from src.mcp.tools.support import (
    heartbeat as _heartbeat,
)
from src.mcp.tools.support import (
    run_async as _run_async,
)
from src.utils.config import Config
from src.version import VERSION

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


def _load_json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}

def _resolve_query_alias(primary: str | None, alias: str | None) -> str | None:
    """Resolve canonical MCP argument names with a legacy ``query`` alias."""
    value = primary if primary is not None else alias
    if isinstance(value, str):
        value = value.strip()
    return value or None

def _natural_language_query_dsl(query: str, *, limit: int = 100, offset: int = 0) -> dict:
    return {
        "filter": {"fulltext": query},
        "limit": limit,
        "offset": offset,
        "sort": {"by": "updated_at", "order": "desc"},
    }

def _looks_like_json(value: str) -> bool:
    value = value.strip()
    return value.startswith("{") or value.startswith("[")

def _parse_query_dsl_or_natural_language(
    value,
    *,
    limit: int = 100,
    offset: int = 0,
    allow_natural_language: bool = True,
) -> tuple[dict, bool]:
    """Return ``(dsl_dict, was_natural_language_query)``."""
    if isinstance(value, dict):
        return value, False
    if isinstance(value, str) and (_looks_like_json(value) or not allow_natural_language):
        return json.loads(value), False
    if isinstance(value, str):
        return _natural_language_query_dsl(value, limit=limit, offset=offset), True
    return value, False

def _search_sources_from_query(query: str, *, limit: int = 5) -> list[dict]:
    rows = _get_container().db.search_knowledge(query, limit=max(1, limit), offset=0)
    return [
        {
            "knowledge_id": row.get("id"),
            "title": row.get("title", ""),
            "text": row.get("content", ""),
        }
        for row in rows
        if row.get("id")
    ]

def _list_blocks_for_page(page_id: str) -> list[dict]:
    rows = _get_container().db.get_conn().execute(
        """SELECT id, parent_id, page_id, content, block_type, properties, order_idx,
                  created_at, updated_at
           FROM blocks
           WHERE page_id = ?
           ORDER BY order_idx ASC, created_at ASC""",
        (page_id,),
    ).fetchall()
    blocks = []
    for row in rows:
        block = dict(row)
        block["properties"] = _load_json_dict(block.get("properties"))
        blocks.append(block)
    return blocks

def _embedding_context_config() -> dict:
    return {
        "enabled": bool(Config.get("rag.embedding_context.enabled", False)),
        "include_parent_chain": bool(
            Config.get("rag.embedding_context.include_parent_chain", True)
        ),
        "include_links": bool(Config.get("rag.embedding_context.include_links", True)),
        "include_siblings": bool(
            Config.get("rag.embedding_context.include_siblings", False)
        ),
        "max_chars": int(Config.get("rag.embedding_context.max_chars", 1200) or 1200),
    }

@_define_tool(
    name="ping",
    description="轻量级连通性检测（ping）。客户端可用此工具验证 MCP 连接是否存活，"
    "无需访问数据库或 LLM，响应 <10ms。推荐在会话开始时和工具调用前调用。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="ops", side_effect="read",
)
def ping() -> dict:
    """轻量级连通性检测，返回服务状态和时间戳。"""
    return ok({
        "status": "alive",
        "timestamp": time.time(),
        "version": VERSION,
        "uptime_hint": "ok",
    })

@_define_tool(
    name="search",
    description="基于语义相似度搜索知识库。使用向量嵌入查找与查询含义最相关的知识条目。Wiki 结构化知识优先返回。",
    annotations={"readOnlyHint": True, "openWorldHint": False},
    group="kb", side_effect="read",
)
@_heartbeat
def search(query: str, top_k: int = 5) -> dict:
    """基于语义的向量搜索，查找与查询含义最相关的知识内容。

    Args:
        query: 搜索查询文本，支持自然语言描述
        top_k: 返回结果数量，默认5条
    """
    from src.application.retrieval_commands import RetrievalCommands

    results = RetrievalCommands(_get_container()).semantic_search(
        query, top_k=top_k,
    )
    return ok(results, total_estimate=len(results), top_k=top_k)

@_define_tool(
    name="search_fulltext",
    description="基于关键词的全文搜索（FTS5）。适用于精确匹配关键词的场景。Wiki 结构化知识优先返回。",
    annotations={"readOnlyHint": True, "openWorldHint": False},
    group="kb", side_effect="read",
)
@_heartbeat
def search_fulltext(query: str, limit: int = 20, offset: int = 0) -> dict:
    """使用 FTS5 全文索引搜索知识库。

    Args:
        query: 搜索关键词
        limit: 返回结果数量上限，默认20
        offset: 分页偏移量，默认0
    """
    container = _get_container()
    db = container.db
    output = []

    # Wiki 结构化知识优先
    wiki_results = db.search_wiki_fts(query, limit=3)
    for wr in wiki_results:
        summary = wr.get("concept_summary", "")
        content_preview = (wr.get("content", "") or "")[:300]
        output.append({
            "source": "wiki",
            "title": wr["title"],
            "summary": summary,
            "text": f"[Wiki] {wr['title']}: {summary}\n{content_preview}",
            "fts_rank": wr.get("fts_rank", 0),
        })

    seen_block_ids = set()
    seen_knowledge_ids = set()

    # Block/chunk FTS uses jieba pre-tokenization and works better for Chinese
    # phrases than the item-level unicode61 index.
    block_results = db.search_blocks_fts(query, limit=max(limit + offset, limit))
    for block in block_results[offset:offset + limit]:
        block_id = block.get("id", "")
        knowledge_id = block.get("page_id", "")
        seen_block_ids.add(block_id)
        if knowledge_id:
            seen_knowledge_ids.add(knowledge_id)
        item = db.get_knowledge(knowledge_id) if knowledge_id else None
        output.append({
            "source": "knowledge",
            "match_channel": "block_fts",
            "match_channels": ["block_fts"],
            "block_id": block_id,
            "knowledge_id": knowledge_id,
            "title": item.get("title", "") if item else "",
            "text": block.get("content", ""),
            "block_type": block.get("block_type", ""),
            "properties": block.get("properties", {}),
            "fts_rank": block.get("fts_rank", 0),
        })

    chunk_results = db.search_chunks_fts(query, limit=max(limit + offset, limit))
    for chunk in chunk_results[offset:offset + limit]:
        chunk_id = chunk.get("id", "")
        if chunk_id in seen_block_ids:
            continue
        knowledge_id = chunk.get("knowledge_id", "")
        if knowledge_id:
            seen_knowledge_ids.add(knowledge_id)
        item = db.get_knowledge(knowledge_id) if knowledge_id else None
        output.append({
            "source": "knowledge",
            "match_channel": "chunk_fts",
            "match_channels": ["chunk_fts"],
            "chunk_id": chunk_id,
            "knowledge_id": knowledge_id,
            "title": item.get("title", "") if item else "",
            "text": chunk.get("chunk_text", ""),
            "fts_rank": chunk.get("fts_rank", 0),
        })

    # FTS5 知识搜索
    kb_results = db.search_knowledge(query, limit=limit, offset=offset)
    for item in kb_results:
        if item.get("id") in seen_knowledge_ids:
            continue
        item["source"] = "knowledge"
        item.setdefault("match_channel", "knowledge_fts")
        item.setdefault("match_channels", ["knowledge_fts"])
        output.append(item)

    # BUG-7 fix: 统一排序 — 将所有层的结果按 fts_score 归一化后排序
    # BUG-2 fix: 增加 title boost — 标题与查询有重叠的结果优先展示
    import re as _re

    from src.models.retrieval import normalize_fts_score
    # 提取查询关键词用于标题匹配
    _query_terms = set(_re.findall(r'[\u4e00-\u9fffA-Za-z0-9]{2,}', query))

    for item in output:
        raw_rank = item.get("fts_rank", 0)
        fts_score = normalize_fts_score(raw_rank)
        # Wiki 结构化知识优先：给 wiki 结果加小幅分数 boost
        if item.get("source") == "wiki":
            fts_score = min(fts_score + 0.1, 1.0)
        # BUG-2: title boost — 标题与查询关键词有重叠的文档提升排名
        title = item.get("title", "")
        if title and _query_terms:
            title_chars = set(title)
            overlap = len(_query_terms & {t for t in _query_terms if all(c in title_chars for c in t)})
            if overlap > 0:
                boost = 0.15 * overlap
                fts_score = min(fts_score + boost, 1.0)
                item.setdefault("match_channels", [])
                item["match_channels"].append("title_boost")
        item["fts_score"] = fts_score

    # 按 fts_score 降序排列（高相关性在前）
    output.sort(key=lambda x: x.get("fts_score", 0), reverse=True)

    has_more = len(kb_results) == limit or len(block_results) > offset + limit or len(chunk_results) > offset + limit
    return ok(
        output,
        limit=limit,
        offset=offset,
        next_offset=offset + len(output) if has_more else None,
        truncated=has_more,
        total_estimate=len(output),
    )

@_define_tool(
    name="ask",
    description="向知识库提问，使用 RAG（检索增强生成）流程自动检索相关内容并生成回答。"
    "返回结构化 payload：answer / sources / source_graph / route / query_plan / "
    "block_contexts / warnings。"
    "[耗时提示：通常 5-30 秒，首次调用可能更长；服务端总超时 rag.ask.total_timeout（默认 90s），"
    "超时返回空 answer + warnings，建议客户端超时 ≥ 100s]",
    annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="kb", side_effect="read",
)
@_heartbeat
def ask(
    question: str | None = None,
    include_graph: bool = True,
    include_context: bool = True,
    max_sources: int = 5,
    max_graph_nodes: int = 50,
    query: str | None = None,
) -> dict:
    """基于知识库的智能问答，返回 7 字段结构化 RAG payload。

    Args:
        question: 用户的问题

    Returns:
        envelope.data 包含：
            - answer: 生成的答复
            - sources: 引用来源列表，每项必含 block_id / knowledge_id / title
            - source_graph: 引用关系图（nodes / edges / truncated / node_count）
            - route: 路由决策（mode / explanation / query_spec / traverse）
            - query_plan: DSL 查询计划（structured 模式时有内容）
            - block_contexts: block_id → block 父链上下文的映射
            - warnings: 检索/生成阶段的告警
            - wiki_context: Wiki 知识上下文（仅当 wiki.enabled=true）
    """
    question = _resolve_query_alias(question, query)
    if not question:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "ask requires question (or query alias)",
        )
    # Prefer server._do_ask when tests patch src.mcp.server
    import sys

    server_mod = sys.modules.get("src.mcp.server")
    do_ask = getattr(server_mod, "_do_ask", None) if server_mod else None
    if do_ask is None:
        do_ask = _do_ask
    result = do_ask(question)
    if max_sources and max_sources > 0:
        result["sources"] = list(result.get("sources", []))[:max_sources]
    if include_graph:
        from src.services.source_graph import build_source_graph
        result["source_graph"] = build_source_graph(
            result.get("sources", []),
            db=_get_container().db,
            max_nodes=max_graph_nodes,
            graph_backend=_get_container().graph_backend,
        )
    else:
        result["source_graph"] = {"nodes": [], "edges": [], "truncated": False, "node_count": 0}
    if not include_context:
        result["block_contexts"] = {}
    return ok(
        result,
        source_count=len(result.get("sources", [])),
        warning_count=len(result.get("warnings", [])),
        route_mode=result.get("route", {}).get("mode", "unknown"),
        graph_truncated=result.get("source_graph", {}).get("truncated", False),
        trace_id=result.get("trace_id", ""),
    )

def _should_use_verified_ask() -> bool:
    """Phase 4: verified hybrid answer path when flag + mode allow wiki read.

    Honors monkeypatch of ``src.mcp.server._should_use_verified_ask`` in tests.
    """
    import sys

    server = sys.modules.get("src.mcp.server")
    if server is not None:
        patched = getattr(server, "_should_use_verified_ask", None)
        # If tests replaced the attribute with a different callable, use it.
        if callable(patched) and getattr(patched, "__module__", "") != __name__:
            return bool(patched())
        if callable(patched) and patched is not _should_use_verified_ask_impl:
            # lambda / bound stub without __module__ match
            code = getattr(patched, "__code__", None)
            if code is not None and code.co_name == "<lambda>":
                return bool(patched())

    return _should_use_verified_ask_impl()


def _should_use_verified_ask_impl() -> bool:
    try:
        from src.utils.knowledge_settings import resolve_effective_knowledge_settings

        settings = resolve_effective_knowledge_settings()
        return settings.verified_hybrid_enabled and settings.wiki_read_enabled
    except Exception:  # noqa: BLE001
        return False


def ask_verified(container: AppContainer, question: str, *, top_k: int = 5) -> dict:
    """Verified hybrid ask via Application RetrievalCommands / AnswerService."""
    from src.application.retrieval_commands import RetrievalCommands

    return RetrievalCommands(container).ask_verified(question, top_k=top_k)


def _do_ask(question: str) -> dict:
    # BUG-2 fix (50轮测试报告): ask 工具增加总超时控制。
    # Phase 4: verified hybrid 时优先 SearchService + AnswerService
    # （冲突披露 / claim+evidence 引用 / answer_mode）；否则走 rag_pipeline。
    import concurrent.futures

    from src.utils.config import Config

    total_timeout = float(Config.get("rag.ask.total_timeout", 90) or 90)
    timeout_label = f"{total_timeout:g}s"

    container = _get_container()

    def _run_verified() -> dict:
        return ask_verified(
            container,
            question,
            top_k=int(Config.get("rag.ask.max_sources", 5) or 5),
        )

    def _run_legacy() -> dict:
        return dict(container.rag_pipeline.query(question, timeout=total_timeout))

    # The production container owns the verified dependencies.  Keeping the
    # legacy runner for minimal test/integration doubles preserves the timeout
    # and error envelope contract without requiring them to emulate the whole
    # SearchService + LLM graph.
    use_verified = _should_use_verified_ask() and isinstance(container, AppContainer)
    runner = _run_verified if use_verified else _run_legacy
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(runner)
            result = dict(future.result(timeout=total_timeout))
    except concurrent.futures.TimeoutError:
        logger.warning(
            "ask timed out after %s for question=%r, returning partial result",
            timeout_label, question[:50],
        )
        result = {
            "answer": "",
            "sources": [],
            "source_graph": {"nodes": [], "edges": [], "truncated": False, "node_count": 0},
            "route": {"mode": "timeout",
                      "explanation": f"ask timed out after {timeout_label}"},
            "query_plan": {},
            "block_contexts": {},
            "warnings": [f"ask timed out after {timeout_label}, "
                         f"question too complex or document too large"],
            "wiki_context": "",
            "trace_id": "",
            "answer_mode": "no_answer",
            "conflict_disclosed": False,
            "claims_used": [],
            "raw_evidence_used": [],
            "conflicts": [],
            "fallbacks": [],
        }
        return result
    except Exception as e:
        # S3.2:query() 现向上传播非超时异常(S1.4:不再盲目 fallback _direct_query),
        # ask 必须在此兜住,返回结构化部分结果 + 告警,避免冒泡成未处理 MCP 错误
        # (与 ask_with_query 的韧性对齐,堵住 Bug-2 同类的「无兜底」缺口)。
        logger.error("ask pipeline failed for question=%r: %s", question[:50], e)
        return {
            "answer": "",
            "sources": [],
            "source_graph": {"nodes": [], "edges": [], "truncated": False, "node_count": 0},
            "route": {"mode": "error", "explanation": f"ask failed: {e}"},
            "query_plan": {},
            "block_contexts": {},
            "warnings": [f"ask failed: {type(e).__name__}: {e}"],
            "wiki_context": "",
            "trace_id": "",
            "answer_mode": "no_answer",
            "conflict_disclosed": False,
            "claims_used": [],
            "raw_evidence_used": [],
            "conflicts": [],
            "fallbacks": [],
        }

    # Ensure Phase 4 fields always present (legacy path defaults)
    result.setdefault("answer_mode", "raw_only")
    result.setdefault("conflict_disclosed", False)
    result.setdefault("claims_used", [])
    result.setdefault("raw_evidence_used", [])
    result.setdefault("conflicts", [])
    result.setdefault("fallbacks", [])

    # Phase 3: add trace_id to result if observability enabled
    if Config.get("rag.observability.trace_enabled", True):
        result.setdefault("trace_id", "")

    # Phase 3: add score_breakdown for each source in debug mode
    if Config.get("rag.observability.debug_scores", False):
        result.setdefault("_debug", {})["score_breakdown"] = True

    return result

def _resolve_read_target(
    *,
    item_id: str | None = None,
    block_id: str | None = None,
    claim_id: str | None = None,
    page_id: str | None = None,
    knowledge_id: str | None = None,
) -> dict | None:
    """Phase 4 Spec §8.4: read by claim_id / block_id / page_id / knowledge_id.

    Returns an envelope dict when a typed target is resolved; None to fall through
    to legacy knowledge-item read.
    """
    container = _get_container()
    raw = (claim_id or block_id or page_id or knowledge_id or item_id or "").strip()
    if not raw and not any([claim_id, block_id, page_id, knowledge_id]):
        return None

    kind = None
    value = raw
    if claim_id:
        kind, value = "claim", claim_id
    elif block_id:
        kind, value = "block", block_id
    elif page_id:
        kind, value = "page", page_id
    elif knowledge_id:
        kind, value = "knowledge", knowledge_id
    elif item_id:
        lower = item_id.lower()
        if lower.startswith("claim:"):
            kind, value = "claim", item_id.split(":", 1)[1]
        elif lower.startswith("block:"):
            kind, value = "block", item_id.split(":", 1)[1]
        elif lower.startswith("page:"):
            kind, value = "page", item_id.split(":", 1)[1]
        else:
            # Heuristic: claim_ prefix or looks like claim id in wiki repo
            if item_id.startswith("claim_") or item_id.startswith("cl_"):
                kind, value = "claim", item_id
            else:
                return None  # legacy knowledge path

    if kind == "claim":
        try:
            repo = container.wiki_repository
            claim = repo.get_claim(value) if repo is not None else None
        except Exception as e:  # noqa: BLE001
            return fail(ErrorCode.INTERNAL_ERROR, f"读取 Claim 失败: {e}", claim_id=value)
        if claim is None:
            return fail(ErrorCode.NOT_FOUND, f"Claim 不存在: {value}", claim_id=value)
        # Resolve evidence validity via Serving Gate when available
        gate = getattr(container, "wiki_serving_gate", None)
        decision = None
        if gate is not None:
            try:
                decision = gate.evaluate(claim)
            except Exception:  # noqa: BLE001
                decision = None
        evidence_rows = []
        for ev in claim.evidence:
            block = None
            try:
                if ev.block_id:
                    block = container.db.get_conn().execute(
                        "SELECT id, page_id, content, properties FROM blocks WHERE id = ?",
                        (ev.block_id,),
                    ).fetchone()
            except Exception:  # noqa: BLE001
                block = None
            block_dict = dict(block) if block is not None and hasattr(block, "keys") else (
                {"id": block[0], "page_id": block[1], "content": block[2]} if block else None
            ) if block is not None else None
            evidence_rows.append({
                "evidence_id": ev.evidence_id,
                "knowledge_id": ev.knowledge_id,
                "block_id": ev.block_id,
                "stance": ev.stance.value if hasattr(ev.stance, "value") else str(ev.stance),
                "stale": bool(ev.stale),
                "excerpt_hash": ev.excerpt_hash,
                "excerpt": (block_dict or {}).get("content", "")[:500] if block_dict else "",
                "valid": (not ev.stale) and block_dict is not None,
            })
        relations = []
        for rel in (claim.relations or []):
            if hasattr(rel, "__dict__"):
                relations.append({
                    k: getattr(rel, k) for k in ("relation_type", "target_id", "direction")
                    if hasattr(rel, k)
                } or {"raw": str(rel)})
            elif isinstance(rel, dict):
                relations.append(rel)
            else:
                relations.append({"raw": str(rel)})
        payload = {
            "type": "claim",
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "normalized_statement": claim.normalized_statement,
            "status": claim.status.value if hasattr(claim.status, "value") else str(claim.status),
            "revision": claim.revision,
            "confidence": claim.confidence,
            "relations": relations,
            "evidence": evidence_rows,
            "serving": {
                "eligible": bool(decision.eligible) if decision else None,
                "disclose_only": bool(decision.disclose_only) if decision else None,
                "reason_codes": list(decision.reason_codes) if decision else [],
            },
        }
        return ok(payload, claim_id=value)

    if kind == "block":
        try:
            row = container.db.get_conn().execute(
                "SELECT id, page_id, content, block_type, properties, order_idx "
                "FROM blocks WHERE id = ?",
                (value,),
            ).fetchone()
        except Exception as e:  # noqa: BLE001
            return fail(ErrorCode.INTERNAL_ERROR, f"读取 Block 失败: {e}", block_id=value)
        if row is None:
            return fail(ErrorCode.NOT_FOUND, f"Block 不存在: {value}", block_id=value)
        if hasattr(row, "keys"):
            block = dict(row)
        else:
            block = {
                "id": row[0], "page_id": row[1], "content": row[2],
                "block_type": row[3], "properties": row[4], "order_idx": row[5],
            }
        kid = block.get("page_id") or ""
        item = container.db.get_knowledge(kid) if kid else None
        return ok({
            "type": "block",
            "block_id": block.get("id"),
            "knowledge_id": kid,
            "content": block.get("content"),
            "block_type": block.get("block_type"),
            "properties": block.get("properties"),
            "knowledge": item,
        }, block_id=value)

    if kind == "page":
        try:
            repo = container.wiki_repository
            page = repo.get_page(value) if repo is not None and hasattr(repo, "get_page") else None
        except Exception as e:  # noqa: BLE001
            return fail(ErrorCode.INTERNAL_ERROR, f"读取 Page 失败: {e}", page_id=value)
        if page is None:
            # Fall back to knowledge item
            item = container.db.get_knowledge(value)
            if item:
                return ok({"type": "knowledge", **item}, page_id=value)
            return fail(ErrorCode.NOT_FOUND, f"Page 不存在: {value}", page_id=value)
        if hasattr(page, "to_dict"):
            payload = page.to_dict()
        elif isinstance(page, dict):
            payload = page
        else:
            payload = {
                "page_id": getattr(page, "page_id", value),
                "title": getattr(page, "title", ""),
                "status": str(getattr(page, "status", "")),
            }
        payload = dict(payload)
        payload["type"] = "wiki_page"
        return ok(payload, page_id=value)

    if kind == "knowledge":
        item = container.db.get_knowledge(value)
        if not item:
            return fail(ErrorCode.NOT_FOUND, f"知识条目不存在: {value}", knowledge_id=value)
        return ok({"type": "knowledge", **item}, knowledge_id=value)

    return None

@_define_tool(
    name="read",
    description="根据 ID 读取知识/块/Claim/Wiki 页面。"
    "支持 item_id=知识ID、block_id、claim_id、page_id（或带前缀 claim:/block:/page:）。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def read(
    item_id: str | None = None,
    include_blocks: bool = False,
    include_embedding_preview: bool = False,
    include_effective_properties: bool = False,
    include_linked_summaries: bool = False,
    query: str | None = None,
    top_k: int = 1,
    block_id: str | None = None,
    claim_id: str | None = None,
    page_id: str | None = None,
    knowledge_id: str | None = None,
) -> dict:
    """读取指定 ID 的知识条目、Block、Claim 或 Wiki Page（Spec §8.4）。

    Args:
        item_id: 知识条目 ID，或 claim:/block:/page: 前缀 ID
        block_id / claim_id / page_id / knowledge_id: 显式定位（优先于 item_id）
    """
    container = _get_container()
    item_id = _resolve_query_alias(item_id, None)
    query = _resolve_query_alias(None, query)

    # Phase 4: explicit typed IDs
    typed = _resolve_read_target(
        item_id=item_id,
        block_id=block_id,
        claim_id=claim_id,
        page_id=page_id,
        knowledge_id=knowledge_id,
    )
    if typed is not None:
        return typed

    if not item_id and query:
        exact = container.db.get_knowledge(query)
        if exact:
            item_id = exact["id"]
        else:
            matches = container.db.search_knowledge(query, limit=max(1, int(top_k or 1)), offset=0)
            if not matches:
                return fail(
                    ErrorCode.NOT_FOUND,
                    f"未找到与查询匹配的知识条目: {query}",
                    query=query,
                )
            if int(top_k or 1) > 1:
                items = [container.db.get_knowledge(row["id"]) or row for row in matches]
                return ok(
                    items,
                    resolved_from_query=True,
                    query=query,
                    count=len(items),
                    top_k=top_k,
                )
            item_id = matches[0]["id"]
    if not item_id:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "read requires item_id (or query alias)",
        )
    item = container.db.get_knowledge(item_id)
    if not item:
        return fail(ErrorCode.NOT_FOUND, f"知识条目不存在: {item_id}", item_id=item_id)
    if include_blocks or include_embedding_preview or include_effective_properties or include_linked_summaries:
        blocks = _list_blocks_for_page(item_id)
        if include_embedding_preview:
            from src.services.embedding import EmbeddingService
            embedding_service = EmbeddingService(container.config)
            config_snapshot = _embedding_context_config()
            for block in blocks:
                text = embedding_service.build_embedding_text(block)
                block["embedding_preview"] = {
                    "enabled": config_snapshot["enabled"],
                    "text": text,
                    "char_count": len(text),
                    "config": config_snapshot,
                }
        if include_effective_properties:
            service = container.effective_properties
            compute = getattr(service, "_compute_effective_for_block", None)
            for block in blocks:
                block["effective_properties"] = (
                    compute(block["id"]) if compute else service.refresh_block(block["id"])
                )
        if include_linked_summaries:
            from src.services.block_context import BlockContextService
            context_service = BlockContextService(db=container.db, config=container.config)
            max_links = int(Config.get("rag.link_expansion.max_links", 3) or 3)
            get_links = getattr(context_service, "_get_linked_summaries")
            for block in blocks:
                block["linked_summaries"] = get_links(block["id"], max_links)
        item = dict(item)
        item["blocks"] = blocks
    return ok(item, resolved_from_query=bool(query and item_id))

@_define_tool(
    name="structured_query",
    description="执行结构化查询 DSL，返回知识条目列表", annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="kb", side_effect="read",
)
@_heartbeat
def structured_query(
    query_dsl: str | dict | None = None,
    limit: int = 100,
    offset: int = 0,
    query: str | dict | None = None,
    filters: str | dict | None = None,  # BUG-6 fix: 向后兼容 v1.3.1 的 filters 参数别名
) -> dict:
    """Execute a structured JSON DSL query against the knowledge base.

    The DSL supports tag, property, fulltext, link, file_type, source_type filters
    combined with and/or/not groups.

    Args:
        query_dsl: JSON string with the query DSL（也接受 dict）
        limit: Maximum results to return
        offset: 分页偏移量
        filters: （已弃用，向后兼容）等同于 query_dsl。新代码请使用 query_dsl
        query: （已弃用，向后兼容）等同于 query_dsl
    """
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    container = _get_container()
    try:
        # BUG-6 fix: filters 参数向后兼容，优先使用 query_dsl
        query_value = query_dsl if query_dsl is not None else (filters if filters is not None else query)
        if query_value is None:
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "structured_query requires query_dsl (or query alias)",
            )
        dsl, natural_language = _parse_query_dsl_or_natural_language(
            query_value,
            limit=limit,
            offset=offset,
            allow_natural_language=query_dsl is None and query is not None,
        )
        if natural_language and isinstance(query_value, str):
            rows = container.db.search_knowledge(query_value, limit=limit, offset=offset)
            has_more = len(rows) == limit
            return ok(
                rows,
                limit=limit,
                offset=offset,
                next_offset=offset + len(rows) if has_more else None,
                truncated=has_more,
                query_alias_used=query is not None,
                natural_language_query=True,
            )
        spec = QuerySpec.from_json(dsl)
        spec.limit = min(spec.limit, limit)
        spec.offset = offset
        executor = QueryExecutor(db=container.db)
        results = executor.execute(spec)
        results_list = list(results) if not isinstance(results, list) else results
        # BUG#2 修复：meta 的 limit 与 has_more 应基于实际生效的 spec.limit
        # （DSL limit 与 tool limit 的较小值），而非 tool 参数 limit(默认100)。
        effective_limit = spec.limit
        has_more = len(results_list) == effective_limit
        return ok(
            results_list,
            limit=effective_limit,
            offset=offset,
            next_offset=offset + len(results_list) if has_more else None,
            truncated=has_more,
        )
    except Exception as exc:
        logger.exception("structured_query failed: %s", exc)
        return fail(ErrorCode.QUERY_PARSE_ERROR, str(exc))

@_define_tool(
    name="explain_query",
    description="解释结构化查询的执行计划与匹配条件", annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    group="kb", side_effect="read",
)
@_heartbeat
def explain_query(query_dsl: str | dict | None = None, query: str | dict | None = None) -> dict:
    """Explain a structured query: show human-readable summary, execution plan, and condition tree.

    Args:
        query_dsl: JSON string with the query DSL（也接受 dict）
    """
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    try:
        query_value = query_dsl if query_dsl is not None else query
        if query_value is None:
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "explain_query requires query_dsl (or query alias)",
            )
        dsl, natural_language = _parse_query_dsl_or_natural_language(
            query_value,
            allow_natural_language=query_dsl is None and query is not None,
        )
        spec = QuerySpec.from_json(dsl)
        payload = QueryExplainer().explain(spec)
        if natural_language:
            payload["query"] = spec.to_json()
            payload["natural_language_query"] = True
        return ok(payload, query_alias_used=query is not None)
    except Exception as exc:
        logger.exception("explain_query failed: %s", exc)
        return fail(ErrorCode.QUERY_PARSE_ERROR, str(exc))

@_define_tool(
    name="route_query",
    description="路由分析并附带轻量搜索线索。返回 mode (structured|graph|hybrid) + query_spec + "
    "traverse + explanation + evidence_preview（top 3 匹配标题+摘要），"
    "Agent 据此决定下一步走 execute_query 还是 ask_with_query。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def route_query(question: str | None = None, query: str | None = None,
                include_evidence: bool = True) -> dict:
    """路由分析：识别问题是结构化 / 图谱 / 模糊语义，并可附带轻量级证据摘要。

    Args:
        question: 用户原始问题
        include_evidence: 是否附带轻量级搜索线索（默认True，
            返回top 3匹配标题+摘要，不触发LLM，仅FTS快速检索）

    Returns:
        envelope.data 字段：
            - mode: structured | graph | hybrid
            - query_spec: QuerySpec JSON dict（structured 模式）
            - traverse: 遍历配置（graph 模式，max_depth 等）
            - explanation: 路由选择的理由
            - evidence_preview: [{title, text_preview, score}] 轻量证据（可选）
    """
    from src.services.agentic_router import AgenticRouter, serialize_route
    container = _get_container()
    try:
        question = _resolve_query_alias(question, query)
        if not question:
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "route_query requires question (or query alias)",
            )
        router = AgenticRouter(db=container.db, llm=container.llm)
        routing = router.route(question)
        payload = serialize_route(routing)

        # BUG-3 fix + Phase 2: 附带轻量级搜索线索，让Agent能看到相关文档而不需要二次调用
        # 优先级：vector（语义匹配）> blocks_fts（jieba分词，中文精确匹配）> wiki_fts > LIKE兜底
        if include_evidence:
            try:
                db = container.db
                evidence: list[dict] = []
                # 对查询做简化提取：取核心中文词组（去除停用词），避免长句FTS5匹配失败
                import re as _re
                _STOP_WORDS = {'的','了','是','在','和','与','或','有','中','及','对','等',
                               '为','被','把','向','从','到','由','用','以','按','将','给',
                               '不','没','也','都','就','而','且','但','如','什么','怎么',
                               '哪些','如何','可以','能够','需要','是否'}
                # 提取2-6字的中文/字母/数字词组
                chunks = _re.findall(r'[\u4e00-\u9fffA-Za-z0-9]{2,6}', question)
                simple_query = ' '.join(c for c in chunks if c not in _STOP_WORDS) or question

                # Phase 2: 第一优先 — vector 语义搜索（覆盖语义模糊匹配）
                if len(evidence) < 5:
                    try:
                        from src.services.block_store import BlockStore
                        vec_results = BlockStore().search(question, top_k=3)
                        seen_kids_vec = {e.get("knowledge_id") for e in evidence if e.get("knowledge_id")}
                        for vr in vec_results[:3]:
                            kid = vr.get("metadata", {}).get("page_id", "") or vr.get("metadata", {}).get("knowledge_id", "")
                            if kid in seen_kids_vec:
                                continue
                            seen_kids_vec.add(kid)
                            item = db.get_knowledge(kid) if kid else None
                            evidence.append({
                                "title": item.get("title", "") if item else "",
                                "text_preview": (vr.get("text", ""))[:200],
                                "source": "knowledge",
                                "knowledge_id": kid,
                                "source_channel": "vector",
                                "score": round(max(0, 1 - vr.get("distance", 1.0) / 2), 4),
                            })
                    except Exception:
                        pass

                # 第二优先：block FTS（使用jieba分词，中文搜索可靠）
                block_hits = db.search_blocks_fts(simple_query, limit=5)
                seen_kids = {e.get("knowledge_id") for e in evidence if e.get("knowledge_id")}
                for b in block_hits:
                    kid = b.get("page_id", "")
                    if kid in seen_kids:
                        continue
                    seen_kids.add(kid)
                    item = db.get_knowledge(kid) if kid else None
                    evidence.append({
                        "title": item.get("title", "") if item else "",
                        "text_preview": (b.get("content", ""))[:200],
                        "source": "knowledge",
                        "knowledge_id": kid,
                        "source_channel": "fts",
                    })
                    if len(evidence) >= 5:
                        break

                # 第三优先：wiki FTS
                if len(evidence) < 5:
                    wiki_hits = db.search_wiki_fts(simple_query, limit=2)
                    for w in wiki_hits:
                        evidence.append({
                            "title": w.get("title", ""),
                            "text_preview": (w.get("concept_summary", "") or w.get("content", ""))[:200],
                            "source": "wiki",
                            "source_channel": "fts",
                        })

                # 最终兜底：知识项标题模糊匹配
                if not evidence:
                    try:
                        tried = set()
                        for chunk in (chunks or [question[:4]]):
                            if chunk in tried or len(chunk) < 2:
                                continue
                            tried.add(chunk)
                            rows = db.get_conn().execute(
                                "SELECT id, title FROM knowledge_items WHERE title LIKE ? AND deleted_at IS NULL LIMIT 3",
                                (f"%{chunk}%",),
                            ).fetchall()
                            for row in rows:
                                evidence.append({
                                    "title": row[1],
                                    "text_preview": "",
                                    "source": "knowledge_title_match",
                                    "knowledge_id": row[0],
                                    "source_channel": "like",
                                })
                            if evidence:
                                break
                    except Exception:
                        pass
                payload["evidence_preview"] = evidence[:5]
            except Exception as e:
                logger.warning("route_query evidence preview failed (non-fatal): %s", e)
                payload["evidence_preview"] = []

        return ok(payload, mode=payload.get("mode"))
    except Exception as exc:
        logger.exception("route_query failed: %s", exc)
        return fail(ErrorCode.INTERNAL_ERROR, str(exc), question=question)

@_define_tool(
    name="execute_query",
    description="执行显式 QuerySpec DSL。支持 type=structured（条件过滤）或 graph（图遍历）。"
    "混合检索请使用 search、ask 或 ask_with_query。分页透传 limit/offset/next_offset/truncated。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def execute_query(
    query_spec: dict,
    type: str = "structured",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """执行 QuerySpec DSL。

    Args:
        query_spec: QuerySpec JSON dict（含 filter / sort / limit / offset / include_blocks）
        type: structured | graph — 决定走哪个执行器
        limit: 返回结果数量上限
        offset: 分页偏移量

    Returns:
        envelope.data 列表 + meta.{total_estimate, limit, offset, next_offset, truncated, type}
    """
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    container = _get_container()
    try:
        spec = QuerySpec.from_json(query_spec) if isinstance(query_spec, dict) else query_spec
        spec.limit = min(spec.limit, limit)
        spec.offset = offset

        if type == "graph":
            from src.services.graph_traversal import GraphTraversalService
            start_ids = (query_spec or {}).get("start_ids", [])
            start_type = (query_spec or {}).get("start_type", "knowledge")
            max_depth = (query_spec or {}).get("max_depth", 2)
            if not start_ids:
                return fail(
                    ErrorCode.VALIDATION_ERROR,
                    "graph 模式需要在 query_spec.start_ids 提供起点节点",
                )
            traversal = GraphTraversalService(db=container.db, graph_backend=container.graph_backend).traverse(
                start_ids=start_ids, start_type=start_type, max_depth=max_depth,
            )
            nodes = traversal.get("nodes", [])
            has_more = len(nodes) == limit
            sliced = nodes[offset:offset + limit] if has_more else nodes
            return ok(
                {
                    "nodes": sliced,
                    "edges": traversal.get("edges", []),
                    "paths": traversal.get("paths", []),
                    "truncated": traversal.get("truncated", False) or has_more,
                },
                type=type,
                limit=limit,
                offset=offset,
                next_offset=offset + len(sliced) if has_more else None,
                total_estimate=len(nodes),
            )

        if type == "structured":
            executor = QueryExecutor(db=container.db)
            results = executor.execute(spec)
            results_list = list(results) if not isinstance(results, list) else results
            has_more = len(results_list) == limit
            return ok(
                results_list,
                type=type,
                limit=limit,
                offset=offset,
                next_offset=offset + len(results_list) if has_more else None,
                total_estimate=len(results_list),
                truncated=has_more,
            )

        return fail(
            ErrorCode.VALIDATION_ERROR,
            f"不支持的 type: {type}，仅支持 structured / graph",
            type=type,
        )
    except Exception as exc:
        logger.exception("execute_query failed: %s", exc)
        return fail(ErrorCode.QUERY_PARSE_ERROR, str(exc), type=type)

@_define_tool(
    name="ask_with_query",
    description="用显式 QuerySpec 或简化参数控制 RAG 检索阶段，再调用 LLM 生成回答。"
    "返回结构化 payload（含 answer / sources / route / query_plan / block_contexts / warnings）。"
    "[耗时提示：通常 5-30 秒，建议客户端超时 ≥ 60s]\n"
    "调用要求：至少提供 question 或 search_query 其中一个。\n"
    "简化模式：仅需传 search_query 即可（自动用 search_query 作为 question 并构造 fulltext QuerySpec）。\n"
    "标准模式：传 question + search_query（question 为用户原始问题，search_query 控制检索）。\n"
    "高级模式：传 question + query_spec（支持复杂过滤条件），"
    "query_spec 格式如 {\"filter\": {\"fulltext\": \"关键词\"}, "
    "\"sort\": {\"by\": \"updated_at\", \"order\": \"desc\"}}，"
    "filter 支持 tag/property/fulltext/title/link/file_type/source_type/and/or/not 类型。",
    annotations={"readOnlyHint": True, "idempotentHint": False},
    group="kb", side_effect="read",
)
@_heartbeat
def ask_with_query(
    question: str | None = None,
    query_spec: dict | None = None,
    search_query: str | None = None,
    search_mode: str = "blend",
    top_k: int = 10,
    query: str | None = None,  # BUG-5 fix: 向后兼容 v1.3.1 的 query 参数别名
) -> dict:
    """用显式 QuerySpec 或简化参数控制 RAG 检索，再生成回答。

    Args:
        question: 用户问题（可选，不传时使用 search_query 作为问题）
        query_spec: QuerySpec JSON dict，控制 RAG 检索阶段（可选，
            不传时自动根据 search_query 构造简单 fulltext QuerySpec，
            或完全依赖混合检索）。格式:
            {"filter": {"fulltext": "关键词"}, "sort": {"by": "updated_at", "order": "desc"}}
        search_query: 简化参数 — 全文搜索关键词。提供时自动构造
            fulltext QuerySpec，无需手动构造 query_spec（优先级低于 query_spec）。
            同时可作为 question 的替代，不传 question 时用 search_query 兜底。
        search_mode: 检索模式 blend(默认)/keyword/semantic，
            仅在 query_spec 和 search_query 都未提供时生效
        top_k: 检索阶段召回的候选数
        query: （已弃用，向后兼容）等同于 search_query。新代码请使用 search_query
    Returns:
        与 ``ask`` 工具相同的 7 字段结构化 payload（data 内）
    """
    from src.models.query_dsl import QuerySpec
    from src.services.rag_pipeline import DEFAULT_PIPELINE_CONFIG, RagPipeline

    container = _get_container()
    try:
        # BUG-5 fix: query 参数向后兼容，优先使用 search_query
        search_query = search_query or query
        # BUG-4 fix: question 可选，不传时用 search_query 兜底
        effective_question = question or search_query
        if not effective_question:
            return fail(
                ErrorCode.VALIDATION_ERROR,
                "ask_with_query requires at least one of: question, search_query",
            )

        # BUG-4 fix: query_spec 可选，支持简化参数模式
        spec = None
        if query_spec is not None:
            spec = QuerySpec.from_json(query_spec) if isinstance(query_spec, dict) else query_spec
        elif search_query is not None:
            # 简化模式：自动构造 fulltext QuerySpec
            spec = QuerySpec.from_json({"filter": {"fulltext": search_query}})

        # Phase 3: configurable timeout from config
        total_timeout = int(Config.get("rag.ask_with_query.total_timeout", 120) or 120)

        pipeline = RagPipeline(
            pipeline_config=DEFAULT_PIPELINE_CONFIG,
            llm=container.llm,
            deps={
                "db": container.db,
                "llm": container.llm,
                "query_rewriter": container.query_rewriter,
                "reranker": container.reranker,
                "hybrid_search": container.hybrid_search,
                # 与 container.rag_pipeline 对齐:缺这四项会让 graph 模式无后端,
                # 且 size-aware 路由 / wiki parent-child(本次升级核心)在
                # ask_with_query 静默失效,即便项目已启用。
                "graph_backend": container.graph_backend,
                "size_aware_router": container.size_aware_router,
                "wiki_page_locator": container.wiki_page_locator,
                "wiki_parent_retriever": container.wiki_parent_retriever,
            },
        )
        # 把 spec 注入 metadata，VectorSearchStage 会跳过自动路由直接使用
        # 使用 _run_async 安全执行，避免在已有事件循环中调用 asyncio.run()
        try:
            result = _run_async(
                pipeline.execute(
                    effective_question,
                    query_spec_override=spec,
                    top_k=top_k,
                    tool_name="ask_with_query",
                ),
                timeout=total_timeout,
            )
        except TimeoutError:
            # Phase 3: return partial result + timeout warning
            logger.warning("ask_with_query timed out after %ds for question=%r", total_timeout, effective_question[:50])
            return ok(
                {
                    "answer": "",
                    "sources": [],
                    "source_graph": {"nodes": [], "edges": [], "truncated": False, "node_count": 0},
                    "route": {"mode": "timeout", "explanation": f"Query timed out after {total_timeout}s"},
                    "query_plan": {},
                    "block_contexts": {},
                    "warnings": [f"ask_with_query timed out after {total_timeout}s"],
                    "wiki_context": "",
                },
                source_count=0,
                warning_count=1,
                route_mode="timeout",
                graph_truncated=False,
            )

        # Phase 3: add trace_id to result
        trace_id = result.get("trace_id", "")

        return ok(
            result,
            source_count=len(result.get("sources", [])),
            warning_count=len(result.get("warnings", [])),
            route_mode=result.get("route", {}).get("mode", "unknown"),
            graph_truncated=result.get("source_graph", {}).get("truncated", False),
            trace_id=trace_id,
        )
    except Exception as exc:
        logger.exception("ask_with_query failed: %s", exc)
        return fail(ErrorCode.INTERNAL_ERROR, str(exc), question=question)

def _runtime_diagnostics() -> dict:
    """Return non-secret operational diagnostics for MCP clients."""
    c = _get_container()
    key_state = {
        "llm": bool(Config.get("llm.api_key", "")),
        "embedding": bool(Config.get("embedding.api_key", "") or Config.get("llm.api_key", "")),
        "reranker": bool(Config.get("reranker.api_key", "") or Config.get("embedding.api_key", "")),
    }

    block_count = 0
    vector_count = 0
    vector_error = ""
    try:
        row = c.db.get_conn().execute("SELECT COUNT(*) AS cnt FROM blocks").fetchone()
        block_count = int(row["cnt"] if row else 0)
    except Exception as exc:
        vector_error = f"block count unavailable: {type(exc).__name__}"

    try:
        vector_count = int(c.block_store.count())
    except Exception as exc:
        vector_error = f"sqlite-vec unavailable: {type(exc).__name__}: {str(exc)[:120]}"

    coverage = (vector_count / block_count) if block_count else 0.0
    if not key_state["embedding"]:
        recommendation = "配置 embedding.api_key 或 llm.api_key 后重建向量索引"
    elif block_count and vector_count == 0:
        recommendation = "向量索引为空，请执行 reindex_all 或迁移脚本回填 block embeddings"
    elif block_count and coverage < 0.8:
        recommendation = "向量索引覆盖率偏低，请执行 reindex_all 回填缺失 block embeddings"
    else:
        recommendation = "向量索引状态正常"

    return {
        "api_keys": key_state,
        "vector_index": {
            "blocks": block_count,
            "vectors": vector_count,
            "coverage": round(coverage, 4),
            "sqlite_vec_ok": not bool(vector_error),
            "error": vector_error,
            "recommendation": recommendation,
        },
    }

def _kb_capabilities_verified_fields() -> dict:
    """Phase 6 Spec §9.4 verified hybrid capability fields."""
    from src.utils.knowledge_settings import resolve_effective_knowledge_settings

    try:
        settings = resolve_effective_knowledge_settings()
    except Exception:  # noqa: BLE001
        return {
            "knowledge_mode": "invalid",
            "raw_retrieval": True,
            "verified_wiki_read": False,
            "wiki_authoring": False,
            "wiki_serving_status": "disabled",
            "fallback": "raw_retrieval",
        }
    wiki_serving = "unavailable"
    try:
        container = _get_container()
        if not settings.wiki_read_enabled or not settings.verified_hybrid_enabled:
            wiki_serving = "disabled"
        else:
            claims = container.search_service.list_servable_wiki_claims(limit=1)
            wiki_serving = "ready" if claims else "empty"
    except Exception:  # noqa: BLE001
        wiki_serving = "degraded"
    return {
        "knowledge_mode": settings.mode,
        "raw_retrieval": True,
        "verified_wiki_read": settings.wiki_read_enabled,
        "wiki_authoring": settings.authoring_enabled,
        "wiki_serving_status": wiki_serving,
        "fallback": "raw_retrieval",
    }

@_define_tool(
    name="kb_capabilities",
    description="查询知识库 MCP 能力清单、payload 限制、推荐调用流程。Agent 第一个应调用的工具。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="kb", side_effect="read",
)
@_heartbeat
def kb_capabilities() -> dict:
    """返回当前 MCP 服务的能力、限制和推荐调用流程。"""
    from src.mcp.registration import STATE, compute_hidden_groups
    from src.mcp.tool_catalog import TOOL_METADATA
    from src.mcp.tool_registry import get_definitions

    state = STATE
    if state is None:
        # bootstrap not run (tests importing tools only) — best-effort defaults
        visible = set(get_definitions().keys())
        registered_aliases: dict[str, str] = {}
        profile = "extended"
        experimental = False
        aliases_enabled = False
    else:
        visible = state.visible_tool_names
        registered_aliases = state.registered_aliases
        profile = state.profile
        experimental = state.experimental_enabled
        aliases_enabled = state.aliases_enabled

    tool_summaries: list[dict] = []
    try:
        all_defs = get_definitions()
        for name, definition in all_defs.items():
            if name in visible:
                tool_summaries.append({
                    "name": name,
                    "description": definition.description,
                })
        for alias_name, original_name in registered_aliases.items():
            original = all_defs.get(original_name)
            if original:
                tool_summaries.append({
                    "name": alias_name,
                    "description": f"{original.description} (alias of {original_name})",
                })
    except Exception as exc:
        logger.debug("tool registry introspection failed: %s", exc)

    return ok({
        "name": "ShineHeKnowledge",
        "version": VERSION,
        "envelope": {
            "ok": "bool — 成功/失败判断字段",
            "data": "any — 成功时的负载",
            "error": "{code, message, details} — 失败时的稳定错误码",
            "meta": "分页/截断/计数等元信息",
            "operation_id": "写操作的 audit log id（与 get_operation_log 配合）",
            "dry_run": "预览变更不执行",
        },
        "error_codes": {
            "NOT_FOUND": "资源不存在",
            "VALIDATION_ERROR": "参数校验失败",
            "PERMISSION_DENIED": "路径越权",
            "INGEST_FAILED": "文件/URL 导入失败",
            "INTERNAL_ERROR": "内部异常",
            "WIKI_DISABLED": "Wiki 未启用",
            "JOB_NOT_FOUND": "任务不存在",
            "QUERY_PARSE_ERROR": "DSL/JSON 解析失败",
        },
        "limits": {
            "default_page_size": int(Config.get("mcp.default_page_size", 20)),
            "max_payload_bytes": int(Config.get("mcp.max_payload_bytes", 1_000_000)),
            "max_graph_nodes": int(Config.get("rag.max_graph_nodes", 200)),
            "max_graph_depth": int(Config.get("rag.max_graph_depth", 3)),
        },
        "tool_metadata": TOOL_METADATA,
        "tool_aliases": registered_aliases,
        "tools": tool_summaries,
        "runtime_diagnostics": _runtime_diagnostics(),
        "recommended_flows": {
            "research": ["kb_capabilities", "route_query", "execute_query|ask", "get_source_graph", "read"],
            "safe_update": ["read", "preview_operation", "update(dry_run=true)", "update", "get_operation_log"],
            "import": ["kb_capabilities", "create_ingest_job|ingest_file", "get_job", "structured_query", "ask"],
            "import_large": ["kb_capabilities", "create_ingest_job", "get_job", "structured_query", "ask"],
            "qna": ["route_query", "ask(include_graph=true, include_context=true)", "get_source_graph", "read"],
            "agent_memory": ["remember_fact", "recall_facts", "update_project_context", "search_decisions", "summarize_recent_changes"],
        },
        "tool_profile": profile,
        "write_policy": Config.get("mcp.write_policy", ""),
        "experimental_tools_enabled": experimental,
        "visible_tools": sorted({t["name"] for t in tool_summaries if "." not in t["name"]}),
        "hidden_groups": sorted(compute_hidden_groups(tool_summaries)),
        "legacy_aliases_enabled": aliases_enabled,
        # Phase 6 Spec §9.4
        **_kb_capabilities_verified_fields(),
        "registered_tools": sorted({t["name"] for t in tool_summaries if "." not in t["name"]}),
        "hidden_by_policy": sorted(state.hidden_by_policy) if state else [],
        "serving_claim_statuses": ["active"],
        "citation_layers": ["claim", "raw_evidence"],
        "recommended_flow": ["search", "read", "ask"],
    })

@_define_tool(
    name="kb_health_check",
    description="知识库健康度检查。返回 API Key 状态、向量覆盖率、标签覆盖率、缓存命中率、P95 延迟等指标。"
    "用于运维巡检和问题定位。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="ops", side_effect="read",
)
@_heartbeat
def kb_health_check() -> dict:
    """执行知识库健康度检查，返回各项指标。

    Returns:
        status: healthy / degraded / unhealthy
        api_keys: {llm, embedding, reranker} 各是否已配置
        vector_coverage: 0.0-1.0
        tag_coverage: 0.0-1.0
        cache_hit_rate: {embedding, rag_cache_size}
        latency_p95_ms: 最近50次查询的P95延迟
        total_documents / total_blocks / total_vectors
        warnings: 告警列表
    """
    from src.services.health import kb_health_check as _check
    try:
        result = _check()
        return ok(result, status=result.get("status", "unknown"))
    except Exception as exc:
        logger.exception("kb_health_check failed: %s", exc)
        return fail(ErrorCode.INTERNAL_ERROR, str(exc))

@_define_tool(
    name="auto_tag",
    description="使用 LLM 对无标签知识条目进行批量自动打标。提升标签覆盖率，改善按标签过滤和结构化查询的效果。"
    "建议在标签覆盖率 < 50% 时执行。",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    group="ops", side_effect="write",
    experimental=False,
)
@_heartbeat
def auto_tag(limit: int = 50, force: bool = False) -> dict:
    """基于 LLM 的批量自动标签工具。

    扫描 tags 为空（或 '[]'）的知识条目，使用 LLM 根据标题+内容摘要
    自动生成 1-3 个标签并写入数据库。

    Args:
        limit: 单次最多处理的条目数（默认 50，最大 500）
        force: 强制重新打标（包括已有标签的条目），默认仅处理无标签条目

    Returns:
        tagged_count: 已打标数量
        skipped_count: 跳过的数量
        errors: 错误列表
        tags_applied: 新应用的标签列表（去重）
    """
    _guard = _check_write_policy("auto_tag")
    if _guard:
        return _guard

    container = _get_container()
    try:
        db = container.db
        if db is None or getattr(db, "_shutdown", False):
            return fail(ErrorCode.INTERNAL_ERROR, "数据库未初始化")

        from src.application.tagging_service import TaggingService

        result = TaggingService(db, container.llm).auto_tag(
            limit=limit, force=force,
        )
        tagged_count = int(result.get("tagged_count") or 0)
        skipped_count = int(result.get("skipped_count") or 0)
        errors = list(result.get("errors") or [])
        tags_applied = list(result.get("tags_applied") or [])
        message = result.get("message") or (
            f"成功打标 {tagged_count} 条，跳过 {skipped_count} 条，"
            f"应用标签 {len(tags_applied)} 个"
        )

        return ok(
            {
                "tagged_count": tagged_count,
                "skipped_count": skipped_count,
                "errors": errors[:10],
                "tags_applied": tags_applied,
                "message": message,
            },
            tagged_count=tagged_count,
            skipped_count=skipped_count,
            error_count=len(errors),
        )

    except Exception as exc:
        logger.exception("auto_tag failed: %s", exc)
        return fail(ErrorCode.INTERNAL_ERROR, str(exc))

@_define_tool(
    name="get_trace",
    description="根据 trace_id 查询链路追踪记录，包含各管线阶段耗时、结果数等信息。"
    "用于问题定位和性能分析。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    group="ops", side_effect="read",
)
@_heartbeat
def get_trace(trace_id: str) -> dict:
    """查询链路追踪记录。

    Args:
        trace_id: 追踪 ID（由 ask/ask_with_query 返回的 trace_id 字段获取）
    """
    if not trace_id:
        return fail(ErrorCode.VALIDATION_ERROR, "trace_id 必填")
    from src.services.trace import QueryTrace
    result = QueryTrace.get_by_id(trace_id)
    if result is None:
        return fail(ErrorCode.NOT_FOUND, f"Trace {trace_id} not found", trace_id=trace_id)
    return ok(result)
