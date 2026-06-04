"""知识图谱仓库 — knowledge_graphs / nodes / relations"""
import uuid
from datetime import datetime
from typing import Optional


class GraphRepository:
    """知识图谱、节点、关系边"""

    def __init__(self, db=None):
        from src.services.db import Database
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    # ---- Graphs ----

    def insert_graph(self, name: str, description: str = "", source_type: str = "manual") -> str:
        graph_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        self._conn().execute(
            "INSERT INTO knowledge_graphs (id, name, description, source_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (graph_id, name, description, source_type, now, now),
        )
        self._conn().commit()
        return graph_id

    def get_graph(self, graph_id: str) -> Optional[dict]:
        row = self._conn().execute("SELECT * FROM knowledge_graphs WHERE id = ?", (graph_id,)).fetchone()
        return dict(row) if row else None

    def list_graphs(self, source_type: str | None = None) -> list[dict]:
        if source_type:
            rows = self._conn().execute(
                "SELECT * FROM knowledge_graphs WHERE source_type = ? ORDER BY updated_at DESC",
                (source_type,),
            ).fetchall()
        else:
            rows = self._conn().execute("SELECT * FROM knowledge_graphs ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def update_graph(self, graph_id: str, **fields):
        allowed = {"name", "description"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), graph_id]
        self._conn().execute(
            f"UPDATE knowledge_graphs SET {sets}, updated_at = ? WHERE id = ?",
            values,
        )
        self._conn().commit()

    def delete_graph(self, graph_id: str):
        self._conn().execute("DELETE FROM knowledge_graphs WHERE id = ?", (graph_id,))
        self._conn().commit()

    def get_graph_for_knowledge(self, knowledge_id: str) -> list[dict]:
        rows = self._conn().execute(
            """SELECT g.* FROM knowledge_graphs g
               JOIN knowledge_graph_nodes n ON n.graph_id = g.id
               WHERE n.knowledge_id = ?""",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Nodes ----

    def insert_nodes(self, graph_id: str, knowledge_ids: list[str]):
        conn = self._conn()
        for kid in knowledge_ids:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_graph_nodes (id, graph_id, knowledge_id, x, y, is_pinned) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), graph_id, kid, 0, 0, 0),
            )
        conn.commit()

    def get_nodes(self, graph_id: str) -> list[dict]:
        rows = self._conn().execute(
            """SELECT n.*, ki.title as knowledge_title, ki.file_type, ki.tags
               FROM knowledge_graph_nodes n
               JOIN knowledge_items ki ON ki.id = n.knowledge_id WHERE n.graph_id = ?""",
            (graph_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_node_position(self, node_id: str, x: float, y: float):
        self._conn().execute(
            "UPDATE knowledge_graph_nodes SET x = ?, y = ? WHERE id = ?", (x, y, node_id),
        )
        self._conn().commit()

    def delete_nodes(self, graph_id: str, knowledge_ids: list[str]):
        if not knowledge_ids:
            return
        placeholders = ",".join("?" for _ in knowledge_ids)
        self._conn().execute(
            f"DELETE FROM knowledge_graph_nodes WHERE graph_id = ? AND knowledge_id IN ({placeholders})",
            (graph_id, *knowledge_ids),
        )
        self._conn().commit()

    # ---- Relations ----

    def insert_relations(self, graph_id: str, relations: list[dict]):
        conn = self._conn()
        rows = [
            (str(uuid.uuid4()), graph_id,
             rel["source_knowledge_id"], rel["target_knowledge_id"],
             rel.get("relation_type", "related"), rel.get("description", ""), rel.get("weight", 1.0))
            for rel in relations
        ]
        conn.executemany(
            """INSERT OR REPLACE INTO knowledge_graph_relations
               (id, graph_id, source_knowledge_id, target_knowledge_id, relation_type, description, weight)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

    def get_relations(self, graph_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM knowledge_graph_relations WHERE graph_id = ?", (graph_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_relations(self, graph_id: str):
        self._conn().execute(
            "DELETE FROM knowledge_graph_relations WHERE graph_id = ?", (graph_id,),
        )
        self._conn().commit()
