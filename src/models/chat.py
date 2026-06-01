"""对话数据模型"""
from dataclasses import dataclass, field
from datetime import datetime
import json
import uuid


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Conversation":
        return cls(
            id=row["id"],
            title=row.get("title", ""),
            created_at=row.get("created_at", ""),
        )


@dataclass
class ChatMessage:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str = ""
    role: str = "user"            # user | assistant
    content: str = ""
    sources: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "sources": json.dumps(self.sources, ensure_ascii=False),
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "ChatMessage":
        sources = row.get("sources", "[]")
        if isinstance(sources, str):
            try:
                sources = json.loads(sources)
            except (json.JSONDecodeError, ValueError):
                sources = []
        return cls(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            sources=sources,
            created_at=row.get("created_at", ""),
        )
