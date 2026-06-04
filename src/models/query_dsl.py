from dataclasses import dataclass, field
from typing import Any


VALID_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "like"}
VALID_SORT_FIELDS = {"updated_at", "created_at", "title", "version", "file_type"}
VALID_SORT_ORDERS = {"asc", "desc"}


@dataclass
class Condition:
    type: str
    value: Any = None
    key: str = ""
    op: str = "eq"
    children: list = field(default_factory=list)
    child: Any = None
    expand_descendants: bool = True

    def to_json(self) -> dict:
        if self.type == "and":
            return {"and": [c.to_json() for c in self.children]}
        if self.type == "or":
            return {"or": [c.to_json() for c in self.children]}
        if self.type == "not":
            return {"not": self.child.to_json()}
        if self.type == "tag":
            return {"tag": self.value, "expand_descendants": self.expand_descendants}
        if self.type == "property":
            return {"property": {"key": self.key, "op": self.op, "value": self.value}}
        if self.type == "fulltext":
            return {"fulltext": self.value}
        if self.type == "link":
            return {"link": self.value}
        if self.type == "file_type":
            return {"file_type": self.value}
        if self.type == "source_type":
            return {"source_type": self.value}
        return {}


@dataclass
class QuerySpec:
    filter_condition: Condition
    limit: int = 100
    offset: int = 0
    sort_by: str = "updated_at"
    sort_order: str = "desc"
    include_blocks: bool = False

    def to_json(self) -> dict:
        result = {
            "filter": self.filter_condition.to_json(),
            "limit": self.limit,
            "offset": self.offset,
            "sort": {"by": self.sort_by, "order": self.sort_order},
        }
        if self.include_blocks:
            result["include_blocks"] = True
        return result

    @classmethod
    def from_json(cls, data: dict) -> "QuerySpec":
        filter_data = data.get("filter", {})
        condition = cls._parse_condition(filter_data)
        sort_data = data.get("sort", {})
        sort_by = sort_data.get("by", "updated_at")
        sort_order = sort_data.get("order", "desc")
        if sort_by not in VALID_SORT_FIELDS:
            raise ValueError(f"invalid sort field: {sort_by}")
        if sort_order not in VALID_SORT_ORDERS:
            raise ValueError(f"invalid sort order: {sort_order}")
        return cls(
            filter_condition=condition,
            limit=data.get("limit", 100),
            offset=data.get("offset", 0),
            sort_by=sort_by,
            sort_order=sort_order,
            include_blocks=data.get("include_blocks", False),
        )

    @classmethod
    def _parse_condition(cls, data: dict) -> Condition:
        if not data:
            return Condition(type="and", children=[])
        if "and" in data:
            return Condition(
                type="and",
                children=[cls._parse_condition(c) for c in data["and"]],
            )
        if "or" in data:
            return Condition(
                type="or",
                children=[cls._parse_condition(c) for c in data["or"]],
            )
        if "not" in data:
            return Condition(
                type="not",
                child=cls._parse_condition(data["not"]),
            )
        if "tag" in data:
            return Condition(
                type="tag",
                value=data["tag"],
                expand_descendants=data.get("expand_descendants", True),
            )
        if "property" in data:
            prop = data["property"]
            op = prop.get("op", "eq")
            if op not in VALID_OPS:
                raise ValueError(f"unknown operator: {op}")
            return Condition(
                type="property",
                key=prop["key"],
                op=op,
                value=prop["value"],
            )
        if "fulltext" in data:
            return Condition(type="fulltext", value=data["fulltext"])
        if "link" in data:
            return Condition(type="link", value=data["link"])
        if "file_type" in data:
            return Condition(type="file_type", value=data["file_type"])
        if "source_type" in data:
            return Condition(type="source_type", value=data["source_type"])
        raise ValueError(f"unknown filter type in: {list(data.keys())}")
