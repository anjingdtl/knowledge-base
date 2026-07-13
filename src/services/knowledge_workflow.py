"""KnowledgeWorkflowService — Wiki Authoring 文件系统层编排器。

mode=authoring（及兼容旧值 wiki_first）时,ingest 后编排
source/entity/index/log 四个编译器。
失败隔离(每步 try/except),整体不抛。时间戳由调用方传入(可复现)。

verified / evidence_only 跳过自动编译（只读或仅证据）。

注意:与现有 ``WikiCompiler``(SQLite ``wiki_pages``)是两套并行产物 ——
本服务只管"文件系统 wiki 层"(``wiki/*.md``)。
"""
from __future__ import annotations

import logging
from typing import Any, cast

from src.services.db import Database
from src.services.wiki_entity_updater import WikiEntityUpdater
from src.services.wiki_index_compiler import WikiIndexCompiler
from src.services.wiki_log_compiler import WikiLogCompiler
from src.services.wiki_source_compiler import WikiSourceCompiler
from src.utils.config import Config
from src.utils.knowledge_mode import allows_authoring, resolve_knowledge_mode

logger = logging.getLogger(__name__)


def _workflow_mode_label() -> tuple[str, str]:
    """Return (display_mode, resolved_mode). display keeps raw config for logs/tests."""
    raw = Config.get("knowledge_workflow.mode", None)
    resolved = resolve_knowledge_mode(raw)
    display = str(raw) if raw is not None and str(raw).strip() else resolved
    return display, resolved


class KnowledgeWorkflowService:
    def __init__(
        self,
        source_compiler: WikiSourceCompiler | None = None,
        entity_updater: WikiEntityUpdater | None = None,
        index_compiler: WikiIndexCompiler | None = None,
        log_compiler: WikiLogCompiler | None = None,
        shadow_workflow: Any = None,
        canary_workflow: Any = None,
        primary_workflow: Any = None,
        rebuild_scheduler: Any = None,
    ):
        self._source = source_compiler or WikiSourceCompiler()
        self._entity = entity_updater or WikiEntityUpdater()
        self._index = index_compiler or WikiIndexCompiler()
        self._log = log_compiler or WikiLogCompiler()
        self._shadow = shadow_workflow
        self._canary = canary_workflow
        self._primary = primary_workflow
        self._rebuild = rebuild_scheduler

    def compile(self, knowledge_id: str, ingested_at: str | None = None) -> dict:
        """编排 Authoring 编译（wiki_first 兼容）。失败隔离,不抛。"""
        mode, resolved = _workflow_mode_label()
        if not allows_authoring(resolved):
            return {"mode": mode, "resolved_mode": resolved, "skipped": True}

        item = Database.get_knowledge(knowledge_id)
        if not item:
            return {
                "mode": mode,
                "resolved_mode": resolved,
                "skipped": True,
                "reason": "not_found",
            }
        ts = ingested_at or item.get("created_at") or ""

        result: dict = {"mode": mode, "resolved_mode": resolved, "errors": []}

        try:
            from src.services.wiki_query_service import resolve_canonical_mode

            canonical_mode = resolve_canonical_mode(Config)
        except Exception:
            canonical_mode = "off"

        if self._primary is not None and canonical_mode == "primary":
            try:
                result["primary"] = self._primary.run(
                    knowledge_id=knowledge_id,
                    item=item,
                    source_summary=item.get("title", ""),
                    now=ts,
                )
            except Exception as e:
                logger.warning("primary workflow failed (%s): %s", knowledge_id, e)
                result["errors"].append({"stage": "primary", "error": str(e)})
            self._maybe_schedule_rebuild(knowledge_id, item, canonical_mode)
            return result

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

        if self._shadow is not None and canonical_mode == "shadow":
            try:
                result["shadow"] = self._shadow.run(
                    knowledge_id=knowledge_id,
                    item=item,
                    source_summary=src.get("summary") or item.get("title", ""),
                    now=ts,
                )
            except Exception as e:
                logger.warning("shadow workflow failed (%s): %s", knowledge_id, e)
                result["errors"].append({"stage": "shadow", "error": str(e)})

        if self._canary is not None and canonical_mode == "canary":
            try:
                result["canary"] = self._canary.run(
                    knowledge_id=knowledge_id,
                    item=item,
                    source_summary=src.get("summary") or item.get("title", ""),
                    now=ts,
                )
            except Exception as e:
                logger.warning("canary workflow failed (%s): %s", knowledge_id, e)
                result["errors"].append({"stage": "canary", "error": str(e)})

        return result

    def _maybe_schedule_rebuild(self, knowledge_id: str, item: dict, canonical_mode: str) -> None:
        """Phase 5 门控:auto_on_source_update 或 rebuild.auto_allowlist 命中时 schedule(update)。

        默认 off(auto_on_source_update=false + 空 allowlist)→ 不 schedule,最保守。
        canary 级:auto_allowlist 显式列出的 knowledge_id/source_path 才自动 rebuild。
        """
        if self._rebuild is None or canonical_mode != "primary":
            return
        try:
            if _rebuild_gate_allows(knowledge_id, item):
                self._rebuild.schedule(knowledge_id, "update")
        except Exception as e:
            logger.warning("rebuild schedule failed (%s): %s", knowledge_id, e)

    @staticmethod
    def _as_entity_input(src: dict, item: dict) -> dict:
        return {
            "key_entities": src.get("key_entities", []),
            "title": item.get("title", ""),
            "summary": src.get("summary", ""),
        }

    def save_query(
        self,
        question: str,
        answer: str,
        source_ids: list[str] | None = None,
        confidence: float = 0.0,
        page_type: str = "syntheses",
        save_mode: str = "manual",
        timestamp: str | None = None,
    ) -> dict:
        """准备高价值 query 的 draft wiki 页建议(comparisons/syntheses)。

        auto 模式按阈值(长度≥query_save_min_length + confidence≥0.6 + source≥2)门控;
        manual 模式直接准备。Phase 4C 后不再绕过 WikiRepository 直接写 markdown。
        """
        mode, resolved = _workflow_mode_label()
        if not allows_authoring(resolved):
            return {"status": "skipped", "reason": f"mode={mode}", "resolved_mode": resolved}

        min_len = int(Config.get("wiki.query_save_min_length", 100))
        if save_mode == "auto":
            if len(answer) < min_len or confidence < 0.6 or len(source_ids or []) < 2:
                return {"status": "skipped", "reason": "below_threshold"}

        ts = timestamp or ""
        frontmatter = {
            "title": question[:120],
            "page_type": page_type,
            "status": "draft",
            "confidence": confidence,
            "source_ids": source_ids or [],
            "saved_at": ts,
            "save_mode": save_mode,
        }
        body = f"# {question[:120]}\n\n{answer}\n"

        try:
            self._log.append({
                "type": "query_save",
                "target": question[:60],
                "timestamp": ts,
                "detail": f"{page_type} confidence={confidence:.2f}",
            })
        except Exception as e:
            logger.warning("save_query log append failed: %s", e)

        return {
            "status": "prepared",
            "page_type": page_type,
            "title": question[:120],
            "frontmatter": frontmatter,
            "body": body,
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
        return cast(dict, container.knowledge_workflow.compile(knowledge_id, ingested_at))
    except Exception as e:
        logger.warning("knowledge workflow compile failed (%s): %s", knowledge_id, e)
        return None


def _rebuild_gate_allows(knowledge_id: str, item: dict | None) -> bool:
    """Phase 5 门控:auto_on_source_update=true 或 rebuild.auto_allowlist 命中。默认 false(最保守)。"""
    if Config.get("wiki.rebuild.auto_on_source_update", False):
        return True
    if knowledge_id in (Config.get("wiki.rebuild.auto_allowlist.knowledge_ids", []) or []):
        return True
    src = str((item or {}).get("source_path") or "").replace("\\", "/")
    for prefix in (Config.get("wiki.rebuild.auto_allowlist.source_paths", []) or []):
        p = str(prefix).replace("\\", "/").rstrip("/")
        if p and (src == p or src.startswith(f"{p}/")):
            return True
    return False


def try_schedule_source_delete(knowledge_id: str, item: dict | None = None) -> None:
    """非阻塞钩子:source 删除后,门控命中时 schedule rebuild(delete)。

    默认 off(auto_on_source_update=false + 空 allowlist)→ 不 schedule。
    失败不抛(仅 warning),不影响删除主流程。
    """
    try:
        from src.core.container import get_active_container

        container = get_active_container()
        if container is None or not _rebuild_gate_allows(knowledge_id, item):
            return
        container.wiki_rebuild_scheduler.schedule(knowledge_id, "delete")
    except Exception as e:
        logger.warning("rebuild delete schedule failed (%s): %s", knowledge_id, e)
