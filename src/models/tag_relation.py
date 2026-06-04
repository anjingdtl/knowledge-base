"""标签父子关系数据模型"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TagRelation:
    parent_tag: str
    child_tag: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_row(self) -> dict:
        return {
            "parent_tag": self.parent_tag,
            "child_tag": self.child_tag,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "TagRelation":
        return cls(
            parent_tag=row["parent_tag"],
            child_tag=row["child_tag"],
            created_at=row.get("created_at", ""),
        )
