"""Wiki parent-child 检索 —— wiki 候选带回其引用的 source 页摘要 (第二阶段 W2)。

与 block 检索的 ``parent_child_retrieval`` 对称:wiki 命中 entities/concepts/
comparisons/syntheses 页时,按 frontmatter 的 ``knowledge_id`` 回查对应 source
页摘要(A2 方案,不动第一阶段编译器),写入候选 ``parent_content`` 字段,复用
``GenerateStage`` 既有渲染路径。

字段名 ``parent_content`` 与 block 侧 ``parent_child_retrieval.py:210`` 一致,
``GenerateStage._build_context_from_filtered`` 已消费此字段。
"""
from __future__ import annotations

import logging
from typing import Any

from src.services.wiki_source_ids import resolve_source_ids

logger = logging.getLogger(__name__)

# page_type 白名单:这些页是 source 的"消费者",需回查 source 摘要。
# sources 页自身即 source,跳过。
_PARENT_PAGE_TYPES = {"entities", "concepts", "comparisons", "syntheses"}


class WikiParentRetriever:
    """为 wiki 检索候选附加 source 页 parent 上下文。

    Args:
        db: Database 实例(可选,None 时走 ``Database`` 单例兜底)。
        config: 配置对象或 dict(可选,None 时走全局 ``Config``)。
    """

    def __init__(self, db=None, config=None):
        self._db = db
        self._config = config

    # ---- 范式照抄 parent_child_retrieval.ParentChildRetriever ----
    def _get_db(self):
        if self._db is not None:
            return self._db
        from src.services.db import Database
        return Database

    def _get_config(self, key: str, default=None):
        if self._config is not None:
            if isinstance(self._config, dict):
                obj: Any = self._config
                for p in key.split("."):
                    if isinstance(obj, dict):
                        obj = obj.get(p)
                    else:
                        return default
                return obj if obj is not None else default
            return self._config.get(key, default)
        try:
            from src.utils.config import Config
            return Config.get(key, default)
        except Exception:
            return default

    @staticmethod
    def _extract_knowledge_ids(meta: dict) -> list[str]:
        """按 page_type 从候选 metadata 取溯源 knowledge_id 列表(A2 方案)。

        - syntheses/comparisons:优先 frontmatter ``source_ids`` 列表,退化到单个 knowledge_id
        - entities/concepts:第一阶段 updater 只写单个 ``knowledge_id``
        - sources/其它:空列表(跳过)
        """
        meta = meta or {}
        page_type = meta.get("page_type", "")
        if page_type not in _PARENT_PAGE_TYPES:
            return []
        return resolve_source_ids(meta)

    def _fetch_summaries(self, db, kids: list[str], max_length: int) -> dict[str, str]:
        """批量回查 source 条目,提炼首段摘要(复用 WikiSourceCompiler._build_summary)。"""
        if not kids:
            return {}
        from src.services.wiki_source_compiler import WikiSourceCompiler

        batch: dict[str, dict] = {}
        try:
            if hasattr(db, "get_knowledge_batch"):
                batch = db.get_knowledge_batch(kids) or {}
            else:
                batch = {}
        except Exception as e:
            logger.warning("wiki parent get_knowledge_batch failed: %s", e)
            batch = {}
        # 退化:无 batch 接口或部分缺失时逐个查
        if len(batch) < len(kids) and hasattr(db, "get_knowledge"):
            for kid in kids:
                if kid in batch:
                    continue
                try:
                    item = db.get_knowledge(kid)
                    if item:
                        batch[kid] = item
                except Exception:
                    continue

        summaries: dict[str, str] = {}
        for kid, item in batch.items():
            content = (item or {}).get("content", "") or ""
            if not content:
                continue
            try:
                summary = WikiSourceCompiler._build_summary(content)
            except Exception:
                summary = content[:max_length]
            if summary:
                summaries[kid] = summary[:max_length]
        return summaries

    def enrich(
        self,
        candidates: list[dict],
        max_length: int | None = None,
    ) -> list[dict]:
        """为 wiki 候选附加 source 页 parent_content。

        非 wiki 候选(id 不以 ``wiki:`` 开头)与 sources 页原样返回,不动。
        """
        if not candidates:
            return candidates
        if max_length is None:
            max_length = int(self._get_config(
                "rag.wiki_parent_child.max_parent_chars", 2000))

        # 收集 kid -> [候选] 映射(仅 wiki 候选)
        kid_to_cands: dict[str, list[dict]] = {}
        for cand in candidates:
            if not str(cand.get("id", "")).startswith("wiki:"):
                continue
            kids = self._extract_knowledge_ids(cand.get("metadata") or {})
            for kid in kids:
                kid_to_cands.setdefault(kid, []).append(cand)

        if not kid_to_cands:
            return candidates

        db = self._get_db()
        summaries = self._fetch_summaries(db, list(kid_to_cands.keys()), max_length)

        for kid, cand_list in kid_to_cands.items():
            summary = summaries.get(kid)
            if not summary:
                continue
            for cand in cand_list:
                existing = cand.get("parent_content", "")
                cand["parent_content"] = (existing + "\n" + summary) if existing else summary
        # 最终截断(多 source 拼接后可能超长)
        for cand in candidates:
            pc = cand.get("parent_content")
            if pc and len(pc) > max_length:
                cand["parent_content"] = pc[:max_length]
        return candidates


def enrich_wiki_parent_context(
    candidates: list[dict],
    db=None,
    config=None,
) -> list[dict]:
    """便捷函数:为 wiki 候选附加 source 页 parent 上下文。"""
    return WikiParentRetriever(db=db, config=config).enrich(candidates)
