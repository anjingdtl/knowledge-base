"""WikiWriteService —— 双轨 wiki 统一写入入口(轻量收敛 Task 3)。

收敛 save_to_wiki(mcp_server)+ _try_auto_save_wiki(rag_pipeline)两处双写:
A 轨(WikiCompiler.save_answer → SQLite wiki_pages)+ B 轨
(KnowledgeWorkflowService.save_query → FS wiki/*.md)。

任一失败不阻塞另一个(统一容错:warning + errors 记录),行为等价于改前
两处独立 try/except,但收敛到单一入口便于未来完整迁移时改路由。
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime

from src.models.wiki_v2 import PageStatus, PageType, WikiPage

logger = logging.getLogger(__name__)


class WikiWriteService:
    def __init__(
        self,
        wiki_compiler,
        knowledge_workflow,
        repository=None,
        projection=None,
        config=None,
    ):
        self._compiler = wiki_compiler
        self._workflow = knowledge_workflow
        self._repo = repository
        self._projection = projection
        self._config = config

    def _cfg(self, key: str, default=None):
        if self._config is not None:
            return self._config.get(key, default)
        from src.utils.config import Config
        return Config.get(key, default)

    def save(self, question, answer, source_ids, *,
             confidence: float = 0.0, save_mode: str = "manual",
             auto_publish=None, enhance: bool = True,
             timestamp: str = "") -> dict:
        """统一双写 A(SQLite save_answer)+ B(FS save_query)。

        Returns:
            ``{sqlite_page_id, fs_saved, errors}``。任一失败不阻塞另一个。
        """
        result: dict = {
            "sqlite_page_id": None,
            "fs_saved": False,
            "errors": [],
            "page_id": None,
            "canonical_saved": False,
            "projection_pending": False,
            "projection_processed": 0,
        }
        if self._cfg("wiki.canonical_v2.mode", "off") == "primary" and self._repo is not None:
            return self._save_primary(
                question=question,
                answer=answer,
                source_ids=source_ids or [],
                timestamp=timestamp,
                result=result,
            )
        try:
            result["sqlite_page_id"] = self._compiler.save_answer(
                question, answer, source_ids,
                auto_publish=auto_publish, enhance=enhance)
        except Exception as e:
            logger.warning("WikiWriteService sqlite save failed: %s", e)
            result["errors"].append(f"sqlite: {e}")
        try:
            self._workflow.save_query(
                question, answer, source_ids,
                confidence=confidence, save_mode=save_mode, timestamp=timestamp)
            result["fs_saved"] = True
        except Exception as e:
            logger.warning("WikiWriteService fs save failed: %s", e)
            result["errors"].append(f"fs: {e}")
        return result

    def _save_primary(
        self,
        *,
        question: str,
        answer: str,
        source_ids: list[str],
        timestamp: str,
        result: dict,
    ) -> dict:
        ts = timestamp or datetime.now().isoformat()
        title = question.strip().replace("\n", " ")[:120] or "Untitled Query"
        body = f"# {title}\n\n{answer}\n"
        page = WikiPage(
            schema_version=1,
            page_id=f"page_{uuid.uuid4()}",
            title=title,
            page_type=PageType.SYNTHESES,
            status=PageStatus.DRAFT,
            revision=1,
            aliases=[],
            tags=[],
            source_ids=list(source_ids),
            claim_ids=[],
            created_at=ts,
            updated_at=ts,
            content_hash="sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest(),
            body=body,
        )
        self._repo.save_page(page, expected_revision=None)
        result["page_id"] = page.page_id
        result["canonical_saved"] = True

        if self._projection is None:
            result["projection_pending"] = True
            return result

        projection_result = self._projection.process_outbox()
        result["projection_processed"] = int(getattr(projection_result, "processed", 0))
        projection_errors = list(getattr(projection_result, "errors", []))
        if projection_errors:
            result["projection_pending"] = True
            result["errors"].extend(f"projection: {e}" for e in projection_errors)
        return result
