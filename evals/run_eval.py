"""RAG 评测运行器

用法:
    python evals/run_eval.py --dataset evals/datasets/basic_qa.yaml
    python evals/run_eval.py --all
    python evals/run_eval.py --all --report markdown
    python evals/run_eval.py --all --report json --output evals/results.json

评测流程:
1. 加载数据集 (YAML)
2. 初始化 RAG 管线 (通过 AppContainer)
3. 逐条执行查询，记录结果
4. 计算指标并输出报告
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import yaml

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.metrics import EvalMetrics, SingleResult, aggregate_metrics  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rag-eval")

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"

ALL_DATASETS = [
    ("basic_qa", "基础问答", "basic"),
    ("table_qa", "表格问答", "table"),
    ("graph_qa", "图谱问答", "graph"),
    ("no_answer", "无答案测试", "no_answer"),
]


def load_dataset(path: Path) -> list[dict]:
    """加载 YAML 数据集"""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        raise ValueError(f"Dataset must be a YAML list, got {type(data)}")
    return data


def run_single_query(rag_service, item: dict) -> SingleResult:
    """执行单条评测查询"""
    question = item["question"]
    relevant_ids = item.get("relevant_knowledge_ids", [])

    t0 = time.monotonic()
    result = SingleResult(
        question=question,
        relevant_knowledge_ids=relevant_ids,
    )

    try:
        response = rag_service.query(question)
        result.answer = response.get("answer", "")
        result.sources = response.get("sources", [])
        result.route = str(response.get("route", {}))
        result.warnings = response.get("warnings", [])
    except Exception as e:
        logger.error("Query failed for %r: %s", question[:50], e)
        result.error = str(e)
    finally:
        result.latency = time.monotonic() - t0

    return result


def run_dataset(
    rag_service,
    dataset_path: Path,
    dataset_type: str = "basic",
    max_items: int | None = None,
) -> tuple[list[SingleResult], EvalMetrics]:
    """运行单个数据集评测"""
    items = load_dataset(dataset_path)
    if max_items:
        items = items[:max_items]

    logger.info("Running dataset %s (%d items)", dataset_path.name, len(items))
    results = []
    for i, item in enumerate(items):
        logger.info("  [%d/%d] %s", i + 1, len(items), item["question"][:60])
        r = run_single_query(rag_service, item)
        results.append(r)

    metrics = aggregate_metrics(results, dataset_type=dataset_type)
    return results, metrics


def format_metrics_table(all_metrics: list[tuple[str, str, EvalMetrics]]) -> str:
    """格式化 Markdown 指标表格"""
    lines = []
    lines.append("# RAG 评测报告")
    lines.append("")
    lines.append("## 总览")
    lines.append("")

    # 总览表
    lines.append("| 数据集 | 类型 | 问题数 | 已回答 | 拒答 | Recall@5 | Recall@10 | MRR | 引用准确率 | 忠实度 | 拒答准确率 |")
    lines.append("|--------|------|--------|--------|------|----------|-----------|-----|-----------|--------|-----------|")
    for name, desc, m in all_metrics:
        lines.append(
            f"| {desc} | {name} | {m.total_questions} | {m.total_answered} | {m.total_refused} "
            f"| {m.recall_at_5:.2f} | {m.recall_at_10:.2f} | {m.mrr:.2f} "
            f"| {m.citation_accuracy:.2f} | {m.faithfulness:.2f} | {m.no_answer_accuracy:.2f} |"
        )

    lines.append("")
    lines.append("## 延迟统计")
    lines.append("")
    lines.append("| 数据集 | P50 (s) | P95 (s) | Mean (s) |")
    lines.append("|--------|---------|---------|----------|")
    for name, desc, m in all_metrics:
        lines.append(f"| {desc} | {m.latency_p50:.2f} | {m.latency_p95:.2f} | {m.latency_mean:.2f} |")

    lines.append("")
    lines.append("---")
    lines.append("*报告由 `evals/run_eval.py` 自动生成*")
    return "\n".join(lines)


def format_metrics_json(all_metrics: list[tuple[str, str, EvalMetrics]]) -> str:
    """格式化 JSON 输出"""
    output = {}
    for name, desc, m in all_metrics:
        output[name] = {
            "description": desc,
            "total_questions": m.total_questions,
            "total_answered": m.total_answered,
            "total_refused": m.total_refused,
            "recall_at_5": round(m.recall_at_5, 4),
            "recall_at_10": round(m.recall_at_10, 4),
            "mrr": round(m.mrr, 4),
            "citation_accuracy": round(m.citation_accuracy, 4),
            "faithfulness": round(m.faithfulness, 4),
            "no_answer_accuracy": round(m.no_answer_accuracy, 4),
            "latency_p50": round(m.latency_p50, 4),
            "latency_p95": round(m.latency_p95, 4),
            "latency_mean": round(m.latency_mean, 4),
        }
    return json.dumps(output, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="RAG 评测运行器")
    parser.add_argument("--dataset", type=str, help="单个数据集路径 (YAML)")
    parser.add_argument("--all", action="store_true", help="运行所有数据集")
    parser.add_argument("--report", choices=["text", "markdown", "json"], default="text", help="报告格式")
    parser.add_argument("--output", type=str, help="输出文件路径 (配合 --report json/markdown)")
    parser.add_argument("--max-items", type=int, help="每个数据集最多运行 N 条")
    args = parser.parse_args()

    if not args.dataset and not args.all:
        parser.error("请指定 --dataset 或 --all")

    # 初始化容器
    logger.info("Initializing container...")
    from src.core.container import create_container
    container = create_container()
    rag_service = container.rag_pipeline

    all_metrics: list[tuple[str, str, EvalMetrics]] = []

    if args.all:
        for ds_name, ds_desc, ds_type in ALL_DATASETS:
            ds_path = DATASETS_DIR / f"{ds_name}.yaml"
            if not ds_path.exists():
                logger.warning("Dataset not found: %s", ds_path)
                continue
            results, metrics = run_dataset(rag_service, ds_path, ds_type, args.max_items)
            all_metrics.append((ds_name, ds_desc, metrics))
    else:
        ds_path = Path(args.dataset)
        # 猜测类型
        ds_type = "basic"
        for name, _, dtype in ALL_DATASETS:
            if name in ds_path.stem:
                ds_type = dtype
                break
        results, metrics = run_dataset(rag_service, ds_path, ds_type, args.max_items)
        all_metrics.append((ds_path.stem, ds_path.stem, metrics))

    # 输出报告
    if args.report == "json":
        report = format_metrics_json(all_metrics)
    elif args.report == "markdown":
        report = format_metrics_table(all_metrics)
    else:
        # 文本格式
        lines = []
        for name, desc, m in all_metrics:
            lines.append(f"\n{'='*60}")
            lines.append(f"数据集: {desc} ({name})")
            lines.append(f"{'='*60}")
            lines.append(f"  问题数: {m.total_questions}")
            lines.append(f"  已回答: {m.total_answered}")
            lines.append(f"  拒答:   {m.total_refused}")
            lines.append(f"  Recall@5:  {m.recall_at_5:.4f}")
            lines.append(f"  Recall@10: {m.recall_at_10:.4f}")
            lines.append(f"  MRR:       {m.mrr:.4f}")
            lines.append(f"  引用准确率: {m.citation_accuracy:.4f}")
            lines.append(f"  忠实度:    {m.faithfulness:.4f}")
            lines.append(f"  拒答准确率: {m.no_answer_accuracy:.4f}")
            lines.append(f"  延迟 P50:  {m.latency_p50:.2f}s")
            lines.append(f"  延迟 P95:  {m.latency_p95:.2f}s")
            lines.append(f"  延迟 Mean: {m.latency_mean:.2f}s")
        report = "\n".join(lines)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        logger.info("Report written to %s", args.output)
    else:
        print(report)

    # 清理
    from src.core.container import shutdown_container
    shutdown_container(container)


if __name__ == "__main__":
    main()
