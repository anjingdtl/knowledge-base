"""知识条目数据模型"""
import json
import uuid
from dataclasses import dataclass, field

from src.utils.time_utils import utcnow_iso


@dataclass
class KnowledgeItem:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    content: str = ""
    source_type: str = "manual"   # file | manual | web
    source_path: str = ""
    file_type: str = ""           # pdf | docx | xlsx | csv | txt | md | image | code | html
    file_size: int = 0            # bytes
    content_hash: str = ""        # sha256 of content
    quality: str = ""             # "" | "ok" | "garbled"
    file_created_at: str = ""     # 原始文件创建时间戳
    file_modified_at: str = ""    # 原始文件修改时间戳
    tags: list[str] = field(default_factory=list)
    version: int = 1
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "content_hash": self.content_hash,
            "file_created_at": self.file_created_at,
            "file_modified_at": self.file_modified_at,
            "tags": json.dumps(self.tags, ensure_ascii=False),
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "KnowledgeItem":
        tags = row.get("tags", "[]")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, ValueError):
                tags = []
        return cls(
            id=row["id"],
            title=row["title"],
            content=row.get("content", ""),
            source_type=row.get("source_type", "manual"),
            source_path=row.get("source_path", ""),
            file_type=row.get("file_type", ""),
            file_size=row.get("file_size", 0),
            content_hash=row.get("content_hash", ""),
            file_created_at=row.get("file_created_at", ""),
            file_modified_at=row.get("file_modified_at", ""),
            tags=tags,
            version=row.get("version", 1),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
        )


@dataclass
class KnowledgeChunk:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    knowledge_id: str = ""
    chunk_index: int = 0
    chunk_text: str = ""
    created_at: str = field(default_factory=utcnow_iso)

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "knowledge_id": self.knowledge_id,
            "chunk_index": self.chunk_index,
            "chunk_text": self.chunk_text,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "KnowledgeChunk":
        return cls(
            id=row["id"],
            knowledge_id=row["knowledge_id"],
            chunk_index=row["chunk_index"],
            chunk_text=row["chunk_text"],
            created_at=row.get("created_at", ""),
        )
