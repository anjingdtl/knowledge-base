"""健康度检查 — kb_health_check()

Phase 3 / pipeline-hardening: 返回系统健康指标，包括 API Key 状态、向量覆盖率、
标签覆盖率、平均延迟 P95、缓存命中率。
"""
import logging
import time
from typing import Any

from src.utils.config import Config

logger = logging.getLogger(__name__)


def _get_kb_domain_summary(candidates: list[dict] | None = None) -> str:
    """BUG-7 helper: 构建知识库领域概览，用于检索无结果时的 LLM 上下文兜底。

    Returns:
        领域概览字符串，包含知识库文档数量、标签列表和主要领域
    """
    try:
        from src.services.db import Database
        db = Database()
        total = db.count_knowledge() or 0
        tags = db.get_all_tags()
        tag_list = sorted(tags)[:20] if tags else []
        lines = [
            f"知识库当前包含 {total} 篇文档，{len(tags)} 个标签。",
            f"主要标签：{', '.join(tag_list)}" if tag_list else "(暂无标签)",
        ]
        # 尝试获取最近的文档标题分布
        try:
            recent_docs = db.search_knowledge("", limit=5)
            if recent_docs:
                recent_titles = [d.get("title", "")[:30] for d in recent_docs if d.get("title")]
                if recent_titles:
                    lines.append(f"最近文档示例：{' | '.join(recent_titles[:3])}")
        except Exception:
            pass
        return "\n".join(lines)
    except Exception:
        return "知识库概览暂时无法获取"


def kb_health_check() -> dict[str, Any]:
    """执行知识库健康度检查，返回各项指标。

    Returns:
        dict with keys:
            - status: "healthy" | "degraded" | "unhealthy"
            - api_keys: {llm: bool, embedding: bool, reranker: bool}
            - vector_coverage: float (0.0-1.0)
            - tag_coverage: float (0.0-1.0)
            - cache_hit_rate: {embedding: float, rag: float}
            - latency_p95_ms: float | None
            - total_documents: int
            - total_blocks: int
            - total_vectors: int
            - warnings: list[str]
    """
    warnings: list[str] = []
    status = "healthy"

    # --- API Key 检查 ---
    llm_key = bool(Config.get("llm.api_key", ""))
    embedding_key = bool(Config.get("embedding.api_key", "") or Config.get("llm.api_key", ""))
    reranker_key = bool(Config.get("reranker.api_key", "") or Config.get("llm.api_key", ""))

    if not llm_key:
        warnings.append("LLM API Key 未配置")
        status = "degraded"
    if not embedding_key:
        warnings.append("Embedding API Key 未配置")
        status = "degraded"
    if not reranker_key:
        warnings.append("Reranker API Key 未配置")

    # --- 数据库指标 ---
    total_documents = 0
    total_blocks = 0
    total_vectors = 0
    vector_coverage = 0.0
    tag_coverage = 0.0

    try:
        from src.services.db import Database
        db = Database._instance
        if db is not None and not db._shutdown:
            with db.get_conn() as conn:
                # 文档数
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM knowledge_items WHERE deleted_at IS NULL"
                ).fetchone()
                total_documents = row["cnt"] if row else 0

                # Block 数
                row = conn.execute("SELECT COUNT(*) as cnt FROM blocks").fetchone()
                total_blocks = row["cnt"] if row else 0

                # 向量数
                try:
                    row = conn.execute("SELECT COUNT(*) as cnt FROM vec_blocks").fetchone()
                    total_vectors = row["cnt"] if row else 0
                except Exception:
                    total_vectors = 0

                # 向量覆盖率
                if total_blocks > 0:
                    vector_coverage = min(total_vectors / total_blocks, 1.0)
                if vector_coverage < 0.5:
                    warnings.append(f"向量覆盖率仅 {vector_coverage:.1%}")
                    status = "degraded"

                # 标签覆盖率
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM knowledge_items WHERE deleted_at IS NULL AND tags IS NOT NULL AND tags != '' AND tags != '[]'"
                ).fetchone()
                tagged = row["cnt"] if row else 0
                tag_coverage = tagged / total_documents if total_documents > 0 else 0.0
                if tag_coverage < 0.1:
                    warnings.append(f"标签覆盖率仅 {tag_coverage:.1%}（严重偏低，建议执行 auto_tag 批量补标）")
                    status = "degraded"
                elif tag_coverage < 0.5:
                    warnings.append(f"标签覆盖率仅 {tag_coverage:.1%}（建议执行 auto_tag 提升标签覆盖率）")
    except Exception as e:
        warnings.append(f"数据库查询失败: {e}")
        status = "unhealthy"

    # --- 缓存命中率 ---
    embedding_hit_rate = 0.0
    rag_hit_rate = 0.0
    try:
        from src.services.embedding import _l1_cache
        embedding_hit_rate = _l1_cache.hit_rate
    except Exception:
        pass
    try:
        from src.services.rag_pipeline import _rag_cache
        # _RAGResultCache doesn't track hit rate yet, report size
        rag_cache_size = _rag_cache.size
    except Exception:
        rag_cache_size = 0

    # --- 延迟 P95 (从最近的 trace 记录中读取) ---
    latency_p95 = None
    try:
        from src.services.db import Database
        db = Database._instance
        if db is not None and not db._shutdown:
            with db.get_conn() as conn:
                rows = conn.execute(
                    "SELECT metadata FROM operation_logs WHERE operation LIKE 'trace:%' "
                    "ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                if rows:
                    import json
                    durations = []
                    for r in rows:
                        try:
                            data = json.loads(r["metadata"])
                            dur = data.get("total_duration_ms", 0)
                            if dur > 0:
                                durations.append(dur)
                        except Exception:
                            pass
                    if durations:
                        durations.sort()
                        p95_idx = min(int(len(durations) * 0.95), len(durations) - 1)
                        latency_p95 = round(durations[p95_idx], 1)
                        if latency_p95 > 10000:  # > 10s
                            warnings.append(f"P95 延迟 {latency_p95/1000:.1f}s 偏高")
                            status = "degraded"
    except Exception:
        pass

    # 最终状态判定
    if not llm_key and not embedding_key:
        status = "unhealthy"

    # Phase 3: 借健康检查触发 L2 embedding 缓存的过期清理（项目无独立调度器，
    # 不清理则调小 l2_ttl_hours 后未命中的过期行会持续堆积膨胀 embedding_cache 表）。
    try:
        from src.core.embedding_cache import EmbeddingCache
        cleaned = EmbeddingCache().cleanup_expired()
        if cleaned:
            warnings.append(f"清理 {cleaned} 条过期 embedding 缓存")
    except Exception:
        pass

    # 同步清理过期 trace 记录，避免 operation_logs 里 trace 行无限留存 + 膨胀
    # （trace 默认 trace_enabled=True 会持续写入）。
    try:
        from src.services.trace import QueryTrace
        retention_days = int(Config.get("rag.observability.trace_retention_days", 30) or 30)
        cleaned = QueryTrace.cleanup_old(retention_days)
        if cleaned:
            warnings.append(f"清理 {cleaned} 条过期 trace 记录")
    except Exception:
        pass

    return {
        "status": status,
        "api_keys": {
            "llm": llm_key,
            "embedding": embedding_key,
            "reranker": reranker_key,
        },
        "vector_coverage": round(vector_coverage, 4),
        "tag_coverage": round(tag_coverage, 4),
        "cache_hit_rate": {
            "embedding": round(embedding_hit_rate, 4),
            "rag_cache_size": rag_cache_size,
        },
        "latency_p95_ms": latency_p95,
        "total_documents": total_documents,
        "total_blocks": total_blocks,
        "total_vectors": total_vectors,
        "warnings": warnings,
        "recommendations": [r for r in ([
            "执行 auto_tag 工具对无标签条目进行 LLM 批量自动补标" if tag_coverage < 0.5 else None,
            "标签覆盖率低于 10%，结构化查询和按标签过滤功能将大范围失效" if tag_coverage < 0.1 else None,
        ] if tag_coverage < 0.5 else []) if r is not None],
    }
