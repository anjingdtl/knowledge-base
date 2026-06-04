"""统一图谱节点和边的数据模型"""
from dataclasses import dataclass, field


@dataclass
class UnifiedNode:
    id: str
    node_type: str
    label: str
    source_id: str = ""
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.node_type,
            "label": self.label,
            "source_id": self.source_id,
            "properties": self.properties,
        }


@dataclass
class UnifiedEdge:
    source: str
    target: str
    edge_type: str
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.edge_type,
            "properties": self.properties,
        }
