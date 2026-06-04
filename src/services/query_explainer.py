from src.models.query_dsl import Condition, QuerySpec


class QueryExplainer:
    def explain(self, spec: QuerySpec) -> dict:
        summary = self._summarize(spec.filter_condition)
        plan = self._build_plan(spec)
        tree = self._build_tree(spec.filter_condition)
        return {
            "summary": summary,
            "plan": plan,
            "condition_tree": tree,
            "spec": spec.to_json(),
        }

    def _summarize(self, condition: Condition, depth: int = 0) -> str:
        if condition.type == "and":
            parts = [self._summarize(c, depth + 1) for c in condition.children]
            joiner = " AND "
            result = joiner.join(parts)
            return f"({result})" if depth > 0 else result
        if condition.type == "or":
            parts = [self._summarize(c, depth + 1) for c in condition.children]
            joiner = " OR "
            result = joiner.join(parts)
            return f"({result})" if depth > 0 else result
        if condition.type == "not":
            return f"NOT {self._summarize(condition.child, depth + 1)}"
        if condition.type == "tag":
            suffix = " (with descendants)" if condition.expand_descendants else ""
            return f"tag = '{condition.value}'{suffix}"
        if condition.type == "property":
            return f"property '{condition.key}' {condition.op} '{condition.value}'"
        if condition.type == "fulltext":
            return f"fulltext search '{condition.value}'"
        if condition.type == "link":
            return f"links to '{condition.value}'"
        if condition.type == "file_type":
            return f"file type = '{condition.value}'"
        if condition.type == "source_type":
            return f"source type = '{condition.value}'"
        return "empty filter"

    def _build_plan(self, spec: QuerySpec) -> dict:
        tables = set()
        indexes = []
        self._collect_tables(spec.filter_condition, tables, indexes)
        if spec.include_blocks:
            tables.add("blocks")
        complexity = self._estimate_complexity(spec.filter_condition, tables)
        return {
            "tables_used": sorted(tables),
            "indexes_used": sorted(indexes),
            "estimated_complexity": complexity,
            "pagination": {"limit": spec.limit, "offset": spec.offset},
            "sort": {"field": spec.sort_by, "order": spec.sort_order},
        }

    def _collect_tables(self, condition: Condition, tables: set, indexes: list):
        tables.add("knowledge_items")
        if condition.type in ("and", "or"):
            for child in condition.children:
                self._collect_tables(child, tables, indexes)
        elif condition.type == "not":
            self._collect_tables(condition.child, tables, indexes)
        elif condition.type == "tag":
            indexes.append("json_each(ki.tags)")
        elif condition.type == "property":
            tables.add("effective_property_index")
            tables.add("blocks")
            indexes.append("idx_effective_prop_key_val")
        elif condition.type == "fulltext":
            tables.add("knowledge_fts")
            indexes.append("knowledge_fts MATCH")
        elif condition.type == "link":
            tables.add("entity_refs")
            tables.add("knowledge_items")

    def _estimate_complexity(self, condition: Condition, tables: set) -> str:
        depth = self._max_depth(condition)
        table_count = len(tables)
        if depth <= 1 and table_count <= 2:
            return "low"
        if depth <= 2 and table_count <= 4:
            return "medium"
        return "high"

    def _max_depth(self, condition: Condition) -> int:
        if condition.type in ("and", "or"):
            if not condition.children:
                return 0
            return 1 + max(self._max_depth(c) for c in condition.children)
        if condition.type == "not":
            return 1 + self._max_depth(condition.child)
        return 0

    def _build_tree(self, condition: Condition) -> dict:
        node = {"type": condition.type}
        if condition.type in ("and", "or"):
            node["children"] = [self._build_tree(c) for c in condition.children]
        elif condition.type == "not":
            node["child"] = self._build_tree(condition.child)
        elif condition.type == "tag":
            node["value"] = condition.value
            node["expand_descendants"] = condition.expand_descendants
        elif condition.type == "property":
            node["key"] = condition.key
            node["op"] = condition.op
            node["value"] = condition.value
        elif condition.type in ("fulltext", "link", "file_type", "source_type"):
            node["value"] = condition.value
        return node
