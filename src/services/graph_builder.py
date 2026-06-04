"""知识图谱构建服务 — 通过 LLM 分析知识关系并生成图谱结构"""

import json
import uuid
import logging
from datetime import datetime

from src.services.db import Database
from src.services.llm import LLMService
from src.utils.llm_text import strip_think

logger = logging.getLogger(__name__)

# 定义关系类型
RELATION_TYPES = {
    "related": "主题相关，可以互相参考",
    "contains": "包含关系，源知识包含目标知识的内容",
    "references": "引用关系，源知识引用了目标知识",
    "prerequisite": "前置知识，理解源知识需要目标知识作为基础",
    "contradicts": "信息矛盾，两条知识存在冲突",
    "part_of": "部分与整体，源知识是目标知识的一部分",
}

RELATION_DISCOVERY_PROMPT = """你是一个知识关系分析器。请分析以下知识条目之间的语义关系，识别它们之间的逻辑关联。

## 知识条目列表
__KNOWLEDGE_ITEMS__

## 任务
1. 分析每对知识条目之间是否存在语义关系
2. 只输出确实存在关联的条目对，无关的不要列出
3. 每个关系必须附带简短的理由说明

## 支持的关系类型
- related: 主题相关，可以互相参考
- contains: 包含关系，A 包含 B 的内容
- references: 引用关系，A 引用了 B
- prerequisite: 前置知识，理解 A 需要 B 作为基础
- contradicts: 信息矛盾，A 与 B 存在冲突
- part_of: 部分与整体，A 是 B 的一部分

## 输出格式（严格 JSON）
```json
{"relations": [{"source_id": "知识条目的ID", "target_id": "知识条目的ID", "relation_type": "related", "description": "一句话说明关联原因"}]}
```
注意：只输出 JSON，不要包含其他解释文字。"""


class GraphBuilder:
    MAX_ITEMS_PER_ANALYSIS = 30
    CONTENT_TRUNCATE = 500

    def __init__(self, progress_callback=None):
        self._llm = LLMService()
        self._progress = progress_callback  # signature: (message: str) -> None

    def _emit_progress(self, message: str, current: int | None = None, total: int | None = None) -> None:
        if not self._progress:
            return
        try:
            if current is not None and total is not None:
                self._progress(message, current, total)
            else:
                self._progress(message)
        except TypeError:
            self._progress(message)

    def build_from_knowledge(self, graph_id: str, knowledge_ids: list[str]) -> list[dict]:
        """分析指定知识条目之间的关系，并将结果写入数据库。返回生成的关系列表。"""
        if len(knowledge_ids) < 2:
            return []

        # 确保所有节点已添加到图谱
        Database.insert_graph_nodes(graph_id, knowledge_ids)

        # 如果知识条目过多，分批处理
        if len(knowledge_ids) <= self.MAX_ITEMS_PER_ANALYSIS:
            return self._analyze_batch(graph_id, knowledge_ids)

        all_relations = []
        batch_size = self.MAX_ITEMS_PER_ANALYSIS

        if len(knowledge_ids) > batch_size:
            Database.delete_graph_relations(graph_id)

        for i in range(0, len(knowledge_ids), batch_size):
            batch = knowledge_ids[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(knowledge_ids) + batch_size - 1) // batch_size
            if total_batches > 1:
                self._emit_progress(f"分析批次 {batch_num}/{total_batches}...", batch_num, total_batches)
            relations = self._analyze_batch(graph_id, batch, skip_delete=True)
            all_relations.extend(relations)

        if all_relations:
            Database.insert_graph_relations(graph_id, all_relations)
        return all_relations

    def _analyze_batch(self, graph_id: str, knowledge_ids: list[str]) -> list[dict]:
        items = Database.get_knowledge_batch(knowledge_ids)
        if len(items) < 2:
            return []

        lines = []
        for kid in knowledge_ids:
            item = items.get(kid)
            if not item:
                continue
            content = (item.get("content") or "")[:self.CONTENT_TRUNCATE]
            lines.append(f"- [{kid}] 标题：{item.get('title', '')}\n  内容摘要：{content[:200]}")

        prompt = RELATION_DISCOVERY_PROMPT.replace("__KNOWLEDGE_ITEMS__", "\n\n".join(lines))

        try:
            response = self._llm.chat([{"role": "user", "content": prompt}], silent=True)
            result = self._parse_json_response(response, "relations")
        except Exception as e:
            logger.warning("Graph relation analysis failed for graph %s: %s", graph_id, e)
            return []

        if not result or not isinstance(result, list):
            return []

        valid_relations = []
        for rel in result:
            source_id = rel.get("source_id", "")
            target_id = rel.get("target_id", "")
            relation_type = rel.get("relation_type", "related")
            if source_id not in items or target_id not in items:
                continue
            if source_id == target_id:
                continue
            if relation_type not in RELATION_TYPES:
                relation_type = "related"
            valid_relations.append({
                "source_knowledge_id": source_id,
                "target_knowledge_id": target_id,
                "relation_type": relation_type,
                "description": rel.get("description", ""),
                "weight": 1.0,
            })

        if valid_relations:
            # 先删除该图谱的旧关系，再写入新的
            Database.delete_graph_relations(graph_id)
            Database.insert_graph_relations(graph_id, valid_relations)
        return valid_relations

    def auto_generate_by_categories(self) -> list[str]:
        """按现有分类自动生成图谱。返回新生成/更新的图谱 ID 列表。"""
        categories = Database.get_all_categories()
        created_ids = []
        # 先过滤出有效分类
        valid_cats = []
        for cat in categories:
            items = Database.get_knowledge_by_category(cat["id"])
            if len(items) >= 2:
                valid_cats.append((cat, [it["id"] for it in items]))

        total = len(valid_cats)
        if total > 0:
            self._emit_progress(f"共 {total} 个分类待分析...", 0, total)

        for idx, (cat, knowledge_ids) in enumerate(valid_cats):
            cat_id = cat["id"]
            cat_name = cat["name"]
            self._emit_progress(f"正在分析 [{idx + 1}/{total}]: {cat_name} ({len(knowledge_ids)}条)", idx + 1, total)

            # 查找是否已有同名的 auto 类型图谱
            existing = None
            graphs = Database.list_graphs(source_type="auto")
            for g in graphs:
                if g["name"] == cat_name:
                    existing = g
                    break

            if existing:
                graph_id = existing["id"]
                # 增量更新：只删除已不在该分类的知识节点，保留坐标
                old_nodes = Database.get_graph_nodes(graph_id)
                old_kids = {n["knowledge_id"] for n in old_nodes}
                new_kids = set(knowledge_ids)
                to_remove = list(old_kids - new_kids)
                if to_remove:
                    Database.delete_graph_nodes(graph_id, to_remove)
                # 删除旧关系，重新分析
                Database.delete_graph_relations(graph_id)
            else:
                graph_id = Database.insert_graph(
                    name=cat_name,
                    description=cat.get("description", ""),
                    source_type="auto",
                )

            # 插入新节点（IGNORE 跳过已存在的，保留旧坐标）
            Database.insert_graph_nodes(graph_id, knowledge_ids)

            try:
                self.build_from_knowledge(graph_id, knowledge_ids)
            except Exception as e:
                logger.warning("Auto generate graph for category '%s' failed: %s", cat_name, e)

            # 图谱和节点已写入，无论关系分析是否成功都返回
            created_ids.append(graph_id)

        return created_ids

    def auto_generate_all(self) -> list[str]:
        """无分类时回退：取全部知识生成单图谱。"""
        self._emit_progress("未找到有效分类，正在分析全部知识...", 0, 1)
        all_items = Database.list_knowledge(limit=500)
        knowledge_ids = [it["id"] for it in all_items]
        if len(knowledge_ids) < 2:
            logger.warning("auto_generate_all: only %d knowledge items, need >= 2", len(knowledge_ids))
            return []

        # 查找已有的全量 auto 图谱
        existing = None
        for g in Database.list_graphs(source_type="auto"):
            if g["name"] == "全部知识":
                existing = g
                break

        if existing:
            graph_id = existing["id"]
            Database.delete_graph_relations(graph_id)
            old_nodes = Database.get_graph_nodes(graph_id)
            if old_nodes:
                Database.delete_graph_nodes(graph_id, [n["knowledge_id"] for n in old_nodes])
        else:
            graph_id = Database.insert_graph(name="全部知识", description="自动分析全部知识的关联关系", source_type="auto")

        Database.insert_graph_nodes(graph_id, knowledge_ids)

        # 关系分析失败不应影响图谱显示，图谱+节点已写入
        try:
            self.build_from_knowledge(graph_id, knowledge_ids)
        except Exception as e:
            logger.warning("auto_generate_all: relation analysis failed: %s", e)

        return [graph_id]

    def _parse_json_response(self, response: str, key: str | None = None) -> dict | list | None:
        """从 LLM 响应中提取 JSON"""
        text = strip_think(response).strip()
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            text = text[start:end].strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if key and isinstance(data, dict):
            return data.get(key)
        return data
