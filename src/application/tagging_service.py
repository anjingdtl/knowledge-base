"""TaggingService — batch auto-tag knowledge items via LLM.

Used by MCP ``auto_tag`` tool. Does not use Database._instance.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class TaggingService:
    """Auto-tag knowledge rows with LLM-generated labels."""

    def __init__(self, db: Any, llm: Any):
        self._db = db
        self._llm = llm

    def list_candidates(self, *, limit: int = 50, force: bool = False) -> list[Any]:
        limit = max(1, min(int(limit), 500))
        if force:
            query_sql = (
                "SELECT id, title, content, tags FROM knowledge_items "
                "WHERE deleted_at IS NULL LIMIT ?"
            )
        else:
            query_sql = (
                "SELECT id, title, content, tags FROM knowledge_items "
                "WHERE deleted_at IS NULL AND (tags IS NULL OR tags = '' OR tags = '[]') "
                "LIMIT ?"
            )
        with self._db.get_conn() as conn:
            return list(conn.execute(query_sql, (limit,)).fetchall())

    def apply_tags(self, knowledge_id: str, tags: list[str]) -> None:
        tags_json = json.dumps(tags, ensure_ascii=False)
        with self._db.get_conn() as conn:
            conn.execute(
                "UPDATE knowledge_items SET tags = ? WHERE id = ?",
                (tags_json, knowledge_id),
            )

    def generate_tags(
        self,
        *,
        title: str,
        content_preview: str,
        existing_tags: list[str] | None = None,
    ) -> list[str]:
        existing_tags = list(existing_tags or [])
        prompt = (
            "你是一个知识库标签专家。请根据以下文档的标题和内容摘要，"
            "生成 1-3 个标签（中英文皆可，优先中文）。\n"
            "标签应该：简洁（2-6个字）、准确反映主题、便于检索和分类。\n"
            "只输出 JSON 数组，例如：[\"Python\", \"FastAPI\", \"后端开发\"]\n\n"
            f"标题：{title}\n"
            f"内容摘要：{content_preview}\n\n"
            f"{'已有标签：' + ', '.join(existing_tags) if existing_tags else ''}"
        )
        tag_messages = [{"role": "user", "content": prompt}]
        llm = self._llm
        if hasattr(llm, "chat_with_usage"):
            response_text = llm.chat_with_usage(tag_messages, silent=True)[0]
        else:
            response_text = llm.chat(tag_messages, silent=True)
        response_text = (response_text or "").strip()
        if response_text.startswith("```"):
            response_text = (
                response_text.split("\n", 1)[-1]
                if "\n" in response_text
                else response_text
            )
            response_text = (
                response_text.rsplit("```", 1)[0]
                if "```" in response_text
                else response_text
            )
        new_tags = json.loads(response_text)
        if not isinstance(new_tags, list):
            raise ValueError(f"LLM 返回了非数组格式: {type(new_tags)}")
        all_tags = list(dict.fromkeys(existing_tags + new_tags))
        return all_tags[:5]

    def auto_tag(self, *, limit: int = 50, force: bool = False) -> dict[str, Any]:
        """Batch auto-tag. Returns tagged_count / skipped_count / errors / tags_applied."""
        rows = self.list_candidates(limit=limit, force=force)
        if not rows:
            return {
                "tagged_count": 0,
                "skipped_count": 0,
                "errors": [],
                "tags_applied": [],
                "message": "没有需要打标的条目（所有条目已有标签）",
            }

        tagged_count = 0
        skipped_count = 0
        errors: list[str] = []
        tags_applied_set: set[str] = set()

        for row in rows:
            try:
                kid = row["id"]
                title = row["title"] or ""
                content_preview = (row["content"] or "")[:500]
                existing_tags_str = row["tags"] if "tags" in row.keys() else ""
                existing_tags: list[str] = []
                if existing_tags_str and existing_tags_str != "[]":
                    try:
                        parsed = json.loads(existing_tags_str)
                        if isinstance(parsed, list):
                            existing_tags = parsed
                    except json.JSONDecodeError:
                        existing_tags = []

                all_tags = self.generate_tags(
                    title=title,
                    content_preview=content_preview,
                    existing_tags=existing_tags,
                )
                tags_applied_set.update(all_tags)
                self.apply_tags(kid, all_tags)
                tagged_count += 1
            except Exception as e:  # noqa: BLE001
                def _row_value(key: str, default: str = "?") -> str:
                    try:
                        if hasattr(row, "keys") and key in row.keys():
                            return str(row[key] or default)
                    except Exception:
                        pass
                    return default

                title = str(_row_value("title"))[:30]
                errors.append(f"{_row_value('id')} | {title}: {e}")
                skipped_count += 1

        return {
            "tagged_count": tagged_count,
            "skipped_count": skipped_count,
            "errors": errors,
            "tags_applied": sorted(tags_applied_set),
        }
