"""Parent-Child Retrieval — 小块检索 + 自动返回父块上下文

核心思路:
1. Embedding 用小块（block 级）— 保持检索精度
2. 检索后自动返回父块内容（chapter/section 级）— 提供更完整的上下文
3. Source citation 仍定位到最小 block — 精确溯源
4. 按文档类型差异化 block 策略 — 不同格式最优拆分

使用方式:
    from src.services.parent_child_retrieval import ParentChildRetriever
    retriever = ParentChildRetriever(db=db)
    enriched = retriever.enrich(results, file_type="pdf")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# 按文档类型差异化的 block 策略
BLOCK_STRATEGIES = {
    "pdf": {
        "parent": "page",        # PDF 以 page 为父块
        "child": "paragraph",    # 以 paragraph 为子块
        "parent_block_types": {"page", "section", "heading"},
        "max_parent_chars": 4000,
    },
    "docx": {
        "parent": "section",     # Word 以 section 为父块
        "child": "paragraph",    # 以 paragraph 为子块
        "parent_block_types": {"section", "heading", "h1", "h2", "h3"},
        "max_parent_chars": 4000,
    },
    "xlsx": {
        "parent": "sheet",       # Excel 以 sheet 为父块
        "child": "row_range",    # 以 row_range 为子块
        "parent_block_types": {"sheet", "table"},
        "max_parent_chars": 6000,
    },
    "pptx": {
        "parent": "slide",       # PPT 以 slide 为父块
        "child": "text_block",   # 以 text_block 为子块
        "parent_block_types": {"slide", "section"},
        "max_parent_chars": 3000,
    },
    "md": {
        "parent": "heading",     # Markdown 以 heading 为父块
        "child": "paragraph",    # 以 paragraph 为子块
        "parent_block_types": {"heading", "h1", "h2", "h3", "h4"},
        "max_parent_chars": 4000,
    },
    "txt": {
        "parent": "document",    # 纯文本以整个文档为父块
        "child": "paragraph",
        "parent_block_types": {"document"},
        "max_parent_chars": 4000,
    },
}

# 默认策略（未知文件类型）
DEFAULT_STRATEGY = {
    "parent": "section",
    "child": "paragraph",
    "parent_block_types": {"section", "heading", "page"},
    "max_parent_chars": 4000,
}


@dataclass
class ParentBlock:
    """父块信息"""
    block_id: str
    content: str
    block_type: str
    page_id: str
    title: str = ""
    children_count: int = 0


class ParentChildRetriever:
    """Parent-Child 检索增强

    对检索到的小块结果（child），自动向上查找对应的父块（parent），
    将父块内容附加到结果中供 LLM 生成使用，同时保持 citation 定位到子块。

    Args:
        db: Database 实例
        config: 配置（可选，读取 rag.parent_child 配置节）
    """

    def __init__(self, db=None, config=None):
        self._db = db
        self._config = config

    def _get_db(self):
        if self._db is not None:
            return self._db
        from src.services.db import Database
        return Database

    def _get_config(self, key: str, default=None):
        """读取配置项，支持 Config 对象或 dict。"""
        if self._config is not None:
            if isinstance(self._config, dict):
                parts = key.split(".")
                obj = self._config
                for p in parts:
                    if isinstance(obj, dict):
                        obj = obj.get(p)
                    else:
                        return default
                return obj if obj is not None else default
            return self._config.get(key, default)
        # 尝试从全局 Config 读取
        try:
            from src.utils.config import Config
            return Config.get(key, default)
        except Exception:
            return default

    def _get_strategy(self, file_type: str) -> dict:
        """获取文件类型对应的 block 策略"""
        # 查找精确匹配
        ft = file_type.lower().strip(".")
        if ft in BLOCK_STRATEGIES:
            return BLOCK_STRATEGIES[ft]
        # 尝试不带前缀
        for key in BLOCK_STRATEGIES:
            if ft.endswith(key):
                return BLOCK_STRATEGIES[key]
        return DEFAULT_STRATEGY

    def enrich(
        self,
        results: list[dict],
        file_type: str = "",
        max_parent_chars: int | None = None,
    ) -> list[dict]:
        """为检索结果附加父块上下文

        Args:
            results: 检索结果列表，每条含 id/text/metadata
            file_type: 文件类型（pdf/docx/xlsx/pptx/md/txt）
            max_parent_chars: 父块最大字符数（None 则用策略默认值）

        Returns:
            附加了 parent_content 字段的结果列表
        """
        if not results:
            return results

        strategy = self._get_strategy(file_type)
        if max_parent_chars is None:
            # 优先从 config 读取 rag.parent_child.max_parent_chars
            config_val = self._get_config("rag.parent_child.max_parent_chars")
            if config_val is not None:
                max_parent_chars = int(config_val)
            else:
                max_parent_chars = strategy["max_parent_chars"]

        db = self._get_db()

        # 批量收集所有 block_id 和对应的 page_id
        block_ids = []
        page_ids = set()
        for r in results:
            bid = (
                r.get("id")
                or r.get("block_id")
                or (r.get("metadata") or {}).get("block_id")
            )
            if bid:
                block_ids.append(bid)
            pid = (r.get("metadata") or {}).get("page_id", "")
            if pid:
                page_ids.add(pid)

        if not block_ids:
            return results

        # 批量查询 blocks 信息
        block_info = self._batch_get_blocks(db, block_ids)

        # 批量查询父块 — 按策略中的 parent_block_types 过滤
        parent_types = strategy["parent_block_types"]
        parent_blocks = self._batch_find_parents(db, block_ids, block_info, parent_types)

        # 对每个 page_id，查询该 page 下所有子 block 以构建完整的父块内容
        page_parent_content = {}
        for page_id in page_ids:
            parent = self._find_page_parent(db, page_id, parent_types, max_parent_chars)
            if parent:
                page_parent_content[page_id] = parent

        # 附加父块内容到结果
        for r in results:
            bid = (
                r.get("id")
                or r.get("block_id")
                or (r.get("metadata") or {}).get("block_id")
            )
            metadata = r.get("metadata") or {}

            # 方式 1：从批量查询的直接父块中获取
            parent = parent_blocks.get(bid) if bid else None
            if parent and parent.content:
                r["parent_content"] = parent.content[:max_parent_chars]
                r["parent_block_id"] = parent.block_id
                continue

            # 方式 2：从 page 级别的父块内容获取
            page_id = metadata.get("page_id", "")
            if page_id in page_parent_content:
                r["parent_content"] = page_parent_content[page_id][:max_parent_chars]
                continue

            # 方式 3：用现有 block_context（已在 HybridSearcher 中填充）
            # 如果已有 block_context 则保持不变

        return results

    def _batch_get_blocks(self, db, block_ids: list[str]) -> dict[str, dict]:
        """批量查询 block 信息"""
        if not block_ids:
            return {}
        try:
            conn = db.get_conn()
            placeholders = ",".join("?" for _ in block_ids)
            rows = conn.execute(
                f"SELECT id, parent_id, page_id, content, block_type, order_idx "
                f"FROM blocks WHERE id IN ({placeholders})",
                block_ids,
            ).fetchall()
            return {r["id"]: dict(r) for r in rows}
        except Exception as e:
            logger.warning("Failed to batch get blocks: %s", e)
            return {}

    def _batch_find_parents(
        self,
        db,
        block_ids: list[str],
        block_info: dict[str, dict],
        parent_types: set[str],
    ) -> dict[str, ParentBlock]:
        """为每个 block 向上查找最近的指定类型的父块"""
        parents: dict[str, ParentBlock] = {}

        # 收集所有需要查询的 parent_id
        all_parent_ids = set()
        for bid in block_ids:
            info = block_info.get(bid)
            if info and info.get("parent_id"):
                all_parent_ids.add(info["parent_id"])

        # 批量查询所有可能父块的 block_type
        parent_info = {}
        if all_parent_ids:
            try:
                conn = db.get_conn()
                placeholders = ",".join("?" for _ in all_parent_ids)
                rows = conn.execute(
                    f"SELECT id, parent_id, page_id, content, block_type FROM blocks WHERE id IN ({placeholders})",
                    list(all_parent_ids),
                ).fetchall()
                parent_info = {r["id"]: dict(r) for r in rows}
            except Exception as e:
                logger.warning("Failed to batch get parent blocks: %s", e)

        # 为每个 block 查找最近的策略类型父块（最多向上查 5 层）
        for bid in block_ids:
            info = block_info.get(bid)
            if not info:
                continue
            parent_id = info.get("parent_id")
            depth = 0
            while parent_id and depth < 5:
                pinfo = parent_info.get(parent_id)
                if not pinfo:
                    break
                if pinfo.get("block_type", "") in parent_types:
                    parents[bid] = ParentBlock(
                        block_id=pinfo["id"],
                        content=pinfo.get("content", ""),
                        block_type=pinfo.get("block_type", ""),
                        page_id=pinfo.get("page_id", ""),
                    )
                    break
                parent_id = pinfo.get("parent_id")
                depth += 1

        return parents

    def _find_page_parent(
        self, db, page_id: str, parent_types: set[str], max_chars: int,
    ) -> str | None:
        """查找一个 page 下最高层级的父块内容"""
        if not page_id:
            return None
        try:
            conn = db.get_conn()
            # 找到该 page 下指定类型的所有父块
            placeholders = ",".join("?" for _ in parent_types)
            rows = conn.execute(
                f"""SELECT id, content, block_type FROM blocks
                    WHERE page_id = ? AND block_type IN ({placeholders})
                    ORDER BY order_idx ASC LIMIT 5""",
                [page_id] + list(parent_types),
            ).fetchall()

            if not rows:
                return None

            # 拼接所有父块内容
            parts = []
            total = 0
            for r in rows:
                content = (r.get("content") or "").strip()
                if content:
                    parts.append(content)
                    total += len(content)
                    if total >= max_chars:
                        break

            return "\n\n".join(parts)[:max_chars] if parts else None
        except Exception as e:
            logger.warning("Failed to find page parent for %s: %s", page_id, e)
            return None


def enrich_with_parent_context(
    results: list[dict],
    db=None,
    file_type: str = "",
    config=None,
) -> list[dict]:
    """便捷函数：为检索结果附加父块上下文"""
    retriever = ParentChildRetriever(db=db, config=config)
    return retriever.enrich(results, file_type=file_type)
