from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class Block:
    """内容块 — Logseq 万物皆块的核心数据模型"""
    id: str                          # UUID
    parent_id: Optional[str] = None  # 父块 ID，根块为 None
    page_id: Optional[str] = None    # 所属页面（knowledge_item 或 wiki_page 的 ID）
    content: str = ""
    block_type: str = "text"         # text, heading, code, quote, list
    properties: dict = field(default_factory=dict)  # 任意 key-value 元数据
    order_idx: int = 0               # 兄弟排序
    created_at: str = ""
    updated_at: str = ""

    def to_row(self) -> dict:
        return {
            "id": self.id, "parent_id": self.parent_id, "page_id": self.page_id,
            "content": self.content, "block_type": self.block_type,
            "properties": json.dumps(self.properties, ensure_ascii=False),
            "order_idx": self.order_idx, "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Block":
        props = row.get("properties", "{}")
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except (json.JSONDecodeError, ValueError):
                props = {}
        return cls(
            id=row["id"], parent_id=row.get("parent_id"), page_id=row.get("page_id"),
            content=row.get("content", ""), block_type=row.get("block_type", "text"),
            properties=props, order_idx=row.get("order_idx", 0),
            created_at=row.get("created_at", ""), updated_at=row.get("updated_at", ""),
        )


@dataclass
class BlockRef:
    """块间引用关系"""
    source_id: str
    target_id: str
    ref_type: str = "link"  # link, embed, property

    def to_row(self) -> dict:
        return {"source_id": self.source_id, "target_id": self.target_id, "ref_type": self.ref_type}


@dataclass
class EntityRef:
    """实体间双向引用（泛化的 wiki_links）"""
    id: str
    source_type: str  # 'knowledge', 'wiki', 'block', 'conversation'
    source_id: str
    target_type: str
    target_id: str
    ref_type: str = "mention"  # mention, link, embed, contains
    weight: float = 1.0
    created_at: str = ""

    def to_row(self) -> dict:
        return {
            "id": self.id, "source_type": self.source_type, "source_id": self.source_id,
            "target_type": self.target_type, "target_id": self.target_id,
            "ref_type": self.ref_type, "weight": self.weight, "created_at": self.created_at,
        }


@dataclass
class BlockProperty:
    """块属性索引（用于快速查询特定 key-value）"""
    block_id: str
    prop_key: str
    prop_value: str
    value_type: str = "string"  # string, number, date, boolean, ref
