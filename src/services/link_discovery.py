"""Automatic discovery of Logseq-style wiki links in stored blocks."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from src.models.block import EntityRef
from src.repositories.entity_ref_repo import EntityRefRepository
from src.services.db import Database


@dataclass
class DiscoveredLink:
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    ref_type: str = "link"


class LinkDiscoveryService:
    _WIKI_LINK_RE = re.compile(r"\[\[([^]\n#]+)(?:#([^]\n]+))?\]\]")

    def __init__(self, db=None, repo: EntityRefRepository | None = None):
        self._db = db or Database
        self._repo = repo or EntityRefRepository(db=self._db)

    def scan_content(self, content: str, source_id: str, source_type: str = "block") -> list[DiscoveredLink]:
        links: list[DiscoveredLink] = []
        seen = set()
        for match in self._WIKI_LINK_RE.finditer(content or ""):
            title = match.group(1).strip()
            block_hint = (match.group(2) or "").strip()
            target = self._resolve_target(title, block_hint)
            if not target:
                continue
            key = (source_type, source_id, target["target_type"], target["target_id"])
            if key in seen:
                continue
            seen.add(key)
            links.append(DiscoveredLink(
                source_type=source_type,
                source_id=source_id,
                target_type=target["target_type"],
                target_id=target["target_id"],
            ))
        return links

    def discover_links(self, knowledge_id: str) -> int:
        rows = self._db.get_conn().execute(
            "SELECT id, content FROM blocks WHERE page_id = ? ORDER BY order_idx ASC",
            (knowledge_id,),
        ).fetchall()
        created = 0
        for row in rows:
            self._repo.delete_auto_discovered_for_source("block", row["id"])
            for link in self.scan_content(row["content"], row["id"], "block"):
                self._repo.upsert(EntityRef(
                    id=str(uuid.uuid4()),
                    source_type=link.source_type,
                    source_id=link.source_id,
                    target_type=link.target_type,
                    target_id=link.target_id,
                    ref_type=link.ref_type,
                    auto_discovered=1,
                ))
                created += 1
        return created

    def discover_all(self) -> dict:
        rows = self._db.get_conn().execute("SELECT id FROM knowledge_items").fetchall()
        total = 0
        for row in rows:
            total += self.discover_links(row["id"])
        return {"processed": len(rows), "links_created": total}

    def _resolve_target(self, title: str, block_hint: str = "") -> dict | None:
        page = self._db.get_conn().execute(
            "SELECT id FROM knowledge_items WHERE title = ? LIMIT 1",
            (title,),
        ).fetchone()
        if not page:
            return None
        if block_hint:
            block = self._db.get_conn().execute(
                "SELECT id FROM blocks WHERE page_id = ? AND content LIKE ? ORDER BY order_idx ASC LIMIT 1",
                (page["id"], f"%{block_hint}%"),
            ).fetchone()
            if block:
                return {"target_type": "block", "target_id": block["id"]}
        return {"target_type": "knowledge", "target_id": page["id"]}
