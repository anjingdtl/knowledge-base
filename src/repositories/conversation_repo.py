"""对话仓库 — conversations / chat_messages"""
import json
from typing import Optional


def _require_keys(data: dict, keys: tuple[str, ...]):
    """Validate that all required keys exist in the dict, raise ValueError with a clear message if not."""
    missing = [k for k in keys if k not in data]
    if missing:
        raise ValueError(f"缺少必填字段: {', '.join(missing)}")


class ConversationRepository:
    """对话历史和聊天消息"""

    def __init__(self, db=None):
        from src.services.db import Database
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    # ---- Conversations ----

    def insert_conversation(self, conv: dict) -> str:
        _require_keys(conv, ("id", "title", "created_at"))
        conn = self._conn()
        conn.execute(
            "INSERT INTO conversations (id, title, created_at) VALUES (:id, :title, :created_at)",
            conv,
        )
        conn.commit()
        return conv["id"]

    def list_conversations(self, limit: int = 50) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM conversations ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conv_id: str):
        conn = self._conn()
        conn.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        conn.commit()

    # ---- Messages ----

    def insert_message(self, msg: dict) -> str:
        _require_keys(msg, ("id", "conversation_id", "role", "content", "created_at"))
        msg = {**msg}
        msg.setdefault("source_graph", json.dumps({"nodes": [], "edges": []}, ensure_ascii=False))
        conn = self._conn()
        conn.execute(
            """INSERT INTO chat_messages (id, conversation_id, role, content, sources, source_graph, created_at)
               VALUES (:id, :conversation_id, :role, :content, :sources, :source_graph, :created_at)""",
            msg,
        )
        conn.commit()
        return msg["id"]

    def get_messages(self, conversation_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM chat_messages WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]
