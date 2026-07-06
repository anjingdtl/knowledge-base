"""v1.4.0 三处 bug 修复 + 对抗审查加固的回归测试。

P1 trace 落库 / P2 多样性过滤 / P3 L2 TTL，以及对抗审查发现的：
- 审计列表隔离 trace（B 方案）
- trace created_at 时区统一
- tool 名区分（ask vs ask_with_query）
- 缓存命中清空旧 trace_id
- text=None 兜底（防 search 500）
"""
import json

from src.services.db import Database
from src.services.trace import QueryTrace, StageTrace

# ───────────────────────────── P1: trace 全链路 ─────────────────────────────

def _make_trace(trace_id="t-abc123", tool="ask"):
    return QueryTrace(
        trace_id=trace_id,
        tool=tool,
        question="测试问题",
        stages=[StageTrace(name="vector_search", duration_ms=12.3, result_count=3)],
        total_duration_ms=45.6,
    )


def test_p1_trace_save_persists_to_operation_logs():
    """save() 必须把记录真正写进 operation_logs（修复前因列名错误被异常吞掉）。"""
    trace = _make_trace("t-save-1")
    trace.save()

    with Database._instance.get_conn() as conn:
        row = conn.execute(
            "SELECT operation, target_type, target_id, metadata, created_at "
            "FROM operation_logs WHERE target_type='trace' AND target_id=?",
            ("t-save-1",),
        ).fetchone()
    assert row is not None, "trace 未落库（修复前会因 details/timestamp 列不存在而失败被吞）"
    assert row["operation"] == "trace:ask"
    data = json.loads(row["metadata"])
    assert data["trace_id"] == "t-save-1"
    assert data["total_duration_ms"] == 45.6


def test_p1_trace_get_by_id_returns_record():
    """get_by_id() 应精确取回刚写入的 trace。"""
    _make_trace("t-get-1").save()
    got = QueryTrace.get_by_id("t-get-1")
    assert got is not None, "get_by_id 查不到（修复前查 details 错列）"
    assert got["trace_id"] == "t-get-1"
    assert got["tool"] == "ask"


def test_p1_trace_get_by_id_ignores_substring_collision():
    """修复前用 LIKE '%id%' 会误匹配子串；修复后 target_id 精确匹配。"""
    _make_trace("t-prefix").save()
    _make_trace("t-prefix-extra").save()
    got = QueryTrace.get_by_id("t-prefix")
    assert got is not None
    assert got["trace_id"] == "t-prefix"  # 必须是精确那条，不是 -extra


def test_p1_trace_created_at_local_format():
    """trace 的 created_at 必须与审计记录同为本地无偏移格式，保证字符串排序可靠。"""
    from src.repositories.operation_log_repo import OperationLogRepository
    _make_trace("t-tz-1").save()
    repo = OperationLogRepository()
    repo.insert({"operation": "create", "target_type": "knowledge",
                 "target_id": "k-tz", "source": "mcp"})
    with Database._instance.get_conn() as conn:
        rows = conn.execute(
            "SELECT created_at FROM operation_logs WHERE target_id IN ('t-tz-1','k-tz')"
        ).fetchall()
    # 两条记录的 created_at 都不应带时区偏移（+00:00），格式一致
    for r in rows:
        assert "+" not in r["created_at"].split(".")[0], \
            f"created_at 带时区偏移会与审计记录混排错乱: {r['created_at']}"


def test_p1_trace_tool_name_reflected_in_operation():
    """不同入口的 trace 应通过 operation 列区分（trace:ask vs trace:ask_with_query）。"""
    _make_trace("t-tool-1", tool="ask_with_query").save()
    got = QueryTrace.get_by_id("t-tool-1")
    assert got["tool"] == "ask_with_query"
    with Database._instance.get_conn() as conn:
        row = conn.execute(
            "SELECT operation FROM operation_logs WHERE target_type='trace' AND target_id=?",
            ("t-tool-1",),
        ).fetchone()
    assert row["operation"] == "trace:ask_with_query"


def test_p1_health_p95_reads_trace_durations():
    """health.py 的 P95 查询走 metadata/created_at，能读到 trace 耗时（修复前查 details 永远 None）。"""
    for i in range(10):
        QueryTrace(trace_id=f"t-p95-{i}", tool="ask", question="q",
                   stages=[], total_duration_ms=100.0 + i).save()
    from src.services.health import kb_health_check
    report = kb_health_check()
    assert report["latency_p95_ms"] is not None, "P95 未读到 trace（health.py 仍查 details 错列）"
    assert report["latency_p95_ms"] >= 100.0


def test_p1_audit_list_excludes_trace_by_default():
    """审计列表默认排除 trace，避免被观测数据淹没（B 方案）。显式查 trace 仍可查到。"""
    from src.repositories.operation_log_repo import OperationLogRepository
    repo = OperationLogRepository()
    repo.insert({"operation": "create", "target_type": "knowledge",
                 "target_id": "k-audit", "source": "mcp"})
    _make_trace("t-audit-1").save()

    logs = repo.query(limit=50)  # 默认 exclude_trace=True
    ops = [log["operation"] for log in logs]
    assert "create" in ops
    assert not any(o.startswith("trace:") for o in ops), "默认查询不应包含 trace"
    assert repo.count() == 1  # 只有 create，trace 被排除

    # 显式查 trace（target_type='trace'）能查到
    trace_logs = repo.query(target_type="trace")
    assert any(log["operation"].startswith("trace:") for log in trace_logs)


def test_p1_cache_hit_clears_trace_id():
    """RAG 缓存命中时本次未写新 trace，旧 trace_id 必须清空并标 cache_hit。"""
    from src.services.rag_pipeline import RAGService, _rag_cache
    service = RAGService()  # 缓存命中时不执行 pipeline，默认构造即可
    _rag_cache.put("cache-hit-q-unique", {
        "answer": "cached", "sources": [], "trace_id": "stale-id-999",
    })
    try:
        result = service.query("cache-hit-q-unique")
    finally:
        _rag_cache._cache.pop("cache-hit-q-unique", None)
    assert result.get("cache_hit") is True
    assert result.get("trace_id") == "", "缓存命中应清空不代表本次链路的旧 trace_id"


# ──────────────────────────── P2: 多样性过滤 ────────────────────────────────

def test_p2_minhash_distinguishes_single_chars():
    """单字符文本签名必须互异（修复前全 0 → 100% 相似）。"""
    from src.services.search_service import SearchService
    sig_a = SearchService._minhash("A")
    sig_b = SearchService._minhash("B")
    sig_c = SearchService._minhash("C")
    assert sig_a != sig_b != sig_c != sig_a, "单字符 minhash 仍退化成全 0"
    assert SearchService._minhash("") == [0] * len(sig_a)


def test_p2_diversity_filter_keeps_distinct_short_texts():
    """A/B/C 三个不同一字候选不应被合并成 1 个（报告中的核心复现）。"""
    from src.services.search_service import SearchService
    svc = SearchService.__new__(SearchService)
    candidates = [
        {"text": "A", "rrf_score": 0.9},
        {"text": "B", "rrf_score": 0.8},
        {"text": "C", "rrf_score": 0.7},
    ]
    kept = svc._diversity_filter(candidates, threshold=0.8)
    assert len(kept) == 3, f"修复后应保留全部 3 个，实际 {len(kept)}"


def test_p2_diversity_filter_still_merges_true_duplicates():
    """修复不能矫枉过正：真正相同的长文本仍应被去重。"""
    from src.services.search_service import SearchService
    svc = SearchService.__new__(SearchService)
    dup = "这是一段完全相同的长文本内容用于验证多样性去重功能是否仍然生效" * 3
    candidates = [
        {"text": dup, "rrf_score": 0.9},
        {"text": dup, "rrf_score": 0.8},
    ]
    kept = svc._diversity_filter(candidates, threshold=0.8)
    assert len(kept) == 1, "完全相同的长文本应被合并"


def test_p2_diversity_filter_handles_none_text():
    """block content 为 NULL → text=None 不应让多样性过滤崩溃（防 search 500）。"""
    from src.services.search_service import SearchService
    svc = SearchService.__new__(SearchService)
    candidates = [
        {"text": None, "rrf_score": 0.9},   # NULL content
        {"text": "正常文本内容在这里", "rrf_score": 0.8},
    ]
    kept = svc._diversity_filter(candidates, threshold=0.8)  # 不应抛 TypeError
    assert len(kept) >= 1


def test_p2_empty_text_candidates_merge_consistently():
    """多个空文本候选得到相同全 0 签名会合并——合理（无内容），回归保护行为确定。"""
    from src.services.search_service import SearchService
    svc = SearchService.__new__(SearchService)
    candidates = [
        {"text": "", "rrf_score": 0.9},
        {"text": "", "rrf_score": 0.8},
    ]
    kept = svc._diversity_filter(candidates, threshold=0.8)
    assert len(kept) == 1, "空文本无信息差异，合并为 1 个（保留高分）"


# ───────────────────────────── P3: L2 TTL 配置 ──────────────────────────────

def test_p3_embedding_cache_honors_ttl():
    """EmbeddingCache 必须把传入的 ttl_hours 写入每条记录。"""
    from src.core.embedding_cache import EmbeddingCache
    cache = EmbeddingCache(ttl_hours=24)
    assert cache._ttl_hours == 24
    cache.put("hash-1", "model-x", [0.1, 0.2, 0.3])
    with Database._instance.get_conn() as conn:
        row = conn.execute(
            "SELECT ttl_hours FROM embedding_cache WHERE content_hash=? AND model=?",
            ("hash-1", "model-x"),
        ).fetchone()
    assert row["ttl_hours"] == 24, "TTL 未按构造参数写入"


def test_p3_config_l2_ttl_is_wired():
    """rag.cache.l2_ttl_hours 必须被读取（修复前硬编码 168，改配置无效）。"""
    from src.utils.config import Config
    assert int(Config.get("rag.cache.l2_ttl_hours", 168) or 168) == 168
    Config.set("rag.cache.l2_ttl_hours", 72)
    assert int(Config.get("rag.cache.l2_ttl_hours", 168) or 168) == 72


def test_p3_embed_batch_wires_l2_ttl_e2e(monkeypatch):
    """端到端：config 的 l2_ttl_hours 透传到 embed_batch_with_cache 写入的 L2 ttl_hours 列。"""
    from src.services.embedding import EmbeddingService, _l1_cache
    from src.utils.config import Config
    Config.set("rag.cache.l2_ttl_hours", 72)
    Config.set("embedding.model", "e2e-ttl-model")
    _l1_cache._cache.clear()  # 确保 L1 miss，强制走 L2 写入

    svc = EmbeddingService()
    monkeypatch.setattr(svc, "embed_batch",
                        lambda texts, batch_size=20: [[0.1] * 8 for _ in texts])
    svc.embed_batch_with_cache(["e2e-ttl-unique-probe-text"])

    with Database._instance.get_conn() as conn:
        row = conn.execute(
            "SELECT ttl_hours FROM embedding_cache WHERE model=?", ("e2e-ttl-model",)
        ).fetchone()
    assert row is not None, "L2 缓存未写入（端到端链路断裂）"
    assert row["ttl_hours"] == 72, f"端到端 TTL 未透传，实际 {row['ttl_hours']}"
