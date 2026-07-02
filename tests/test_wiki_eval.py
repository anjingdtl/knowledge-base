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
