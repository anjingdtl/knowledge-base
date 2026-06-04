"""Block 上下文扩展服务 — RAG 检索时补齐 Small-to-Big 上下文。"""
from src.services.db import Database
from src.utils.config import Config


class BlockContextService:
    """Build RAG context around a hit block without mutating stored block text."""

    def __init__(self, db=None, config=None):
        self._db = db or Database
        self._config = config or Config

    def build_context(
        self,
        block_id: str,
        max_depth: int | None = None,
        sibling_window: int | None = None,
        max_links: int | None = None,
    ) -> str:
        if not block_id:
            return ""
        if max_depth is None:
            max_depth = self._config.get("rag.context_trace_depth", 3)
        if sibling_window is None:
            sibling_window = self._config.get("rag.context_sibling_window", 1)
        if max_links is None:
            max_links = self._config.get("rag.link_expansion.max_links", 3)

        block = self._db.get_block(block_id)
        if not block:
            return ""

        parts: list[str] = []
        ancestors = self._db.get_block_ancestors(block_id, max_depth)
        ancestor_text = [a.get("content", "").strip() for a in reversed(ancestors) if a.get("content", "").strip()]
        if ancestor_text:
            parts.append("父链: " + " > ".join(ancestor_text))

        current = block.get("content", "").strip()
        if current:
            parts.append("当前: " + current)

        siblings = self._get_siblings(block, sibling_window)
        if siblings:
            parts.append("相邻: " + " | ".join(siblings))

        linked = self._get_linked_summaries(block_id, max_links)
        if linked:
            parts.append("关联知识: " + " | ".join(linked))

        return "\n".join(parts)

    def enrich_result(self, result: dict, max_depth: int | None = None) -> dict:
        block_id = (
            result.get("id")
            or result.get("block_id")
            or (result.get("metadata") or {}).get("block_id")
        )
        if not block_id:
            return result
        result["block_context"] = self.build_context(block_id, max_depth=max_depth)
        return result

    def _get_siblings(self, block: dict, sibling_window: int) -> list[str]:
        if sibling_window <= 0:
            return []
        parent_id = block.get("parent_id")
        page_id = block.get("page_id")
        order_idx = int(block.get("order_idx") or 0)
        if parent_id:
            where = "parent_id = ?"
            params: list = [parent_id]
        else:
            where = "parent_id IS NULL AND page_id = ?"
            params = [page_id]
        rows = self._db.get_conn().execute(
            f"""SELECT id, content FROM blocks
                WHERE {where}
                  AND order_idx BETWEEN ? AND ?
                  AND id != ?
                ORDER BY order_idx ASC""",
            params + [order_idx - sibling_window, order_idx + sibling_window, block["id"]],
        ).fetchall()
        return [row["content"].strip() for row in rows if row["content"] and row["content"].strip()]

    def _get_linked_summaries(self, block_id: str, max_links: int) -> list[str]:
        if max_links <= 0:
            return []
        rows = self._db.get_conn().execute(
            """SELECT target_type, target_id FROM entity_refs
               WHERE source_type = 'block' AND source_id = ? AND ref_type IN ('link', 'embed', 'mention')
               ORDER BY created_at DESC LIMIT ?""",
            (block_id, max_links),
        ).fetchall()
        summaries = []
        for row in rows:
            if row["target_type"] == "knowledge":
                item = self._db.get_knowledge(row["target_id"])
                if item:
                    title = item.get("title", "")
                    content = (item.get("content", "") or "").replace("\n", " ").strip()
                    summaries.append(f"{title}: {content[:160]}" if content else title)
            elif row["target_type"] == "block":
                target = self._db.get_block(row["target_id"])
                if target:
                    summaries.append((target.get("content", "") or "").replace("\n", " ")[:160])
        return [s for s in summaries if s]


def get_block_context(block_id: str, max_depth: int | None = None) -> str:
    return BlockContextService().build_context(block_id, max_depth=max_depth)


def enrich_result_with_context(result: dict, max_depth: int | None = None) -> dict:
    return BlockContextService().enrich_result(result, max_depth=max_depth)
