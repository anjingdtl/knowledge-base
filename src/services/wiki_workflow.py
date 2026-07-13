"""Wiki 页面工作流状态机"""
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from src.models.wiki_v2 import PageStatus, PageType, WikiPage
from src.services.db import Database
from src.utils.config import Config

logger = logging.getLogger(__name__)


class WikiStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    # 向后兼容的旧状态映射
    ACTIVE = "active"
    ORPHAN = "orphan"


# 有效状态转换
VALID_TRANSITIONS = {
    WikiStatus.DRAFT: {WikiStatus.REVIEW, WikiStatus.PUBLISHED},  # 直接发布跳过审核
    WikiStatus.REVIEW: {WikiStatus.PUBLISHED, WikiStatus.DRAFT},  # 批准或驳回
    WikiStatus.PUBLISHED: {WikiStatus.DEPRECATED, WikiStatus.DRAFT},
    WikiStatus.DEPRECATED: {WikiStatus.PUBLISHED},  # 可以重新发布
}


@dataclass
class WorkflowResult:
    success: bool
    message: str
    from_status: str = ""
    to_status: str = ""


class WikiWorkflow:
    """Wiki 页面工作流管理"""

    @staticmethod
    def _content_hash(content: str) -> str:
        return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _json_list(value) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        return []

    @classmethod
    def _page_status(cls, status: str | None) -> PageStatus:
        normalized = cls._normalize_status(status or "draft")
        if normalized == "published":
            return PageStatus.PUBLISHED
        if normalized == "review":
            return PageStatus.REVIEW
        if normalized == "deprecated":
            return PageStatus.DEPRECATED
        if normalized == "deleted":
            return PageStatus.DELETED
        return PageStatus.DRAFT

    @classmethod
    def _legacy_page_to_canonical(cls, page: dict) -> WikiPage:
        now = datetime.now().isoformat()
        content = page.get("content", "") or ""
        return WikiPage(
            schema_version=1,
            page_id=page["id"],
            title=page.get("title") or "Untitled",
            page_type=PageType.SYNTHESES,
            status=cls._page_status(page.get("status")),
            revision=0,
            aliases=[],
            tags=cls._json_list(page.get("tags", "[]")),
            source_ids=cls._json_list(page.get("source_ids", "[]")),
            claim_ids=[],
            created_at=page.get("created_at") or now,
            updated_at=page.get("updated_at") or now,
            content_hash=cls._content_hash(content),
            body=content,
        )

    @staticmethod
    def _canonical_services():
        try:
            from src.core.container import get_active_container
            container = get_active_container()
        except Exception:
            container = None
        if container is not None:
            return container.wiki_repository, container.wiki_projection

        from src.services.wiki_projection import WikiProjection
        from src.services.wiki_repository import WikiRepository

        wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))
        repo = WikiRepository(
            wiki_dir=wiki_dir,
            registry_path=wiki_dir / "_meta" / "pages.json",
            redirects_path=wiki_dir / "_meta" / "redirects.json",
            outbox_path=Path(Config.get("storage.data_dir", "data")) / "wiki_projection_outbox.jsonl",
        )
        return repo, WikiProjection(repo, Database, enabled=True)

    @classmethod
    def _save_canonical_page(
        cls,
        page_id: str,
        legacy_page: dict,
        *,
        repository=None,
        projection=None,
        **fields,
    ) -> None:
        if repository is None or projection is None:
            default_repository, default_projection = cls._canonical_services()
            repository = repository or default_repository
            projection = projection or default_projection
        repo = repository
        page = repo.get_page(page_id)
        if page is not None and fields.get("title") and page.title != fields["title"]:
            repo.move_page(page_id, fields["title"], page.page_type.value)
            page = repo.get_page(page_id)
        if page is None:
            page = cls._legacy_page_to_canonical(legacy_page)
            if fields.get("title"):
                page.title = fields["title"]

        if "content" in fields:
            page.body = fields["content"] or ""
            page.content_hash = cls._content_hash(page.body)
        if "tags" in fields:
            page.tags = cls._json_list(fields["tags"])
        if "source_ids" in fields:
            page.source_ids = cls._json_list(fields["source_ids"])
        if "status" in fields:
            page.status = cls._page_status(fields["status"])
        page.updated_at = datetime.now().isoformat()

        expected_revision = page.revision if page.revision else None
        repo.save_page(page, expected_revision=expected_revision)
        try:
            projection.process_outbox(force=True)
        except TypeError:
            projection.process_outbox()
        legacy_fields = {}
        if "content" in fields:
            # Markdown serialization normalizes a trailing newline; legacy callers expect exact text.
            legacy_fields["content"] = fields["content"] or ""
        if "concept_summary" in fields:
            legacy_fields["concept_summary"] = fields["concept_summary"] or ""
        if legacy_fields and hasattr(projection, "update_legacy_page_fields"):
            projection.update_legacy_page_fields(page_id, **legacy_fields)

    @staticmethod
    def can_transition(from_status: str, to_status: str) -> bool:
        """检查状态转换是否有效"""
        # 旧状态兼容映射
        legacy_map = {
            "active": WikiStatus.PUBLISHED,
            "orphan": WikiStatus.DEPRECATED,
            WikiStatus.ACTIVE: WikiStatus.PUBLISHED,
            WikiStatus.ORPHAN: WikiStatus.DEPRECATED,
        }
        try:
            from_state = WikiStatus(from_status)
        except ValueError:
            return False
        # 映射旧状态到新状态
        from_state = legacy_map.get(from_state, from_state)

        try:
            to_state = WikiStatus(to_status)
        except ValueError:
            return False
        to_state = legacy_map.get(to_state, to_state)

        return to_state in VALID_TRANSITIONS.get(from_state, set())

    @classmethod
    def submit_for_review(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """提交审核：draft -> review"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        from_status = cls._normalize_status(page.get("status", "draft"))
        if from_status not in ("draft",):
            return WorkflowResult(False, f"Cannot submit from status: {from_status}")

        # 检查是否配置了自动发布（跳过审核）
        if Config.get("wiki.auto_publish", True):
            return cls._do_transition(page_id, from_status, "published", operator, comment or "Auto-published")

        return cls._do_transition(page_id, from_status, "review", operator, comment or "Submitted for review")

    @staticmethod
    def _normalize_status(status: str) -> str:
        """将旧状态（active/orphan）映射到新状态。"""
        legacy_map = {"active": "published", "orphan": "deprecated"}
        return legacy_map.get(status, status)

    @classmethod
    def approve(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """审批：review -> published"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        current = cls._normalize_status(page.get("status", "draft"))
        if current != "review":
            return WorkflowResult(False, f"Cannot approve: current status is '{current}', expected 'review'")
        return cls._do_transition(page_id, "review", "published", operator, comment or "Approved")

    @classmethod
    def reject(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """驳回：review -> draft"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        current = cls._normalize_status(page.get("status", "draft"))
        if current != "review":
            return WorkflowResult(False, f"Cannot reject: current status is '{current}', expected 'review'")
        return cls._do_transition(page_id, "review", "draft", operator, comment or "Rejected")

    @classmethod
    def deprecate(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """弃用：published -> deprecated"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        current = cls._normalize_status(page.get("status", "draft"))
        if current != "published":
            return WorkflowResult(False, f"Cannot deprecate: current status is '{current}', expected 'published'")
        return cls._do_transition(page_id, "published", "deprecated", operator, comment or "Deprecated")

    @classmethod
    def republish(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """重新发布：deprecated -> published"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        current = cls._normalize_status(page.get("status", "draft"))
        if current != "deprecated":
            return WorkflowResult(False, f"Cannot republish: current status is '{current}', expected 'deprecated'")
        return cls._do_transition(page_id, "deprecated", "published", operator, comment or "Republished")

    @classmethod
    def publish_direct(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """直接发布（管理员快捷操作）：draft/review -> published"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        current = cls._normalize_status(page.get("status", "draft"))
        if current == "published":
            return WorkflowResult(True, "Already published", from_status=current, to_status="published")
        if current not in ("draft", "review"):
            return WorkflowResult(False, f"Cannot direct-publish from status: {current}")
        return cls._do_transition(page_id, current, "published", operator, comment or "Direct publish")

    @classmethod
    def get_history(cls, page_id: str) -> list[dict]:
        """获取工作流历史"""
        return Database.get_workflow_history(page_id)

    @classmethod
    def restore_version(cls, page_id: str, version: int) -> WorkflowResult:
        """恢复到指定版本（先保存当前快照，再恢复，整体在事务中）"""
        version_data = Database.get_wiki_version(page_id, version)
        if not version_data:
            return WorkflowResult(False, "版本不存在")
        # 事务保护：先保存当前版本，再恢复目标版本
        current = Database.get_wiki_page(page_id)
        if current:
            Database.save_wiki_version(page_id, current)
        cls._save_canonical_page(
            page_id,
            current or {"id": page_id, "status": "draft"},
            title=version_data["title"],
            content=version_data["content"],
            concept_summary=version_data["concept_summary"],
            tags=version_data["tags"],
            # 恢复后将状态改为 draft，标记需要重新审核
            status="draft",
        )
        logger.info("Wiki page %s restored to version %d, status reset to draft", page_id, version)
        return WorkflowResult(True, f"已恢复到版本 {version}（状态已重置为 draft）")

    @classmethod
    def _do_transition(cls, page_id: str, from_status: str, to_status: str,
                       operator: str, comment: str) -> WorkflowResult:
        """执行状态转换"""
        # 验证转换有效性
        if not cls.can_transition(from_status, to_status):
            return WorkflowResult(False, f"Invalid transition: {from_status} -> {to_status}")

        # 保存版本快照
        page = Database.get_wiki_page(page_id)
        if page:
            Database.save_wiki_version(page_id, page)

        # 更新页面状态
        cls._save_canonical_page(page_id, page or {"id": page_id, "status": from_status}, status=to_status)

        # 记录转换日志
        Database.insert_workflow(page_id, from_status, to_status, operator, comment)

        logger.info(f"Wiki page {page_id}: {from_status} -> {to_status} by {operator}")
        return WorkflowResult(True, f"Transitioned to {to_status}", from_status, to_status)
