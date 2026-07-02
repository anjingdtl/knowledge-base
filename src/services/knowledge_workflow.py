"""KnowledgeWorkflowService — wiki-first 文件系统层编排器。

mode=wiki_first 时,ingest 后编排 source/entity/index/log 四个编译器。
失败隔离(每步 try/except),整体不抛。时间戳由调用方传入(可复现)。

注意:与现有 ``WikiCompiler``(SQLite ``wiki_pages``)是两套并行产物 ——
本服务只管"文件系统 wiki 层"(``wiki/*.md``)。
"""
from __future__ import annotations

import logging

from src.services.db import Database
from src.services.wiki_entity_updater import WikiEntityUpdater
from src.services.wiki_index_compiler import WikiIndexCompiler
from src.services.wiki_log_compiler import WikiLogCompiler
from src.services.wiki_source_compiler import WikiSourceCompiler
from src.utils.config import Config

logger = logging.getLogger(__name__)


class KnowledgeWorkflowService:
    def __init__(
        self,
        source_compiler: WikiSourceCompiler | None = None,
        entity_updater: WikiEntityUpdater | None = None,
        index_compiler: WikiIndexCompiler | None = None,
        log_compiler: WikiLogCompiler | None = None,
    ):
        self._source = source_compiler or WikiSourceCompiler()
        self._entity = entity_updater or WikiEntityUpdater()
        self._index = index_compiler or WikiIndexCompiler()
        self._log = log_compiler or WikiLogCompiler()

    def compile(self, knowledge_id: str, ingested_at: str | None = None) -> dict:
        """编排 wiki-first 编译。失败隔离,不抛。"""
        mode = Config.get("knowledge_workflow.mode", "legacy")
        if mode != "wiki_first":
            return {"mode": mode, "skipped": True}

        item = Database.get_knowledge(knowledge_id)
        if not item:
            return {"mode": mode, "skipped": True, "reason": "not_found"}
        ts = ingested_at or item.get("created_at") or ""

        result: dict = {"mode": mode, "errors": []}

        try:
            src = self._source.compile(knowledge_id, ts)
            result["source"] = src
        except Exception as e:
            logger.warning("source compile failed (%s): %s", knowledge_id, e)
            result["errors"].append({"stage": "source", "error": str(e)})
            src = {}

        try:
            ent = self._entity.update(knowledge_id, self._as_entity_input(src, item), ts)
            result["entity"] = ent
        except Exception as e:
            logger.warning("entity update failed (%s): %s", knowledge_id, e)
            result["errors"].append({"stage": "entity", "error": str(e)})

        try:
            result["index"] = self._index.refresh()
        except Exception as e:
            logger.warning("index refresh failed (%s): %s", knowledge_id, e)
            result["errors"].append({"stage": "index", "error": str(e)})

        try:
            log_ev = {
                "type": "ingest",
                "target": item.get("title", knowledge_id),
                "timestamp": ts,
                "detail": f"compiled {knowledge_id}",
            }
            result["log"] = self._log.append(log_ev)
        except Exception as e:
            logger.warning("log append failed (%s): %s", knowledge_id, e)
            result["errors"].append({"stage": "log", "error": str(e)})

        return result

    @staticmethod
    def _as_entity_input(src: dict, item: dict) -> dict:
        return {
            "key_entities": src.get("key_entities", []),
            "title": item.get("title", ""),
            "summary": src.get("summary", ""),
        }


def try_knowledge_workflow_compile(
    knowledge_id: str, ingested_at: str | None = None
) -> dict | None:
    """非阻塞钩子:从 active container 取服务并编译。失败返回 None。"""
    try:
        from src.core.container import get_active_container

        container = get_active_container()
        if container is None:
            return None
        return container.knowledge_workflow.compile(knowledge_id, ingested_at)
    except Exception as e:
        logger.warning("knowledge workflow compile failed (%s): %s", knowledge_id, e)
        return None
