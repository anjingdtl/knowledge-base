"""链路追踪 — QueryTrace / StageTrace

Phase 3 / pipeline-hardening: 每次搜索/ask请求自动记录各阶段耗时和结果数，
写入 operation_logs 表，方便问题定位。
"""
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class StageTrace:
    """单个管线阶段的追踪记录"""
    name: str               # wiki_retrieval / vector_search / rerank / generate / postprocess
    duration_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    result_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QueryTrace:
    """一次查询的完整追踪记录"""
    trace_id: str
    tool: str               # search / ask / route_query / ask_with_query
    question: str
    stages: list[StageTrace]
    total_duration_ms: float
    # 本地时间无偏移，与 OperationLogRepository.insert 保持一致——
    # operation_logs.created_at 是 TEXT 列按字符串排序，格式必须统一否则 ORDER BY 错乱。
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        # 与全仓库其它日志路径一致，question 落盘前截断——trace 是持久化路径
        # （operation_logs 随 data/ 落盘 + Syncthing 同步），全量明文留存有
        # 隐私/PII 风险。长度可由 rag.observability.trace_question_max_len 配置。
        from src.utils.config import Config
        max_len = int(Config.get("rag.observability.trace_question_max_len", 80) or 80)
        question = self.question
        if max_len > 0 and len(question) > max_len:
            question = question[:max_len] + "..."
        return {
            "trace_id": self.trace_id,
            "tool": self.tool,
            "question": question,
            "stages": [s.to_dict() for s in self.stages],
            "total_duration_ms": round(self.total_duration_ms, 1),
            "created_at": self.created_at,
        }

    def save(self) -> None:
        """将 trace 写入 operation_logs 表。

        operation_logs 真实 schema（见 db.py）:
            id, operation, target_type, target_id, operator, source,
            snapshot_before, snapshot_after, metadata, created_at
        其中 id/operation/target_type/target_id/created_at 为 NOT NULL。
        trace_id 存入 target_id 便于精确查询，trace JSON 存入 metadata。
        """
        try:
            from src.services.db import Database
            db = Database._instance
            if db is None or db._shutdown:
                logger.debug("Cannot save trace: Database not available")
                return

            trace_json = json.dumps(self.to_dict(), ensure_ascii=False)
            log_id = f"trace-{self.trace_id}-{uuid.uuid4().hex[:8]}"
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO operation_logs "
                    "(id, operation, target_type, target_id, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (log_id, f"trace:{self.tool}", "trace", self.trace_id,
                     trace_json, self.created_at),
                )
                conn.commit()
        except Exception as e:
            logger.debug("Failed to save trace: %s", e)

    @classmethod
    def get_by_id(cls, trace_id: str) -> dict | None:
        """根据 trace_id 精确查询追踪记录。"""
        try:
            from src.services.db import Database
            db = Database._instance
            if db is None or db._shutdown:
                return None
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT metadata FROM operation_logs "
                    "WHERE target_type = 'trace' AND target_id = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (trace_id,),
                ).fetchone()
            if row:
                return json.loads(row["metadata"])
            return None
        except Exception:
            return None

    @classmethod
    def cleanup_old(cls, retention_days: int = 30) -> int:
        """删除超过 retention_days 天的 trace 记录，返回删除条数。

        trace 默认 trace_enabled=True 会持续写入 operation_logs（target_type='trace'），
        项目无独立调度器，借 kb_health_check 触发清理，避免无限留存 + 膨胀。
        """
        try:
            from datetime import datetime, timedelta

            from src.services.db import Database
            db = Database._instance
            if db is None or db._shutdown:
                return 0
            cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
            with db.get_conn() as conn:
                cursor = conn.execute(
                    "DELETE FROM operation_logs "
                    "WHERE target_type = 'trace' AND created_at < ?",
                    (cutoff,),
                )
                conn.commit()
                return cursor.rowcount or 0
        except Exception as e:
            logger.debug("Trace cleanup failed: %s", e)
            return 0
