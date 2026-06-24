"""链路追踪 — QueryTrace / StageTrace

Phase 3 / pipeline-hardening: 每次搜索/ask请求自动记录各阶段耗时和结果数，
写入 operation_logs 表，方便问题定位。
"""
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

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
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "tool": self.tool,
            "question": self.question,
            "stages": [s.to_dict() for s in self.stages],
            "total_duration_ms": round(self.total_duration_ms, 1),
            "created_at": self.created_at,
        }

    def save(self) -> None:
        """将 trace 写入 operation_logs 表"""
        try:
            from src.services.db import Database
            db = Database._instance
            if db is None or db._shutdown:
                logger.debug("Cannot save trace: Database not available")
                return

            trace_json = json.dumps(self.to_dict(), ensure_ascii=False)
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO operation_logs (operation, details, timestamp) "
                    "VALUES (?, ?, ?)",
                    (f"trace:{self.tool}", trace_json, self.created_at),
                )
                conn.commit()
        except Exception as e:
            logger.debug("Failed to save trace: %s", e)

    @classmethod
    def get_by_id(cls, trace_id: str) -> dict | None:
        """根据 trace_id 查询追踪记录"""
        try:
            from src.services.db import Database
            db = Database._instance
            if db is None or db._shutdown:
                return None
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT details FROM operation_logs WHERE details LIKE ? ORDER BY timestamp DESC LIMIT 1",
                    (f'%{trace_id}%',),
                ).fetchone()
            if row:
                return json.loads(row["details"])
            return None
        except Exception:
            return None
