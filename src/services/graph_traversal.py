from collections import deque

from src.services.db import Database


class GraphTraversalService:
    def __init__(self, db=None):
        self._db = db or Database

    def traverse(
        self,
        start_ids: list[str],
        start_type: str = "knowledge",
        max_depth: int = 2,
        ref_types: list[str] | None = None,
        node_filter=None,
        max_nodes: int = 200,
    ) -> dict:
        conn = self._db.get_conn()
        nodes = {}
        edges = []
        paths = []
        visited = set()
        queue = deque()

        filter_ids = None
        if node_filter is not None:
            from src.services.query_executor import QueryExecutor
            filter_results = QueryExecutor(db=self._db).execute(node_filter)
            filter_ids = {r["id"] for r in filter_results}

        for sid in start_ids:
            queue.append((sid, start_type, 0, [sid]))

        while queue:
            current_id, current_type, depth, path = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)

            if len(nodes) >= max_nodes:
                break

            if filter_ids is not None and current_id not in filter_ids and depth > 0:
                continue

            node_data = self._load_node(current_id, current_type, conn)
            if node_data:
                nodes[current_id] = node_data

            if depth > 0:
                edges.append({
                    "source": path[-2],
                    "target": current_id,
                    "type": "link",
                    "depth": depth,
                })
                paths.append(path)

            if depth >= max_depth:
                continue

            neighbors = self._find_neighbors(current_id, current_type, ref_types, conn)
            for neighbor_id, neighbor_type, ref_type in neighbors:
                if neighbor_id not in visited:
                    queue.append((neighbor_id, neighbor_type, depth + 1, path + [neighbor_id]))

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "paths": paths,
            "truncated": len(nodes) >= max_nodes,
        }

    def _load_node(self, node_id: str, node_type: str, conn) -> dict | None:
        if node_type in ("knowledge", "page"):
            row = conn.execute(
                "SELECT id, title, file_type, tags FROM knowledge_items WHERE id = ?",
                (node_id,),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "type": "page",
                    "label": row["title"],
                    "properties": {"file_type": row["file_type"]},
                }
        if node_type == "block":
            row = conn.execute(
                "SELECT id, content, page_id FROM blocks WHERE id = ?",
                (node_id,),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "type": "block",
                    "block_id": row["id"],
                    "label": (row["content"] or row["id"])[:80],
                    "properties": {
                        "block_id": row["id"],
                        "page_id": row["page_id"],
                    },
                }
        return None

    def _find_neighbors(self, node_id: str, node_type: str,
                        ref_types: list[str] | None, conn) -> list[tuple[str, str, str]]:
        neighbors = []
        if ref_types:
            rt_clause = "AND ref_type IN ({})".format(
                ",".join("?" for _ in ref_types)
            )
            rt_params = list(ref_types)
        else:
            rt_clause = ""
            rt_params = []

        if node_type in ("knowledge", "page"):
            rows = conn.execute(
                f"""SELECT er.target_id, er.target_type, er.ref_type
                    FROM entity_refs er
                    WHERE er.source_id IN (
                        SELECT id FROM blocks WHERE page_id = ?
                    ) AND er.source_type = 'block' {rt_clause}
                    UNION
                    SELECT er.target_id, er.target_type, er.ref_type
                    FROM entity_refs er
                    WHERE er.source_id = ? AND er.source_type = 'knowledge' {rt_clause}""",
                [node_id] + rt_params + [node_id] + rt_params,
            ).fetchall()
            for row in rows:
                neighbors.append((row["target_id"], row["target_type"], row["ref_type"]))

            back_rows = conn.execute(
                f"""SELECT er.source_id, er.source_type, er.ref_type
                    FROM entity_refs er
                    WHERE er.target_id = ? AND er.target_type = 'knowledge' {rt_clause}""",
                [node_id] + rt_params,
            ).fetchall()
            for row in back_rows:
                if row["source_type"] == "block":
                    page_row = conn.execute(
                        "SELECT page_id FROM blocks WHERE id = ?", (row["source_id"],)
                    ).fetchone()
                    if page_row:
                        neighbors.append((page_row["page_id"], "knowledge", row["ref_type"]))
                else:
                    neighbors.append((row["source_id"], row["source_type"], row["ref_type"]))

        elif node_type == "block":
            rows = conn.execute(
                f"""SELECT er.target_id, er.target_type, er.ref_type
                    FROM entity_refs er
                    WHERE er.source_id = ? AND er.source_type = 'block' {rt_clause}""",
                [node_id] + rt_params,
            ).fetchall()
            for row in rows:
                neighbors.append((row["target_id"], row["target_type"], row["ref_type"]))

        return neighbors
