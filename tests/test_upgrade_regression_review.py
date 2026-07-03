"""大规模升级回归 Review — 修复回归测试。

锁定本轮 review 修复的真实缺陷,防止退化。每条测试对应计划文档
(docs/superpowers/plans/2026-07-03-knowledge-base-upgrade-regression-review.md)
中的一个 fix 项。
"""
import asyncio

import pytest

from src.services.lexical_zh import LexicalZh
from src.services.rag_pipeline import (
    RagContext,
    RAGService,
    VectorSearchStage,
    _RAGResultCache,
)

# ---------------------------------------------------------------------------
# S1.2 — LRU 缓存深拷贝隔离(防调用方 mutate 嵌套结构污染缓存)
# ---------------------------------------------------------------------------

def test_rag_cache_deepcopy_isolation():
    """get 返回深拷贝:调用方改嵌套结构不得污染缓存。"""
    cache = _RAGResultCache(maxsize=4, ttl=600)
    payload = {"answer": "A", "sources": [{"id": "s1"}], "warnings": []}
    cache.put("q1", payload)

    got = cache.get("q1")
    assert got is not None
    # 调用方 mutate 返回值的嵌套结构
    got["sources"].append({"id": "s2"})
    got["warnings"].append("polluted")
    got["answer"] = "B"

    # 缓存里的内容必须保持原样
    again = cache.get("q1")
    assert again["answer"] == "A"
    assert again["sources"] == [{"id": "s1"}]
    assert again["warnings"] == []


def test_rag_cache_put_then_mutate_original_does_not_pollute():
    """put 后调用方继续 mutate 原 dict 引用也不得污染缓存。"""
    cache = _RAGResultCache(maxsize=4, ttl=600)
    payload = {"answer": "A", "sources": [{"id": "s1"}]}
    cache.put("q1", payload)
    # 保留原引用并 mutate
    payload["sources"].append({"id": "polluted"})
    payload["answer"] = "X"

    got = cache.get("q1")
    assert got["answer"] == "A"
    assert got["sources"] == [{"id": "s1"}]


# ---------------------------------------------------------------------------
# S1.5 — lexical_zh 词边界匹配(防 Latin 子串假阳性污染 FTS 召回)
# ---------------------------------------------------------------------------

def _lexical_with_synonyms(synonyms):
    lx = LexicalZh(config={"rag": {"lexical_zh": {"enabled": True}}})
    lx._synonyms = dict(synonyms)
    return lx


def test_lexical_latin_word_not_matched_as_substring():
    """「AI」不应匹配进「available」(子串假阳性)。"""
    lx = _lexical_with_synonyms({"AI": ["人工智能"]})
    # 旧实现 ``"AI" in "available"`` 为 True → 误注入;修复后必须不扩展
    assert lx.expand_query("available options report") == "available options report"


def test_lexical_latin_word_matched_as_standalone():
    """「AI」作为独立词出现时应扩展同义词。"""
    lx = _lexical_with_synonyms({"AI": ["人工智能"]})
    out = lx.expand_query("AI 是什么")
    assert "人工智能" in out


def test_lexical_latin_word_adjacent_to_cjk_matches():
    """「FTTR是什么」中 FTTR 紧邻 CJK,仍应命中(Latin 词非被 Latin 字母数字包裹)。"""
    lx = _lexical_with_synonyms({"FTTR": ["光纤到房间"]})
    out = lx.expand_query("FTTR是什么")
    assert "光纤到房间" in out


def test_lexical_cjk_word_substring_match():
    """CJK 词保持子串匹配(无词边界)。"""
    lx = _lexical_with_synonyms({"创智杯": ["比赛"]})
    out = lx.expand_query("关于创智杯通知的说明")
    assert "比赛" in out


# ---------------------------------------------------------------------------
# S1.1 — blend_fusion 失败时保留 hybrid 候选(不丢全部候选)
# ---------------------------------------------------------------------------

class _FakeSearcher:
    def __init__(self, results):
        self._results = results

    def search(self, queries, top_k):
        return [dict(r) for r in self._results]


def test_blend_fusion_failure_preserves_hybrid_candidates(monkeypatch):
    """blend_fusion 抖动抛异常时,已算出的 hybrid 候选必须保留,不能被外层 except 清空。"""
    from src.services import agentic_router
    from src.services import blend_fusion as bf_mod

    # 让 agentic 路由直接失败 → 内层 except → 走 hybrid 检索路径(确定性、无 LLM)
    monkeypatch.setattr(
        agentic_router.AgenticRouter, "route",
        lambda self, q: (_ for _ in ()).throw(RuntimeError("no llm in test")),
    )
    # 让 blend_fusion 抛异常(模拟 fusion 抖动)
    def _boom(wiki, hybrid):
        raise RuntimeError("blend fusion boom")
    monkeypatch.setattr(bf_mod, "blend_fusion", _boom)

    hybrid_results = [{
        "id": "b1", "text": "hybrid hit",
        "metadata": {"page_id": "p1"}, "rrf_score": 0.9,
    }]
    stage = VectorSearchStage(db=None, hybrid_search=_FakeSearcher(hybrid_results), llm=None)

    ctx = RagContext(question="测试查询", rewritten_queries=["测试查询"])
    ctx.metadata["scale"] = "blend"
    ctx.metadata["_blend_wiki_candidates"] = [
        {"id": "wiki:src:x", "text": "wiki", "metadata": {"page_id": "w1"}},
    ]

    result = asyncio.run(stage.execute(ctx, {"enabled": True, "top_k": 5}))

    # 关键断言:候选保留(hybrid 命中),未因 fusion 失败被清空
    assert len(result.candidates) >= 1
    assert result.candidates[0]["text"] == "hybrid hit"
    # 警告标记 fusion 失败
    assert any("blend_fusion_failed" in w for w in result.metadata.get("warnings", []))


# ---------------------------------------------------------------------------
# S1.4 — query() 管线异常向上传播(不再盲目 fallback _direct_query 二次调 LLM)
# ---------------------------------------------------------------------------

class _ThrowingPipeline:
    async def execute(self, question, conversation_history=None):
        raise RuntimeError("pipeline stage boom")


def test_query_propagates_pipeline_exception(monkeypatch):
    """管线抛非超时异常时,query() 必须向上传播,不调 _direct_query。"""
    service = RAGService(deps={})
    service._pipeline = _ThrowingPipeline()

    # 若退化回 _direct_query,这里会触发 AssertionError
    monkeypatch.setattr(
        service, "_direct_query",
        lambda *a, **kw: pytest.fail("query() must not fall back to _direct_query"),
    )

    with pytest.raises(RuntimeError, match="pipeline stage boom"):
        service.query("some question")


# ---------------------------------------------------------------------------
# S2 段 — Wiki 编译 + 数据层 + 迁移
# ---------------------------------------------------------------------------

def _load_i001():
    """按文件路径加载 alembic i001 迁移(避开项目 alembic/ 目录与包同名)。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "i001_under_test", "alembic/versions/i001_version_conflict.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_i001_upgrade_idempotent_after_app_schema(tmp_path):
    """S2.1:app schema 已建 conflict_* 表后,alembic i001 upgrade 必须幂等不报错。

    复现必现 bug:db._SCHEMA 用 CREATE TABLE IF NOT EXISTS 建表,旧 i001 用
    op.create_table(无 IF NOT EXISTS)→ alembic upgrade head 报 table already exists。
    """
    import sqlite3

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    from src.services.db import _SCHEMA

    db_path = tmp_path / "t.db"
    # 模拟 app 启动:原生 sqlite3 executescript(_SCHEMA) 建全部表(含 conflict_*)
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)
    raw.close()

    engine = create_engine(f"sqlite:///{db_path}")
    i001 = _load_i001()
    # 表+索引均已存在,upgrade 必须幂等(不抛 table already exists),且可重入
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            i001.upgrade()
            i001.upgrade()  # 二次重入同样幂等


def test_resolve_slug_empty_hash_does_not_overwrite_unrelated(tmp_path):
    """S2.2:空 source_hash 不得判为幂等覆盖(否则覆盖不相关同名源页)。"""
    from src.services.wiki_slug import resolve_slug, write_markdown

    # 已存在同名页,frontmatter source_hash 为空
    write_markdown(tmp_path / "foo.md", {"title": "foo", "source_hash": ""}, "body A")
    # 新条目同样空 hash、同名 → 必须走冲突后缀,而非覆盖原页
    slug, path = resolve_slug(tmp_path, "foo", source_hash="")
    assert path != tmp_path / "foo.md"
    assert slug.startswith("foo-")
    # 原页内容未被触碰
    assert "body A" in (tmp_path / "foo.md").read_text(encoding="utf-8")


def test_resolve_slug_nonempty_hash_idempotent_match(tmp_path):
    """S2.2 回归:非空 hash 一致仍走幂等覆盖(不应被修复破坏)。"""
    from src.services.wiki_slug import resolve_slug, write_markdown

    write_markdown(tmp_path / "foo.md", {"title": "foo", "source_hash": "abc12345"}, "body")
    slug, path = resolve_slug(tmp_path, "foo", source_hash="abc12345")
    assert path == tmp_path / "foo.md"
    assert slug == "foo"


def test_write_markdown_atomic_no_temp_leftover(tmp_path):
    """S2.6:原子写后无临时文件残留,内容正确。"""
    from src.services.wiki_slug import write_markdown

    target = tmp_path / "sub" / "page.md"
    write_markdown(target, {"title": "x"}, "hello body")
    text = target.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "hello body" in text
    # 无残留临时文件
    assert not list((tmp_path / "sub").glob("*.tmp"))


def test_migrator_backup_preserves_old_when_copytree_fails(tmp_path, monkeypatch):
    """S2.5b:copytree 中途失败时,旧备份必须完好(不可被删后丢)。"""
    import shutil

    from src.services.migrator import MigrationService
    from src.utils.config import Config

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "x.txt").write_text("x")
    # 已存在的旧备份(含重要内容)
    backup_path = tmp_path / "data.backup"
    backup_path.mkdir()
    (backup_path / "old.txt").write_text("old backup content")

    Config.load()
    Config.set("storage.data_dir", str(data_dir))
    Config.set("storage.db_name", "t.db")

    # copytree 模拟中途失败
    monkeypatch.setattr(
        shutil, "copytree",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full mid-copy")),
    )

    svc = MigrationService(project_dir=tmp_path)
    with pytest.raises(RuntimeError, match="disk full mid-copy"):
        svc.apply(backup=True)

    # 关键:旧备份未被删除,内容完好
    assert (backup_path / "old.txt").read_text() == "old backup content"
    # 临时备份被清理
    assert not (tmp_path / "data.backup.tmp").exists()


# ---------------------------------------------------------------------------
# S3 段 — MCP Server + 工具契约
# ---------------------------------------------------------------------------

def test_do_ask_catches_non_timeout_exception(monkeypatch):
    """S3.2:_do_ask 必须兜住非超时异常(S1.4 后 query() 会传播),返回结构化部分结果,
    不冒泡成未处理 MCP 错误。"""
    from src import mcp_server

    container = mcp_server._get_container()

    def _boom(question, timeout=None, **kwargs):
        raise RuntimeError("simulated pipeline failure")
    monkeypatch.setattr(container.rag_pipeline, "query", _boom)

    result = mcp_server._do_ask("any question")
    assert result["route"]["mode"] == "error"
    assert any("simulated pipeline failure" in w for w in result["warnings"])
    assert result["answer"] == ""
    assert result["sources"] == []


def test_get_operation_log_service_uses_active_container(monkeypatch):
    """S3.3:有活跃 container 时返回 container.operation_log。

    旧实现调 src.api.deps.get_container()(签名需 request 参数)→ 必 TypeError →
    永远走 except fallback,容器注入路径成死代码。改用 get_active_container 后才生效。
    """
    from src.services import version_conflict as vc_mod

    fake_op_log = object()  # sentinel

    class _FakeContainer:
        operation_log = fake_op_log

    monkeypatch.setattr("src.core.container.get_active_container",
                        lambda: _FakeContainer())

    svc = vc_mod.VersionConflictService.__new__(vc_mod.VersionConflictService)
    assert svc._get_operation_log_service() is fake_op_log


def test_get_operation_log_service_fallback_when_no_container(monkeypatch):
    """S3.3 回归:无活跃 container 时降级自建 OperationLogService(不抛)。"""
    from src.services import version_conflict as vc_mod
    from src.services.operation_log import OperationLogService

    monkeypatch.setattr("src.core.container.get_active_container", lambda: None)

    svc = vc_mod.VersionConflictService.__new__(vc_mod.VersionConflictService)
    svc._knowledge_repo = None
    ol = svc._get_operation_log_service()
    assert isinstance(ol, OperationLogService)


# ---------------------------------------------------------------------------
# S4 段 — 安全(SSRF 重定向绕过)
# ---------------------------------------------------------------------------

def test_assert_safe_host_rejects_internal_addresses():
    """S4.1:_assert_safe_host 拒绝内网/回环/链路本地地址。"""
    from src.services.file_parser import _assert_safe_host

    with pytest.raises(ValueError, match="内网"):
        _assert_safe_host("127.0.0.1")          # 回环
    with pytest.raises(ValueError, match="内网"):
        _assert_safe_host("localhost")           # 解析到 127.0.0.1/::1
    with pytest.raises(ValueError, match="内网"):
        _assert_safe_host("169.254.169.254")     # 云元数据(链路本地)


def test_parse_url_rejects_internal_url_before_request():
    """S4.1:parse_url 对内网 URL 在发请求前即拒绝(初始校验)。"""
    from src.services.file_parser import parse_url

    with pytest.raises(ValueError, match="内网"):
        parse_url("http://127.0.0.1/secret")
