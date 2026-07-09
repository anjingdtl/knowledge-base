"""KnowledgeWorkflowService 编排器 + path_indexer e2e(spec S2)。"""
from pathlib import Path
from unittest.mock import MagicMock

from src.services.db import Database
from src.services.knowledge_workflow import (
    KnowledgeWorkflowService,
    try_knowledge_workflow_compile,
)
from src.services.wiki_slug import read_frontmatter
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
    """spec S2:ingest 后 wiki/sources/ + index.md + log.md 自动出现(e2e)。"""
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

    # S2 三处产物
    sources = list((project / "wiki" / "sources").glob("*.md"))
    assert sources, "source summary 未生成"
    assert (project / "wiki" / "index.md").exists(), "index.md 未生成"
    assert (project / "wiki" / "log.md").exists(), "log.md 未生成"


def test_save_query_writes_syntheses_draft(tmp_path):
    """save_query 写文件系统 syntheses/*.md(draft)+ log。"""
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
    assert result["status"] == "saved"
    p = Path(result["path"])
    assert p.exists()
    fm = read_frontmatter(p)
    assert fm["status"] == "draft"
    assert fm["confidence"] == 0.8
    assert (tmp_path / "wiki" / "log.md").exists()


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
