"""wiki-compilation eval — 在 fixture/项目上产出 5 项指标(spec §6.4 4.3, S5)。

指标:
  - source_coverage:         wiki/sources/ 页数 / knowledge 总数
  - cross_page_update_rate:  有 backlinks 的 wiki 页 / wiki 页总数
  - orphan_page_rate:        orphan wiki 页 / wiki 页总数
  - query_save_rate:         wiki/(syntheses|comparisons)/ 页数 / knowledge 总数
  - stale_claim_ratio:       outdated_claim findings / wiki 页总数

Usage:
    python evals/run_wiki_eval.py --project /path/to/project
    python evals/run_wiki_eval.py --project . --output report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def compute_metrics(
    wiki_dir: Path,
    knowledge_count: int,
    orphan_pages: int,
    total_wiki_pages: int,
    outdated_claims: int,
    query_save_pages: int,
    backlinked_pages: int,
) -> dict:
    """计算 5 项 wiki-compilation 指标(纯函数,可测试)。

    source_coverage 从 wiki_dir/sources/ 实际文件数计算(验证文件系统产物);
    其余指标由调用方(run_on_project)从 lint/DB 提取后传入。
    """
    kc = max(knowledge_count, 1)
    twp = max(total_wiki_pages, 1)
    sources = 0
    if wiki_dir.exists():
        sources_dir = wiki_dir / "sources"
        if sources_dir.exists():
            sources = len(list(sources_dir.glob("*.md")))
    return {
        "source_coverage": round(sources / kc, 4),
        "cross_page_update_rate": round(backlinked_pages / twp, 4),
        "orphan_page_rate": round(orphan_pages / twp, 4),
        "query_save_rate": round(query_save_pages / kc, 4),
        "stale_claim_ratio": round(outdated_claims / twp, 4),
    }


def run_on_project(project_dir: Path) -> dict:
    """对一个已编译的 wiki-first 项目,从文件系统 + lint 提取指标。"""
    from src.services.db import Database
    from src.services.wiki_lint import WikiLint
    from src.utils.config import Config

    Config.load()
    wiki_dir = project_dir / Config.get("knowledge_workflow.wiki_dir", "wiki")

    knowledge_count = len(Database.list_knowledge(limit=10000))

    report = WikiLint().run()
    orphan_pages = sum(1 for f in report["findings"] if f["category"] == "orphan")
    outdated = sum(1 for f in report["findings"] if f["category"] == "outdated_claim")
    total_wiki_pages = report["total_pages"]
    backlinked = total_wiki_pages - sum(
        1 for f in report["findings"] if f["category"] == "missing_backlinks"
    )
    query_save = 0
    for sub in ("syntheses", "comparisons"):
        d = wiki_dir / sub
        if d.exists():
            query_save += len(list(d.glob("*.md")))

    return compute_metrics(
        wiki_dir=wiki_dir, knowledge_count=knowledge_count,
        orphan_pages=orphan_pages, total_wiki_pages=total_wiki_pages,
        outdated_claims=outdated, query_save_pages=query_save,
        backlinked_pages=backlinked,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="wiki-compilation eval (5 metrics)")
    parser.add_argument("--project", default=".", help="项目根目录")
    parser.add_argument("--output", default=None, help="输出 JSON 报告路径")
    args = parser.parse_args(argv)

    metrics = run_on_project(Path(args.project))
    print("wiki-compilation metrics:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    if args.output:
        Path(args.output).write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n报告已写入: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
