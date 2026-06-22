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
    sort_terms: list[tuple[str, str]] = field(default_factory=list)
    include_blocks: bool = False

    def to_json(self) -> dict:
        sort: dict | list[dict]
        if len(self.sort_terms) > 1:
            sort = [{"by": by, "order": order} for by, order in self.sort_terms]
        else:
            sort = {"by": self.sort_by, "order": self.sort_order}
        result = {
            "filter": self.filter_condition.to_json(),
            "limit": self.limit,
            "offset": self.offset,
            "sort": sort,
        }
        if self.include_blocks:
            result["include_blocks"] = True
        return result

    @classmethod
    def from_json(cls, data: dict) -> "QuerySpec":
        filter_data = data.get("filter", {})
        condition = cls._parse_condition(filter_data)
        sort_terms = cls._parse_sort_terms(data.get("sort", {}))
        sort_by, sort_order = sort_terms[0]
        return cls(
            filter_condition=condition,
            limit=data.get("limit", 100),
            offset=data.get("offset", 0),
            sort_by=sort_by,
            sort_order=sort_order,
            sort_terms=sort_terms,
            include_blocks=data.get("include_blocks", False),
        )

    @classmethod
    def _parse_sort_terms(cls, sort_data: Any) -> list[tuple[str, str]]:
        if not sort_data:
            sort_items = [{}]
        elif isinstance(sort_data, list):
            sort_items = sort_data or [{}]
        elif isinstance(sort_data, dict):
            sort_items = [sort_data]
        else:
            raise ValueError("sort must be an object or list of objects")

        terms = []
        for item in sort_items:
            if not isinstance(item, dict):
                raise ValueError("sort entries must be objects")
            sort_by = item.get("by", item.get("field", "updated_at"))
            sort_order = str(item.get("order", "desc")).lower()
            if sort_by not in VALID_SORT_FIELDS:
                raise ValueError(f"invalid sort field: {sort_by}")
            if sort_order not in VALID_SORT_ORDERS:
                raise ValueError(f"invalid sort order: {sort_order}")
            term = (sort_by, sort_order)
            if term not in terms:
                terms.append(term)
        return terms or [("updated_at", "desc")]

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
        # BUG-4 fix: "tags" (plural) 兼容 — 标准化为 "tag" 过滤器
        if "tags" in data:
            tag_data = data["tags"]
            if isinstance(tag_data, dict):
                # {"tags": {"contains": "xxx"}} → tag match
                if "contains" in tag_data:
                    tag_data = tag_data["contains"]
                elif "eq" in tag_data:
                    tag_data = tag_data["eq"]
                elif "in" in tag_data:
                    # {"tags": {"in": ["a", "b"]}} → OR of tags
                    tag_list = tag_data["in"]
                    if not isinstance(tag_list, list):
                        tag_list = [tag_list]
                    children = [Condition(type="tag", value=t, expand_descendants=True)
                                for t in tag_list]
                    if len(children) == 1:
                        return children[0]
                    return Condition(type="or", children=children)
                else:
                    raise ValueError(f"unknown tags operator: {list(tag_data.keys())}")
            return Condition(
                type="tag",
                value=tag_data,
                expand_descendants=data.get("expand_descendants", True),
            )
        if "tag" in data:
            tag_data = data["tag"]
            if isinstance(tag_data, dict):
                if "eq" in tag_data:
                    tag_data = tag_data["eq"]
                else:
                    raise ValueError(f"unknown tag operator: {list(tag_data.keys())}")
            return Condition(
                type="tag",
                value=tag_data,
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
