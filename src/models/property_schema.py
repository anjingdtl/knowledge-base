"""属性类型 Schema 数据模型"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PropertySchema:
    scope_type: str
    property_name: str
    property_type: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    scope_id: str = ""
    required: int = 0
    default_value: object = None
    choices: list | None = None
    constraints: dict | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id or "",
            "property_name": self.property_name,
            "property_type": self.property_type,
            "required": int(self.required),
            "default_value": json.dumps(self.default_value, ensure_ascii=False) if self.default_value is not None else None,
            "choices": json.dumps(self.choices, ensure_ascii=False) if self.choices is not None else None,
            "constraints": json.dumps(self.constraints, ensure_ascii=False) if self.constraints is not None else None,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "PropertySchema":
        def load(value):
            if value in (None, ""):
                return None
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value

        return cls(
            id=row["id"],
            scope_type=row["scope_type"],
            scope_id=row.get("scope_id") or "",
            property_name=row["property_name"],
            property_type=row["property_type"],
            required=int(row.get("required") or 0),
            default_value=load(row.get("default_value")),
            choices=load(row.get("choices")),
            constraints=load(row.get("constraints")),
            created_at=row.get("created_at", ""),
        )
