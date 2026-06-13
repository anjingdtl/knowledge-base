"""分类仓库 — categories / knowledge_categories"""
from datetime import datetime
from typing import Callable

get_all_codes: Callable[[bool], set[str]] | None
try:
    from src.data.classification_schema import get_all_codes as _get_all_codes
    get_all_codes = _get_all_codes
except ImportError:
    get_all_codes = None


class CategoryRepository:
    """知识分类管理"""

    def __init__(self, db=None):
        from src.services.db import Database
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    def insert_category(self, cat_id: str, name: str, description: str = "",
                        parent_id: str | None = None) -> str:
        conn = self._conn()
        conn.execute(
            "INSERT INTO categories (id, name, description, parent_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (cat_id, name, description, parent_id, datetime.now().isoformat()),
        )
        conn.commit()
        return cat_id

    def get_all_categories(self) -> list[dict]:
        rows = self._conn().execute("SELECT * FROM categories ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def delete_category(self, cat_id: str):
        conn = self._conn()
        # Find the parent of the category being deleted (grandparent of children)
        row = conn.execute(
            "SELECT parent_id FROM categories WHERE id = ?", (cat_id,)
        ).fetchone()
        grandparent_id = row["parent_id"] if row else None
        # Reparent direct children to the grandparent (or NULL if no grandparent)
        conn.execute(
            "UPDATE categories SET parent_id = ? WHERE parent_id = ?",
            (grandparent_id, cat_id),
        )
        # Remove junction entries and the category itself
        conn.execute("DELETE FROM knowledge_categories WHERE category_id = ?", (cat_id,))
        conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        conn.commit()

    def clear_categories(self, keep_dynamic=False):
        conn = self._conn()
        if keep_dynamic:
            if get_all_codes is None:
                raise RuntimeError(
                    "classification_schema 模块不可用，无法按动态分类清理；"
                    "请安装 src.data.classification_schema 后重试"
                )
            codes = get_all_codes()
            if not codes:
                conn.commit()
                return
            conditions = " OR ".join("c.name LIKE ?" for _ in codes)
            like_params = [f"{code} %" for code in codes]
            eq_conditions = " OR ".join("c.name = ?" for _ in codes)
            eq_params = list(codes)
            conn.execute(
                f"DELETE FROM knowledge_categories WHERE category_id IN "
                f"(SELECT id FROM categories c WHERE {conditions} OR {eq_conditions})",
                like_params + eq_params,
            )
            conn.execute(
                f"DELETE FROM categories WHERE {conditions} OR {eq_conditions}",
                like_params + eq_params,
            )
        else:
            conn.execute("DELETE FROM knowledge_categories")
            conn.execute("DELETE FROM categories")
        conn.commit()

    def assign_category(self, knowledge_id: str, category_id: str):
        conn = self._conn()
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_categories (knowledge_id, category_id) VALUES (?, ?)",
            (knowledge_id, category_id),
        )
        conn.commit()

    def get_knowledge_by_category(self, category_id: str) -> list[dict]:
        rows = self._conn().execute(
            """SELECT ki.* FROM knowledge_items ki
               JOIN knowledge_categories kc ON kc.knowledge_id = ki.id
               WHERE kc.category_id = ? ORDER BY ki.title""",
            (category_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_categories_for_knowledge(self, knowledge_id: str) -> list[dict]:
        rows = self._conn().execute(
            """SELECT c.* FROM categories c
               JOIN knowledge_categories kc ON kc.category_id = c.id
               WHERE kc.knowledge_id = ?""",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]
