"""批量自动打标脚本 — 修复 kb_route_query 路由 100% 退化问题。

50轮 MCP 测试报告 Bug-1：标签覆盖率仅 3.7%（135 条文档中仅 5 条有标签），
导致 kb_route_query 全部 fallback 到 hybrid search，路由功能完全退化。

本脚本复用 MCP auto_tag 工具的核心逻辑，对知识库中所有无标签文档执行
批量 LLM 自动打标，目标将标签覆盖率提升至 80% 以上。

用法:
    python scripts/auto_tag_batch.py                # 默认处理全部无标签文档
    python scripts/auto_tag_batch.py --limit 50     # 仅处理前 50 条
    python scripts/auto_tag_batch.py --force        # 强制重新打标（含已有标签）
    python scripts/auto_tag_batch.py --dry-run      # 仅统计不写入

退出码: 0 成功 / 1 部分失败 / 2 致命错误
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.container import create_container  # noqa: E402
from src.utils.config import Config  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="批量自动打标，修复 kb_route_query 路由退化（50轮测试报告 Bug-1）"
    )
    p.add_argument(
        "--limit", type=int, default=500,
        help="单次最多处理的条目数（默认 500）",
    )
    p.add_argument(
        "--force", action="store_true",
        help="强制重新打标（包括已有标签的条目）",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="仅统计无标签条目数，不调用 LLM、不写入数据库",
    )
    return p.parse_args()


def _build_tag_prompt(title: str, content_preview: str, existing_tags: list[str]) -> str:
    return (
        "你是一个知识库标签专家。请根据以下文档的标题和内容摘要，"
        "生成 1-3 个标签（中英文皆可，优先中文）。\n"
        "标签应该：简洁（2-6个字）、准确反映主题、便于检索和分类。\n"
        "只输出 JSON 数组，例如：[\"Python\", \"FastAPI\", \"后端开发\"]\n\n"
        f"标题：{title}\n"
        f"内容摘要：{content_preview}\n\n"
        f"{'已有标签：' + ', '.join(existing_tags) if existing_tags else ''}"
    )


def _strip_markdown_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0] if "```" in text else text
    return text.strip()


def main() -> int:
    args = _parse_args()

    print("=" * 60)
    print("批量自动打标 — 修复 kb_route_query 路由退化")
    print("=" * 60)

    container = create_container()
    db = container.db
    llm = container.llm

    if llm is None:
        print("[致命] LLM 服务未初始化，无法执行自动打标。请检查 config.yaml llm.* 配置。")
        return 2

    # 统计当前标签覆盖率
    total_rows = db.conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE deleted_at IS NULL"
    ).fetchone()[0]
    tagged_rows = db.conn.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE deleted_at IS NULL AND tags IS NOT NULL AND tags != '' AND tags != '[]'"
    ).fetchone()[0]
    coverage = (tagged_rows / total_rows * 100) if total_rows else 0.0
    print(f"当前状态: 总文档 {total_rows} 条, 已打标 {tagged_rows} 条, 覆盖率 {coverage:.1f}%")

    if args.dry_run:
        print("[dry-run] 仅统计模式，不执行打标。")
        return 0

    # 获取待打标条目
    if args.force:
        rows = db.conn.execute(
            "SELECT id, title, content, tags FROM knowledge_items "
            "WHERE deleted_at IS NULL LIMIT ?",
            (args.limit,),
        ).fetchall()
    else:
        rows = db.conn.execute(
            "SELECT id, title, content, tags FROM knowledge_items "
            "WHERE deleted_at IS NULL AND (tags IS NULL OR tags = '' OR tags = '[]') "
            "LIMIT ?",
            (args.limit,),
        ).fetchall()

    if not rows:
        print("没有需要打标的条目（所有条目已有标签）。")
        return 0

    print(f"待打标条目: {len(rows)} 条")
    print("-" * 60)

    tagged_count = 0
    skipped_count = 0
    errors: list[str] = []
    tags_applied: set[str] = set()

    for idx, row in enumerate(rows, 1):
        try:
            kid = row["id"]
            title = row["title"] or ""
            content_preview = (row["content"] or "")[:500]

            existing_tags_str = row["tags"] or ""
            existing_tags: list[str] = []
            if existing_tags_str and existing_tags_str != "[]":
                try:
                    parsed = json.loads(existing_tags_str)
                    if isinstance(parsed, list):
                        existing_tags = parsed
                except json.JSONDecodeError:
                    existing_tags = []

            prompt = _build_tag_prompt(title, content_preview, existing_tags)
            messages = [{"role": "user", "content": prompt}]

            # BUG-1 fix: 必须传 messages list，而非字符串 prompt
            if hasattr(llm, "chat_with_usage"):
                response_text = llm.chat_with_usage(messages, silent=True)[0]
            else:
                response_text = llm.chat(messages, silent=True)

            response_text = _strip_markdown_fence(response_text)
            new_tags = json.loads(response_text)
            if not isinstance(new_tags, list):
                raise ValueError(f"LLM 返回了非数组格式: {type(new_tags)}")

            all_tags = list(dict.fromkeys(existing_tags + new_tags))[:5]
            tags_applied.update(all_tags)

            db.conn.execute(
                "UPDATE knowledge_items SET tags = ? WHERE id = ?",
                (json.dumps(all_tags, ensure_ascii=False), kid),
            )
            db.conn.commit()
            tagged_count += 1
            if idx % 10 == 0 or idx == len(rows):
                print(f"  进度: {idx}/{len(rows)} (成功 {tagged_count}, 跳过 {skipped_count})")
        except Exception as e:
            err_msg = f"{row['id']} | {(row['title'] or '?')[:30]}: {e}"
            errors.append(err_msg)
            skipped_count += 1

    # 统计最终覆盖率
    final_tagged = db.conn.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE deleted_at IS NULL AND tags IS NOT NULL AND tags != '' AND tags != '[]'"
    ).fetchone()[0]
    final_coverage = (final_tagged / total_rows * 100) if total_rows else 0.0

    print("=" * 60)
    print(f"打标完成: 成功 {tagged_count} 条, 跳过 {skipped_count} 条")
    print(f"应用标签 {len(tags_applied)} 个: {sorted(tags_applied)[:20]}{'...' if len(tags_applied) > 20 else ''}")
    print(f"标签覆盖率: {coverage:.1f}% → {final_coverage:.1f}%")
    if errors:
        print(f"错误列表 (前 5 条):")
        for e in errors[:5]:
            print(f"  - {e}")
    print("=" * 60)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
