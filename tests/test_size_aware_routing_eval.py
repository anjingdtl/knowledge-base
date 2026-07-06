"""size_aware 路由准确率 eval 测试(Phase2 W4 Task 4.1)。"""
from __future__ import annotations

from evals.run_retrieval_eval import run_routing_eval


def test_run_routing_eval_returns_accuracy(tmp_path):
    """无 wiki/ 时所有查询被判 full_search(命中 0),数据集全标 full_search → accuracy=1.0。"""
    dataset = tmp_path / "routing.yaml"
    dataset.write_text(
        "- query: '对比 A 与 B'\n"
        "  expected_scale: 'full_search'\n"
        "- query: '哪些内容'\n"
        "  expected_scale: 'full_search'\n",
        encoding="utf-8",
    )
    result = run_routing_eval(dataset_path=dataset, wiki_dir=tmp_path / "nowhere")
    assert result["total"] == 2
    assert result["correct"] == 2
    assert result["accuracy"] == 1.0


def test_run_routing_eval_reports_mismatch(tmp_path):
    """期望 wiki_read 但实际 full_search(无 wiki/) → 记 mismatch,accuracy=0。"""
    dataset = tmp_path / "routing.yaml"
    dataset.write_text(
        "- query: 'embedding 维度'\n  expected_scale: 'wiki_read'\n",
        encoding="utf-8",
    )
    result = run_routing_eval(dataset_path=dataset, wiki_dir=tmp_path / "nowhere")
    assert result["total"] == 1
    assert result["correct"] == 0
    assert result["accuracy"] == 0.0
    assert len(result["details"]) == 1
    assert result["details"][0]["actual_scale"] == "full_search"
