"""wiki-compilation eval 指标计算测试(spec S5)。"""
from pathlib import Path

from evals.run_wiki_eval import compute_metrics


def test_source_coverage(tmp_path):
    """source_coverage = wiki/sources 页数 / knowledge 总数。"""
    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    (wiki / "sources" / "a.md").write_text("---\ntitle: A\n---\n", encoding="utf-8")
    metrics = compute_metrics(
        wiki_dir=wiki, knowledge_count=4, orphan_pages=0, total_wiki_pages=1,
        outdated_claims=0, query_save_pages=0, backlinked_pages=1,
    )
    assert metrics["source_coverage"] == 0.25


def test_orphan_and_cross_page_rates():
    """orphan_page_rate = orphan/total; cross_page_update_rate = backlinked/total。"""
    metrics = compute_metrics(
        wiki_dir=Path("/nonexist"), knowledge_count=0,
        orphan_pages=2, total_wiki_pages=5, outdated_claims=0,
        query_save_pages=0, backlinked_pages=3,
    )
    assert metrics["orphan_page_rate"] == 0.4
    assert metrics["cross_page_update_rate"] == 0.6


def test_stale_claim_ratio():
    """stale_claim_ratio = outdated_claims / total_wiki_pages。"""
    metrics = compute_metrics(
        wiki_dir=Path("/nonexist"), knowledge_count=0,
        orphan_pages=0, total_wiki_pages=10, outdated_claims=3,
        query_save_pages=0, backlinked_pages=0,
    )
    assert metrics["stale_claim_ratio"] == 0.3


def test_query_save_rate(tmp_path):
    """query_save_rate = syntheses+comparisons 页 / knowledge。"""
    wiki = tmp_path / "wiki"
    (wiki / "syntheses").mkdir(parents=True)
    (wiki / "syntheses" / "s1.md").write_text("x", encoding="utf-8")
    (wiki / "comparisons").mkdir(parents=True)
    (wiki / "comparisons" / "c1.md").write_text("x", encoding="utf-8")
    metrics = compute_metrics(
        wiki_dir=wiki, knowledge_count=4, orphan_pages=0, total_wiki_pages=2,
        outdated_claims=0, query_save_pages=2, backlinked_pages=0,
    )
    assert metrics["query_save_rate"] == 0.5


# ---------------------------------------------------------------------------
# Gap B(Phase2 W4):run_on_project 按 --source 选 fs/sqlite 引擎
# ---------------------------------------------------------------------------

def _patch_config(monkeypatch, mode):
    """注入 Config.get/load,避免依赖真实 config.yaml。"""
    from src.utils.config import Config
    monkeypatch.setattr(Config, "load", lambda *a, **k: None)
    monkeypatch.setattr(Config, "get", lambda key, default=None: {
        "knowledge_workflow.wiki_dir": "wiki",
        "knowledge_workflow.mode": mode,
    }.get(key, default))


def test_run_on_project_fs_source_uses_wiki_fs_lint(tmp_path, monkeypatch):
    """--source fs 用 WikiFsLint 实扫 wiki/*.md(非 SQLite)。"""
    from evals.run_wiki_eval import run_on_project
    from src.services.db import Database
    from src.services.wiki_slug import write_markdown

    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    write_markdown(wiki / "sources" / "a.md",
                   {"title": "A", "knowledge_id": "k1", "source_hash": "h"}, "正文")
    _patch_config(monkeypatch, "wiki_first")
    # Database 经 _DatabaseMeta 代理,monkeypatch 时会前置位置参数 → 用 *a,**k 吞掉
    monkeypatch.setattr(Database, "list_knowledge", lambda *a, **k: [])

    metrics = run_on_project(tmp_path, source="fs")
    # fs 引擎扫到 1 个 sources 页,knowledge_count=0 → source_coverage = 1/max(0,1) = 1.0
    assert isinstance(metrics["source_coverage"], float)
    assert metrics["source_coverage"] == 1.0


def test_run_on_project_auto_picks_fs_for_wiki_first(tmp_path, monkeypatch):
    """source=auto + mode=wiki_first → 走 fs 引擎。"""
    import evals.run_wiki_eval as rwe
    from evals.run_wiki_eval import run_on_project
    from src.services.db import Database

    called = {"engine": None}

    class _FakeFsLint:
        def __init__(self, wiki_dir=None):
            pass

        def run(self):
            called["engine"] = "fs"
            return {"total_pages": 0, "healthy_pages": 0, "score": 1.0, "findings": []}

    monkeypatch.setattr(rwe, "WikiFsLint", _FakeFsLint)
    _patch_config(monkeypatch, "wiki_first")
    # Database 经 _DatabaseMeta 代理,monkeypatch 时会前置位置参数 → 用 *a,**k 吞掉
    monkeypatch.setattr(Database, "list_knowledge", lambda *a, **k: [])

    run_on_project(tmp_path, source="auto")
    assert called["engine"] == "fs"


def test_run_on_project_auto_picks_sqlite_for_legacy(tmp_path, monkeypatch):
    """source=auto + mode=legacy → 走 SQLite WikiLint(不调 fs 引擎)。"""
    import evals.run_wiki_eval as rwe
    from evals.run_wiki_eval import run_on_project
    from src.services.db import Database

    fs_called = {"n": 0}

    class _FakeFsLint:
        def __init__(self, wiki_dir=None):
            pass

        def run(self):
            fs_called["n"] += 1
            return {"total_pages": 0, "healthy_pages": 0, "score": 1.0, "findings": []}

    monkeypatch.setattr(rwe, "WikiFsLint", _FakeFsLint)
    _patch_config(monkeypatch, "legacy")
    # Database 经 _DatabaseMeta 代理,monkeypatch 时会前置位置参数 → 用 *a,**k 吞掉
    monkeypatch.setattr(Database, "list_knowledge", lambda *a, **k: [])

    run_on_project(tmp_path, source="auto")
    assert fs_called["n"] == 0  # legacy 不走 fs
