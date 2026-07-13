"""KnowledgeWorkflowService 编排器 + path_indexer e2e(spec S2)。"""
from unittest.mock import MagicMock

from src.services.db import Database
from src.services.knowledge_workflow import (
    KnowledgeWorkflowService,
    try_knowledge_workflow_compile,
    try_schedule_source_delete,
)
from src.utils.config import Config


def _insert_knowledge(kid="kid-1", title="T", content="# T\nbody"):
    Database.insert_knowledge({
        "id": kid, "title": title, "content": content,
        "source_type": "file", "source_path": "raw/f.md", "file_type": "md",
        "file_size": len(content), "content_hash": "h1",
        "file_created_at": "", "file_modified_at": "",
        "tags": "[]", "version": 1,
        "created_at": "2026-07-02T10:00:00", "updated_at": "2026-07-02T10:00:00",
    })


class FakeCompilers:
    """四个 mock 编译器,记录调用。"""

    def __init__(self):
        self.source = MagicMock()
        self.source.compile.return_value = {
            "status": "compiled", "key_entities": ["A", "B"], "summary": "s", "title": "T",
        }
        self.entity = MagicMock()
        self.entity.update.return_value = {
            "entities_created": 2, "concepts_created": 0, "llm_calls": 2, "contradictions": [],
        }
        self.index = MagicMock()
        self.index.refresh.return_value = {"status": "compiled", "page_count": 1}
        self.log = MagicMock()
        self.log.append.return_value = {"status": "appended"}


def _wiki_first():
    Config.set("knowledge_workflow.mode", "wiki_first")


def _make_svc(fakes):
    return KnowledgeWorkflowService(
        source_compiler=fakes.source, entity_updater=fakes.entity,
        index_compiler=fakes.index, log_compiler=fakes.log,
    )


def test_compile_wiki_first_triggers_all():
    _wiki_first()
    _insert_knowledge()
    fakes = FakeCompilers()
    result = _make_svc(fakes).compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert result["mode"] == "wiki_first"
    fakes.source.compile.assert_called_once_with("kid-1", "2026-07-02T10:00:00")
    fakes.entity.update.assert_called_once()
    fakes.index.refresh.assert_called_once()
    fakes.log.append.assert_called_once()


def test_compile_legacy_skips():
    Config.set("knowledge_workflow.mode", "legacy")
    _insert_knowledge()
    fakes = FakeCompilers()
    result = _make_svc(fakes).compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert result["skipped"] is True
    fakes.source.compile.assert_not_called()


def test_compile_authoring_triggers_all():
    Config.set("knowledge_workflow.mode", "authoring")
    _insert_knowledge()
    fakes = FakeCompilers()
    result = _make_svc(fakes).compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert result["mode"] == "authoring"
    assert result.get("skipped") is not True
    fakes.source.compile.assert_called_once()


def test_compile_verified_skips():
    Config.set("knowledge_workflow.mode", "verified")
    _insert_knowledge()
    fakes = FakeCompilers()
    result = _make_svc(fakes).compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert result["skipped"] is True
    assert result["resolved_mode"] == "verified"
    fakes.source.compile.assert_not_called()


def test_compile_isolates_failure():
    _wiki_first()
    _insert_knowledge()
    fakes = FakeCompilers()
    fakes.source.compile.side_effect = RuntimeError("boom")
    result = _make_svc(fakes).compile("kid-1", ingested_at="2026-07-02T10:00:00")  # 不抛
    assert result["errors"]  # 收集错误
    fakes.index.refresh.assert_called_once()  # 后续阶段继续执行


def test_compile_shadow_mode_runs_after_legacy_workflow():
    """canonical_v2 shadow:legacy 编译照旧,随后运行隔离 shadow 链路。"""
    _wiki_first()
    Config.set("wiki.canonical_v2.mode", "shadow")
    _insert_knowledge()
    fakes = FakeCompilers()
    shadow = MagicMock()
    shadow.run.return_value = {
        "status": "completed",
        "knowledge_id": "kid-1",
        "new_claims": 1,
        "auto_merged": 0,
        "unresolved": 0,
        "conflicts": 0,
        "evidence_missing": 0,
        "page_diff": "[claim:claim_1] created (draft)",
        "llm_calls": 1,
        "latency_ms": 12,
    }

    result = KnowledgeWorkflowService(
        source_compiler=fakes.source,
        entity_updater=fakes.entity,
        index_compiler=fakes.index,
        log_compiler=fakes.log,
        shadow_workflow=shadow,
    ).compile("kid-1", ingested_at="2026-07-02T10:00:00")

    assert result["mode"] == "wiki_first"
    assert result["shadow"]["status"] == "completed"
    fakes.source.compile.assert_called_once()
    fakes.entity.update.assert_called_once()
    fakes.index.refresh.assert_called_once()
    fakes.log.append.assert_called_once()
    shadow.run.assert_called_once()
    call = shadow.run.call_args
    assert call.kwargs["knowledge_id"] == "kid-1"
    assert call.kwargs["source_summary"] == "s"


def test_compile_shadow_failure_is_isolated():
    """shadow 链路失败不得阻断 raw/legacy wiki 编译结果。"""
    _wiki_first()
    Config.set("wiki.canonical_v2.mode", "shadow")
    _insert_knowledge()
    fakes = FakeCompilers()
    shadow = MagicMock()
    shadow.run.side_effect = RuntimeError("shadow boom")

    result = KnowledgeWorkflowService(
        source_compiler=fakes.source,
        entity_updater=fakes.entity,
        index_compiler=fakes.index,
        log_compiler=fakes.log,
        shadow_workflow=shadow,
    ).compile("kid-1", ingested_at="2026-07-02T10:00:00")

    assert result["index"]["status"] == "compiled"
    assert {"stage": "shadow", "error": "shadow boom"} in result["errors"]
    assert "shadow" not in result


def test_compile_canary_mode_runs_after_legacy_workflow():
    """canonical_v2 canary:legacy fallback 保留,随后运行正式 V2 canary 链路。"""
    _wiki_first()
    Config.set("wiki.canonical_v2.mode", "canary")
    _insert_knowledge()
    fakes = FakeCompilers()
    canary = MagicMock()
    canary.run.return_value = {
        "status": "completed",
        "knowledge_id": "kid-1",
        "tx_id": "tx_test",
        "new_claims": 1,
        "auto_publish": False,
    }

    result = KnowledgeWorkflowService(
        source_compiler=fakes.source,
        entity_updater=fakes.entity,
        index_compiler=fakes.index,
        log_compiler=fakes.log,
        canary_workflow=canary,
    ).compile("kid-1", ingested_at="2026-07-02T10:00:00")

    assert result["mode"] == "wiki_first"
    assert result["canary"]["tx_id"] == "tx_test"
    fakes.source.compile.assert_called_once()
    fakes.entity.update.assert_called_once()
    fakes.index.refresh.assert_called_once()
    fakes.log.append.assert_called_once()
    canary.run.assert_called_once()


def test_compile_canary_failure_is_isolated():
    """canary 链路失败不得阻断 legacy fallback 产物。"""
    _wiki_first()
    Config.set("wiki.canonical_v2.mode", "canary")
    _insert_knowledge()
    fakes = FakeCompilers()
    canary = MagicMock()
    canary.run.side_effect = RuntimeError("canary boom")

    result = KnowledgeWorkflowService(
        source_compiler=fakes.source,
        entity_updater=fakes.entity,
        index_compiler=fakes.index,
        log_compiler=fakes.log,
        canary_workflow=canary,
    ).compile("kid-1", ingested_at="2026-07-02T10:00:00")

    assert result["index"]["status"] == "compiled"
    assert {"stage": "canary", "error": "canary boom"} in result["errors"]
    assert "canary" not in result


def test_compile_primary_mode_runs_primary_without_legacy_compilers():
    """canonical_v2 primary:正式 V2 成为主写路径,不再执行 legacy FS 编译器。"""
    _wiki_first()
    Config.set("wiki.canonical_v2.mode", "primary")
    _insert_knowledge()
    fakes = FakeCompilers()
    primary = MagicMock()
    primary.run.return_value = {
        "status": "completed",
        "knowledge_id": "kid-1",
        "tx_id": "tx_primary",
        "new_claims": 1,
    }

    result = KnowledgeWorkflowService(
        source_compiler=fakes.source,
        entity_updater=fakes.entity,
        index_compiler=fakes.index,
        log_compiler=fakes.log,
        primary_workflow=primary,
    ).compile("kid-1", ingested_at="2026-07-02T10:00:00")

    assert result["mode"] == "wiki_first"
    assert result["primary"]["tx_id"] == "tx_primary"
    fakes.source.compile.assert_not_called()
    fakes.entity.update.assert_not_called()
    fakes.index.refresh.assert_not_called()
    fakes.log.append.assert_not_called()
    primary.run.assert_called_once()
    assert primary.run.call_args.kwargs["knowledge_id"] == "kid-1"


def test_compile_primary_failure_is_isolated_without_legacy_writes():
    """primary 链路失败时记录错误,也不回退到 legacy 直接写 canonical。"""
    _wiki_first()
    Config.set("wiki.canonical_v2.mode", "primary")
    _insert_knowledge()
    fakes = FakeCompilers()
    primary = MagicMock()
    primary.run.side_effect = RuntimeError("primary boom")

    result = KnowledgeWorkflowService(
        source_compiler=fakes.source,
        entity_updater=fakes.entity,
        index_compiler=fakes.index,
        log_compiler=fakes.log,
        primary_workflow=primary,
    ).compile("kid-1", ingested_at="2026-07-02T10:00:00")

    assert result["mode"] == "wiki_first"
    assert {"stage": "primary", "error": "primary boom"} in result["errors"]
    assert "primary" not in result
    fakes.source.compile.assert_not_called()
    fakes.entity.update.assert_not_called()
    fakes.index.refresh.assert_not_called()
    fakes.log.append.assert_not_called()


def test_compile_not_found():
    _wiki_first()
    # 不 insert 任何 knowledge
    fakes = FakeCompilers()
    result = _make_svc(fakes).compile("ghost", ingested_at="2026-07-02T10:00:00")
    assert result.get("skipped") is True
    assert result.get("reason") == "not_found"


def test_try_hook_returns_none_without_container(monkeypatch):
    """无 active container 时返回 None,不抛。"""
    monkeypatch.setattr("src.core.container.get_active_container", lambda: None)
    assert try_knowledge_workflow_compile("kid-1") is None


def test_path_indexer_triggers_wiki_first_e2e(tmp_path, monkeypatch):
    """spec S2:ingest 后触发 wiki_first;source summary 已转为建议载荷。"""
    # 1) mock 掉 index_knowledge_item 的向量化,避免 embedding 调用
    import src.services.indexer as indexer_mod
    monkeypatch.setattr(indexer_mod, "index_knowledge_item", lambda item: None)

    # 2) 项目布局
    project = tmp_path / "proj"
    (project / "raw").mkdir(parents=True)
    src_file = project / "raw" / "doc.md"
    src_file.write_text(
        "# Real Doc\n\nThe MCP and LLM APIs are documented.\n", encoding="utf-8"
    )

    # 3) wiki_first 配置
    Config.set("knowledge_workflow.mode", "wiki_first")
    Config.set("knowledge_workflow.wiki_dir", str(project / "wiki"))
    Config.set("knowledge_workflow.source_summary_dir", str(project / "wiki" / "sources"))
    Config.set("knowledge_workflow.entity_dir", str(project / "wiki" / "entities"))
    Config.set("knowledge_workflow.concept_dir", str(project / "wiki" / "concepts"))
    Config.set("wiki.max_llm_calls_per_ingest", 0)  # 关 LLM,纯验证文件系统层

    # 4) 提供 active container,挂真实编排器
    mock_container = MagicMock()
    mock_container.knowledge_workflow = KnowledgeWorkflowService()
    monkeypatch.setattr("src.core.container.get_active_container", lambda: mock_container)

    # 5) ingest
    from src.services.path_indexer import PathIndexService
    svc = PathIndexService(
        db=Database._instance, config=Config, indexed_file_repo=MagicMock()
    )
    svc._ingest_file(src_file)

    # Source summary/index/log 不再绕过 WikiRepository 直接写 markdown。
    assert not (project / "wiki" / "sources").exists(), "source summary 不应直接写入"
    assert not (project / "wiki" / "index.md").exists(), "index.md 不应直接写入"
    assert not (project / "wiki" / "log.md").exists(), "log.md 不应直接写入"


def test_save_query_prepares_syntheses_draft(tmp_path):
    """save_query 准备 syntheses draft,不直接写 markdown。"""
    Config.set("knowledge_workflow.mode", "wiki_first")
    Config.set("knowledge_workflow.wiki_dir", str(tmp_path / "wiki"))
    Config.set("knowledge_workflow.synthesis_dir", str(tmp_path / "wiki" / "syntheses"))
    Config.set("knowledge_workflow.comparison_dir", str(tmp_path / "wiki" / "comparisons"))
    svc = KnowledgeWorkflowService()
    result = svc.save_query(
        question="LLM 与传统搜索的区别?",
        answer="LLM 检索基于语义..." + "x" * 120,
        source_ids=["k1", "k2"],
        confidence=0.8,
        page_type="syntheses",
        save_mode="auto",
        timestamp="2026-07-02T11:00:00",
    )
    assert result["status"] == "prepared"
    assert result["page_type"] == "syntheses"
    assert result["frontmatter"]["status"] == "draft"
    assert result["frontmatter"]["confidence"] == 0.8
    assert result["frontmatter"]["source_ids"] == ["k1", "k2"]
    assert "LLM 检索基于语义" in result["body"]
    assert not (tmp_path / "wiki" / "syntheses").exists()


def test_save_query_prepares_draft_without_writing_markdown(tmp_path):
    """Phase 4C:save_query 不再绕过 WikiRepository 直接写 markdown。"""
    Config.set("knowledge_workflow.mode", "wiki_first")
    Config.set("knowledge_workflow.wiki_dir", str(tmp_path / "wiki"))
    Config.set("knowledge_workflow.synthesis_dir", str(tmp_path / "wiki" / "syntheses"))
    svc = KnowledgeWorkflowService()

    result = svc.save_query(
        question="LLM 与传统搜索的区别?",
        answer="LLM 检索基于语义..." + "x" * 120,
        source_ids=["k1", "k2"],
        confidence=0.8,
        page_type="syntheses",
        save_mode="manual",
        timestamp="2026-07-02T11:00:00",
    )

    assert result["status"] == "prepared"
    assert result["frontmatter"]["status"] == "draft"
    assert result["frontmatter"]["source_ids"] == ["k1", "k2"]
    assert "LLM 检索基于语义" in result["body"]
    assert not (tmp_path / "wiki" / "syntheses").exists()


def test_save_query_auto_below_threshold_skips(tmp_path):
    """confidence < 0.6 + save_mode=auto → 跳过。"""
    Config.set("knowledge_workflow.mode", "wiki_first")
    Config.set("knowledge_workflow.wiki_dir", str(tmp_path / "wiki"))
    svc = KnowledgeWorkflowService()
    result = svc.save_query(
        question="q?", answer="short",
        source_ids=["k1"], confidence=0.3,
        save_mode="auto", timestamp="2026-07-02T11:00:00",
    )
    assert result["status"] == "skipped"


# ---- Phase 5: rebuild 门控触发 ----
def test_compile_primary_auto_rebuild_schedules_update():
    """primary + auto_on_source_update=true → compile 后 scheduler 收到 update。"""
    _wiki_first()
    Config.set("wiki.canonical_v2.mode", "primary")
    Config.set("wiki.rebuild.auto_on_source_update", True)
    _insert_knowledge()
    primary = MagicMock()
    primary.run.return_value = {"status": "ok"}
    scheduler = MagicMock()
    svc = KnowledgeWorkflowService(primary_workflow=primary, rebuild_scheduler=scheduler)
    svc.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    scheduler.schedule.assert_called_once_with("kid-1", "update")


def test_compile_primary_auto_off_does_not_schedule():
    """primary + auto_on_source_update=false(默认) + 空 allowlist → 不 schedule。"""
    _wiki_first()
    Config.set("wiki.canonical_v2.mode", "primary")
    Config.set("wiki.rebuild.auto_on_source_update", False)
    Config.set("wiki.rebuild.auto_allowlist.knowledge_ids", [])
    Config.set("wiki.rebuild.auto_allowlist.source_paths", [])
    _insert_knowledge()
    primary = MagicMock()
    primary.run.return_value = {"status": "ok"}
    scheduler = MagicMock()
    svc = KnowledgeWorkflowService(primary_workflow=primary, rebuild_scheduler=scheduler)
    svc.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    scheduler.schedule.assert_not_called()


def test_compile_primary_allowlist_canary_schedules():
    """primary + auto off + allowlist 命中 knowledge_id → schedule(canary 级)。"""
    _wiki_first()
    Config.set("wiki.canonical_v2.mode", "primary")
    Config.set("wiki.rebuild.auto_on_source_update", False)
    Config.set("wiki.rebuild.auto_allowlist.knowledge_ids", ["kid-1"])
    _insert_knowledge()
    primary = MagicMock()
    primary.run.return_value = {"status": "ok"}
    scheduler = MagicMock()
    svc = KnowledgeWorkflowService(primary_workflow=primary, rebuild_scheduler=scheduler)
    svc.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    scheduler.schedule.assert_called_once_with("kid-1", "update")


def test_try_schedule_source_delete_gated(monkeypatch):
    """auto_on_source_update=true → schedule delete;false + 空 allowlist → 不 schedule。"""
    scheduler = MagicMock()
    mock_container = MagicMock()
    mock_container.wiki_rebuild_scheduler = scheduler
    monkeypatch.setattr("src.core.container.get_active_container", lambda: mock_container)

    Config.set("wiki.rebuild.auto_on_source_update", True)
    try_schedule_source_delete("kid-x")
    scheduler.schedule.assert_called_once_with("kid-x", "delete")

    scheduler.reset_mock()
    Config.set("wiki.rebuild.auto_on_source_update", False)
    Config.set("wiki.rebuild.auto_allowlist.knowledge_ids", [])
    Config.set("wiki.rebuild.auto_allowlist.source_paths", [])
    try_schedule_source_delete("kid-x")
    scheduler.schedule.assert_not_called()


def test_try_schedule_source_delete_no_container_is_safe(monkeypatch):
    """无 active container → 不抛,不 schedule。"""
    monkeypatch.setattr("src.core.container.get_active_container", lambda: None)
    Config.set("wiki.rebuild.auto_on_source_update", True)
    try_schedule_source_delete("kid-x")  # 不抛
