"""Agent Memory 服务 — 为 AI Agent 提供持久化记忆能力

支持四类记忆：fact（事实）、decision（决策）、context（上下文）、task（任务）
通过 FTS5 实现高效搜索，通过 LLM 实现智能提取
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import cast

logger = logging.getLogger(__name__)


_VALID_CATEGORIES = {"fact", "decision", "context", "task"}


class AgentMemoryService:
    """Agent Memory 业务逻辑层"""

    def __init__(self, repo=None, db=None, llm=None):
        self._repo = repo
        self._db = db
        self._llm = llm

    def _get_repo(self):
        """延迟获取 repo（避免循环导入）"""
        if self._repo is None:
            from src.repositories.agent_memory_repo import AgentMemoryRepository
            self._repo = AgentMemoryRepository(db=self._db)
        return self._repo

    def remember_fact(self, key: str, value: str, category: str = "fact") -> dict:
        """记住一个事实/决策/上下文/任务

        Args:
            key: 记忆键名（唯一标识，相同 key 会覆盖）
            value: 记忆内容
            category: 分类 (fact | decision | context | task)

        Returns:
            {"id": ..., "key": ..., "category": ..., "created": bool}
        """
        if category not in _VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{category}', must be one of {sorted(_VALID_CATEGORIES)}")
        repo = self._get_repo()
        existing = repo.get_by_key(key)
        if existing:
            repo.upsert(key, value, category)
            return {
                "id": existing["id"],
                "key": key,
                "category": category,
                "created": False,
                "message": f"已更新已有记忆: {key}",
            }
        mem_id = repo.store(key, value, category)
        return {
            "id": mem_id,
            "key": key,
            "category": category,
            "created": True,
            "message": f"已记住: {key}",
        }

    def recall_facts(self, query: str, category: str | None = None,
                     limit: int = 5) -> list[dict]:
        """搜索已记住的事实/决策

        Args:
            query: 搜索关键词
            category: 可选分类过滤
            limit: 返回数量上限

        Returns:
            匹配的记忆列表
        """
        repo = self._get_repo()
        try:
            results = repo.search_fts(query, category=category, limit=limit)
        except Exception:
            # FTS 不可用时降级到 LIKE
            results = repo.search_like(query, category=category, limit=limit)
        if not results:
            results = repo.search_like(query, category=category, limit=limit)
        # 清理 metadata 字段
        for r in results:
            if isinstance(r.get("metadata"), str):
                try:
                    r["metadata"] = json.loads(r["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return cast(list[dict], list(results))

    def update_project_context(self, summary: str) -> dict:
        """更新项目整体上下文描述

        使用特殊的 __project_context key 存储，category=context。
        """
        repo = self._get_repo()
        repo.upsert("__project_context", summary, category="context")
        return {
            "key": "__project_context",
            "message": "项目上下文已更新",
        }

    def get_project_context(self) -> str | None:
        """获取当前项目上下文"""
        repo = self._get_repo()
        entry = repo.get_by_key("__project_context")
        return entry["value"] if entry else None

    def search_decisions(self, query: str, limit: int = 5) -> list[dict]:
        """搜索架构/技术决策记录

        等价于 recall_facts(query, category="decision", limit)
        """
        return self.recall_facts(query, category="decision", limit=limit)

    def summarize_recent_changes(self, since_hours: int = 24) -> dict:
        """总结近期知识库变更

        统计 operation_logs 和 agent_memory 的近期活动。
        如果有 LLM 可用，生成自然语言摘要。
        """
        repo = self._get_repo()
        mem_stats = repo.recent_changes(since_hours)

        # 从 operation_logs 获取近期操作统计
        ops_stats = {}
        if self._db:
            try:
                cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()
                row = self._db.get_conn().execute(
                    """SELECT
                        operation,
                        COUNT(*) as cnt
                       FROM operation_logs
                       WHERE created_at >= ?
                       GROUP BY operation
                       ORDER BY cnt DESC""",
                    (cutoff,),
                ).fetchall()
                ops_stats = {r["operation"]: r["cnt"] for r in row}
            except Exception as e:
                logger.warning("Failed to query operation_logs: %s", e)

        result = {
            "since_hours": since_hours,
            "memory_changes": mem_stats,
            "operation_changes": ops_stats,
            "total_operations": sum(ops_stats.values()),
        }

        # 如果有 LLM，生成自然语言摘要
        if self._llm and (mem_stats.get("total", 0) > 0 or ops_stats):
            try:
                summary = self._generate_change_summary(result)
                result["summary"] = summary
            except Exception as e:
                logger.warning("LLM summary generation failed: %s", e)
                result["summary"] = self._build_basic_summary(result)
        else:
            result["summary"] = self._build_basic_summary(result)

        return result

    def extract_tasks_from_doc(self, content: str) -> dict:
        """从文档内容中提取待办任务

        使用 LLM 分析文档内容，提取任务列表。
        如果没有 LLM，使用启发式规则。
        """
        if self._llm:
            return self._extract_tasks_llm(content)
        return self._extract_tasks_heuristic(content)

    def _extract_tasks_llm(self, content: str) -> dict:
        """LLM 驱动的任务提取

        注意: 用户文档内容直接拼入 prompt。在 MCP 信任模型下（调用方是受信任的
        AI Agent，文档也是用户自己的），这可以接受。格式错乱的响应会被 try/except
        捕获并退化为启发式方法。
        """
        prompt = f"""分析以下文档内容，提取其中的待办任务/行动项/TODO。

对每个任务提取：
- task: 任务描述（简洁明确）
- priority: 优先级 (high/medium/low)
- category: 分类 (bug/feature/docs/refactor/test/other)

返回 JSON 数组格式。如果没有发现任务，返回空数组。

文档内容：
{content[:3000]}"""
        try:
            resp = self._llm.chat([{"role": "user", "content": prompt}])
            text = resp.get("content", resp) if isinstance(resp, dict) else str(resp)
            # 尝试提取 JSON
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            tasks = json.loads(text)
            if not isinstance(tasks, list):
                tasks = []
            # 存储到 agent_memory
            repo = self._get_repo()
            stored = []
            for t in tasks[:20]:  # 最多存 20 条
                if isinstance(t, dict) and t.get("task"):
                    task_id = repo.store(
                        key=f"task:{t['task'][:50]}",
                        value=t["task"],
                        category="task",
                        metadata={"priority": t.get("priority", "medium"),
                                  "task_category": t.get("category", "other")},
                    )
                    stored.append({"id": task_id, **t})
            return {
                "tasks": stored,
                "total_found": len(tasks),
                "stored": len(stored),
                "method": "llm",
            }
        except Exception as e:
            logger.warning("LLM task extraction failed: %s", e)
            return self._extract_tasks_heuristic(content)

    def _extract_tasks_heuristic(self, content: str) -> dict:
        """启发式任务提取（不依赖 LLM）"""
        tasks = []
        # 匹配 TODO/FIXME/HACK/XXX/NOTE 标记
        patterns = [
            r'(?:TODO|FIXME|HACK|XXX|BUG|NOTE)\s*[:：]\s*(.+?)(?:\n|$)',
            r'-\s*\[[ x]\]\s*(.+?)(?:\n|$)',  # Markdown checkbox
            r'(?:待办|任务|需要|必须|记得|别忘了)\s*[:：]?\s*(.+?)(?:\n|$)',
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                task_text = m.group(1).strip()
                if task_text and len(task_text) > 2:
                    tasks.append({"task": task_text, "priority": "medium", "category": "other"})

        # 去重
        seen = set()
        unique = []
        for t in tasks:
            if t["task"] not in seen:
                seen.add(t["task"])
                unique.append(t)

        # 存储
        repo = self._get_repo()
        stored = []
        for t in unique[:20]:
            task_id = repo.store(
                key=f"task:{t['task'][:50]}",
                value=t["task"],
                category="task",
                metadata={"priority": t.get("priority", "medium"),
                          "task_category": t.get("category", "other")},
            )
            stored.append({"id": task_id, **t})

        return {
            "tasks": stored,
            "total_found": len(unique),
            "stored": len(stored),
            "method": "heuristic",
        }

    def _generate_change_summary(self, stats: dict) -> str:
        """用 LLM 生成变更摘要"""
        prompt = f"""基于以下知识库统计数据，生成一段简洁的近期变更摘要（100字以内）：

记忆变更: {stats['memory_changes']}
操作日志: {stats['operation_changes']}
时间范围: 最近 {stats['since_hours']} 小时
"""
        resp = self._llm.chat([{"role": "user", "content": prompt}])
        return resp.get("content", resp) if isinstance(resp, dict) else str(resp)

    def _build_basic_summary(self, stats: dict) -> str:
        """构建基础摘要（不需要 LLM）"""
        parts = [f"近 {stats['since_hours']}h:"]
        mc = stats.get("memory_changes", {})
        if mc.get("total", 0) > 0:
            parts.append(f"记忆 {mc['total']} 条变更"
                         f"(事实 {mc.get('facts',0)}/决策 {mc.get('decisions',0)}"
                         f"/上下文 {mc.get('contexts',0)}/任务 {mc.get('tasks',0)})")
        oc = stats.get("operation_changes", {})
        if oc:
            ops = ", ".join(f"{k} {v}次" for k, v in oc.items())
            parts.append(f"操作: {ops}")
        if len(parts) == 1:
            return f"近 {stats['since_hours']}h 无变更"
        return "；".join(parts)
