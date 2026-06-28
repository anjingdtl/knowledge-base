"""版本冲突检测数据模型"""
import uuid
from dataclasses import dataclass, field
from typing import Optional

from src.utils.time_utils import utcnow_iso


def _make_pair_key(a: str, b: str) -> str:
    """归一化 pair_key：始终 min|max，避免 A/B 与 B/A 重复。"""
    return f"{min(a, b)}|{max(a, b)}"


@dataclass
class ConflictSession:
    """扫描会话"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "scanning"  # scanning | judging | ready | completed | error
    total_items_scanned: int = 0
    candidates_found: int = 0
    pairs_judged: int = 0
    pairs_deleted: int = 0
    pairs_ignored: int = 0
    error: Optional[str] = None
    started_at: str = field(default_factory=utcnow_iso)
    completed_at: Optional[str] = None

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "total_items_scanned": self.total_items_scanned,
            "candidates_found": self.candidates_found,
            "pairs_judged": self.pairs_judged,
            "pairs_deleted": self.pairs_deleted,
            "pairs_ignored": self.pairs_ignored,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "ConflictSession":
        return cls(
            id=row["id"],
            status=row["status"],
            total_items_scanned=row.get("total_items_scanned", 0),
            candidates_found=row.get("candidates_found", 0),
            pairs_judged=row.get("pairs_judged", 0),
            pairs_deleted=row.get("pairs_deleted", 0),
            pairs_ignored=row.get("pairs_ignored", 0),
            error=row.get("error"),
            started_at=row["started_at"],
            completed_at=row.get("completed_at"),
        )


@dataclass
class ConflictPair:
    """候选对"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    item_a_id: str = ""
    item_b_id: str = ""
    candidate_source: str = ""  # sql_tag | sql_title | embedding
    similarity_score: Optional[float] = None
    # LLM 判断结果
    relation_type: Optional[str] = None  # supersedes | superseded_by | partial_overlap | unrelated
    newer_item_id: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None
    # 状态机
    status: str = "pending"  # pending | ignored | deleted
    created_at: str = field(default_factory=utcnow_iso)
    judged_at: Optional[str] = None
    resolved_at: Optional[str] = None

    @property
    def pair_key(self) -> str:
        return _make_pair_key(self.item_a_id, self.item_b_id)

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "item_a_id": self.item_a_id,
            "item_b_id": self.item_b_id,
            "candidate_source": self.candidate_source,
            "similarity_score": self.similarity_score,
            "relation_type": self.relation_type,
            "newer_item_id": self.newer_item_id,
            "confidence": self.confidence,
            "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at,
            "judged_at": self.judged_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "ConflictPair":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            item_a_id=row["item_a_id"],
            item_b_id=row["item_b_id"],
            candidate_source=row["candidate_source"],
            similarity_score=row.get("similarity_score"),
            relation_type=row.get("relation_type"),
            newer_item_id=row.get("newer_item_id"),
            confidence=row.get("confidence"),
            reason=row.get("reason"),
            status=row["status"],
            created_at=row["created_at"],
            judged_at=row.get("judged_at"),
            resolved_at=row.get("resolved_at"),
        )


@dataclass
class ConflictIgnore:
    """忽略记录"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    item_a_id: str = ""
    item_b_id: str = ""
    pair_key: str = ""
    ignored_at: str = field(default_factory=utcnow_iso)
    source_pair_id: Optional[str] = None

    @classmethod
    def from_pair(cls, item_a_id: str, item_b_id: str, source_pair_id: str | None = None) -> "ConflictIgnore":
        return cls(
            item_a_id=item_a_id,
            item_b_id=item_b_id,
            pair_key=_make_pair_key(item_a_id, item_b_id),
            source_pair_id=source_pair_id,
        )

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "item_a_id": self.item_a_id,
            "item_b_id": self.item_b_id,
            "pair_key": self.pair_key,
            "ignored_at": self.ignored_at,
            "source_pair_id": self.source_pair_id,
        }

    @classmethod
    def from_row(cls, row: dict) -> "ConflictIgnore":
        return cls(
            id=row["id"],
            item_a_id=row["item_a_id"],
            item_b_id=row["item_b_id"],
            pair_key=row["pair_key"],
            ignored_at=row["ignored_at"],
            source_pair_id=row.get("source_pair_id"),
        )
