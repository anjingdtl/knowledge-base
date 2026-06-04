"""操作日志服务 — 统一记录所有写操作审计日志"""
import json
import logging
from datetime import datetime

from src.utils.config import Config

logger = logging.getLogger(__name__)


class OperationLogService:
    def __init__(self, repo=None):
        self._repo = repo

    def log(self, operation: str, target_type: str, target_id: str,
            operator: str = "system", source: str = "mcp",
            before: dict | None = None,
            after: dict | None = None,
            metadata: dict | None = None) -> str:
        if not Config.get("safety.operation_log.enabled", True):
            return ""
        try:
            return self._repo.insert({
                "operation": operation,
                "target_type": target_type,
                "target_id": target_id,
                "operator": operator,
                "source": source,
                "snapshot_before": json.dumps(before, ensure_ascii=False, default=str) if before else None,
                "snapshot_after": json.dumps(after, ensure_ascii=False, default=str) if after else None,
                "metadata": metadata or {},
                "created_at": datetime.now().isoformat(),
            })
        except Exception:
            logger.warning("Failed to log operation %s on %s/%s", operation, target_type, target_id, exc_info=True)
            return ""

    def query(self, target_type=None, target_id=None, operation=None,
              source=None, limit=50, offset=0) -> list[dict]:
        return self._repo.query(
            target_type=target_type, target_id=target_id,
            operation=operation, source=source,
            limit=limit, offset=offset,
        )

    def get_by_target(self, target_type: str, target_id: str, limit=20) -> list[dict]:
        return self._repo.get_by_target(target_type, target_id, limit=limit)
