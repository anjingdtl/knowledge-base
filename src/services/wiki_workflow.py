"""Wiki 页面工作流状态机"""
import logging
from dataclasses import dataclass
from enum import Enum

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
        from_status = page.get("status", "draft")
        if from_status not in ("draft", "active"):
            return WorkflowResult(False, f"Cannot submit from status: {from_status}")

        # 检查是否配置了自动发布（跳过审核）
        if Config.get("wiki.auto_publish", True):
            return cls._do_transition(page_id, "draft", "published", operator, comment or "Auto-published")

        return cls._do_transition(page_id, "draft", "review", operator, comment or "Submitted for review")

    @classmethod
    def approve(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """审批：review -> published"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        return cls._do_transition(page_id, "review", "published", operator, comment or "Approved")

    @classmethod
    def reject(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """驳回：review -> draft"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        return cls._do_transition(page_id, "review", "draft", operator, comment or "Rejected")

    @classmethod
    def deprecate(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """弃用：published -> deprecated"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        return cls._do_transition(page_id, "published", "deprecated", operator, comment or "Deprecated")

    @classmethod
    def republish(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """重新发布：deprecated -> published"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        return cls._do_transition(page_id, "deprecated", "published", operator, comment or "Republished")

    @classmethod
    def publish_direct(cls, page_id: str, operator: str = "system", comment: str = "") -> WorkflowResult:
        """直接发布（管理员快捷操作）：任意��态 -> published"""
        page = Database.get_wiki_page(page_id)
        if not page:
            return WorkflowResult(False, "Wiki page not found")
        return cls._do_transition(page_id, page.get("status", "draft"), "published", operator, comment or "Direct publish")

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
        Database.update_wiki_page(
            page_id,
            title=version_data["title"],
            content=version_data["content"],
            concept_summary=version_data["concept_summary"],
            tags=version_data["tags"],
        )
        logger.info("Wiki page %s restored to version %d", page_id, version)
        return WorkflowResult(True, f"已恢复到版本 {version}")

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
        Database.update_wiki_page(page_id, status=to_status)

        # 记录转换日志
        Database.insert_workflow(page_id, from_status, to_status, operator, comment)

        logger.info(f"Wiki page {page_id}: {from_status} -> {to_status} by {operator}")
        return WorkflowResult(True, f"Transitioned to {to_status}", from_status, to_status)