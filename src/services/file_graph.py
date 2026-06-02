"""File-first Markdown graph service."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from src.models.knowledge import KnowledgeItem, KnowledgeChunk
from src.services.markdown_outline import MarkdownOutlineParser, OutlineBlock, PageDocument


class FileGraphService:
    """Owns the local Markdown graph and rebuilds DB/vector caches from it."""

    def __init__(self, config, db, block_store, embedding=None):
        self._config = config
        self._db = db
        self._block_store = block_store
        self._embedding = embedding
        self._parser = MarkdownOutlineParser()

    def ensure_graph(self) -> Path:
        root = self.graph_dir
        for name in ("pages", "journals", ".trash", ".kb"):
            (root / name).mkdir(parents=True, exist_ok=True)
        return root

    @property
    def graph_dir(self) -> Path:
        configured = self._config.get("storage.graph_dir", "graph")
        path = Path(configured)
        if not path.is_absolute():
            path = self._config.get_data_dir() / path
        return path

    def export_db_to_graph(self, dry_run: bool = True, backup: bool = True) -> dict:
        root = self.ensure_graph()
        items = self._db.list_knowledge(limit=100000)
        planned = []
        for item in items:
            page = self._page_from_item(item)
            path = self._page_path(page.title, page.id)
            planned.append({"id": page.id, "title": page.title, "path": str(path)})
            if not dry_run:
                if backup and path.exists():
                    backup_path = root / ".kb" / "backups" / path.name
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup_path)
                path.write_text(self._parser.serialize(page), encoding="utf-8")
        if not dry_run:
            self._write_manifest({p["id"]: p["path"] for p in planned})
        return {"dry_run": dry_run, "count": len(planned), "pages": planned}

    def sync_all(self, force_reindex: bool = False) -> dict:
        root = self.ensure_graph()
        manifest = self._read_manifest()
        files = sorted((root / "pages").glob("*.md")) + sorted((root / "journals").glob("*.md"))
        if not files and not manifest and self._db.count_knowledge() > 0:
            return {
                "synced": 0,
                "deleted": 0,
                "skipped": True,
                "message": "graph is empty; run export_db_to_graph before enabling file-first sync",
            }

        synced = []
        current_paths = {str(p.resolve()) for p in files}
        for path in files:
            synced.append(self.sync_page(str(path)))

        deleted = 0
        for page_id, entry in list(manifest.items()):
            old_path = entry.get("path") if isinstance(entry, dict) else entry
            if old_path and str(Path(old_path).resolve()) not in current_paths:
                self._delete_cache(page_id)
                deleted += 1

        self._write_manifest({item["id"]: item["path"] for item in synced if item.get("id") and item.get("path")})
        return {"synced": len(synced), "deleted": deleted, "pages": synced}

    def sync_page(self, path_or_id: str) -> dict:
        path = self._resolve_page_path(path_or_id)
        text = path.read_text(encoding="utf-8")
        page = self._parser.parse(text)
        if not page.title:
            page.title = path.stem.split("--", 1)[0]
        changed = self._parser.ensure_ids(page)
        if changed:
            path.write_text(self._parser.serialize(page), encoding="utf-8")
            text = path.read_text(encoding="utf-8")

        content = self._content_from_page(page)
        now = datetime.now().isoformat()
        stat = path.stat()
        item = KnowledgeItem(
            id=page.id,
            title=page.title,
            content=content,
            source_type=page.metadata.get("source-type", "file_graph"),
            source_path=str(path),
            file_type=page.metadata.get("file-type", "md"),
            file_size=stat.st_size,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            file_created_at=page.metadata.get("created-at", now),
            file_modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            tags=page.tags,
            created_at=page.metadata.get("created-at", now),
            updated_at=page.metadata.get("updated-at", now),
        )

        existing = self._db.get_knowledge(page.id)
        if existing:
            self._db.update_knowledge(
                page.id,
                title=item.title,
                content=item.content,
                source_type=item.source_type,
                source_path=item.source_path,
                file_type=item.file_type,
                file_size=item.file_size,
                content_hash=item.content_hash,
                file_created_at=item.file_created_at,
                file_modified_at=item.file_modified_at,
                tags=json.dumps(item.tags, ensure_ascii=False),
            )
        else:
            self._db.insert_knowledge(item.to_row())

        self._rebuild_page_cache(page, item)
        manifest = self._read_manifest()
        manifest[page.id] = {"path": str(path), "mtime": stat.st_mtime}
        self._write_manifest(manifest)
        return {"id": page.id, "title": page.title, "path": str(path), "blocks": len(list(self._parser.iter_blocks(page.blocks)))}

    def create_page(self, title: str, blocks, tags=None, metadata=None) -> str:
        now = datetime.now().isoformat()
        page = PageDocument(
            title=title,
            tags=tags or [],
            metadata={
                "source-type": (metadata or {}).get("source_type", "manual"),
                "source-path": (metadata or {}).get("source_path", ""),
                "created-at": now,
                "updated-at": now,
            },
            blocks=self._coerce_blocks(blocks),
        )
        self._parser.ensure_ids(page)
        path = self._page_path(page.title, page.id)
        path.write_text(self._parser.serialize(page), encoding="utf-8")
        self.sync_page(str(path))
        return page.id

    def update_page(self, page_id: str, blocks, metadata=None) -> None:
        path = self._resolve_page_path(page_id)
        page = self._parser.parse(path.read_text(encoding="utf-8"))
        if metadata:
            if "title" in metadata and metadata["title"]:
                page.title = metadata["title"]
            if "tags" in metadata and metadata["tags"] is not None:
                page.tags = metadata["tags"]
            for key, value in metadata.items():
                if key not in {"title", "tags"}:
                    page.metadata[key.replace("_", "-")] = value
        page.metadata["updated-at"] = datetime.now().isoformat()
        page.blocks = self._coerce_blocks(blocks)
        self._parser.ensure_ids(page)
        path.write_text(self._parser.serialize(page), encoding="utf-8")
        self.sync_page(str(path))

    def delete_page(self, page_id: str, move_to_trash: bool = True) -> None:
        path = self._resolve_page_path(page_id)
        if move_to_trash and path.exists():
            trash = self.ensure_graph() / ".trash" / path.name
            if trash.exists():
                trash = trash.with_name(f"{trash.stem}-{int(datetime.now().timestamp())}{trash.suffix}")
            shutil.move(str(path), str(trash))
        elif path.exists():
            path.unlink()
        self._delete_cache(page_id)
        manifest = self._read_manifest()
        manifest.pop(page_id, None)
        self._write_manifest(manifest)

    def read_page(self, page_id: str) -> PageDocument:
        path = self._resolve_page_path(page_id)
        page = self._parser.parse(path.read_text(encoding="utf-8"))
        self._parser.ensure_ids(page)
        return page

    def _rebuild_page_cache(self, page: PageDocument, item: KnowledgeItem) -> None:
        from src.services.vectorstore import VectorStore

        try:
            self._block_store.delete_by_page(page.id)
        except Exception:
            pass
        try:
            VectorStore().delete_by_knowledge(page.id)
        except Exception:
            pass
        self._db.delete_blocks_by_page(page.id)
        self._db.delete_chunks_fts(page.id)
        self._db.delete_chunks(page.id)

        now = datetime.now().isoformat()
        block_rows = []
        chunk_rows = []
        for order, (block, parent_id, depth, sibling_idx) in enumerate(self._parser.flatten_blocks(page.blocks)):
            props = {
                **(block.properties or {}),
                "knowledge_id": page.id,
                "chunk_index": order,
                "depth": depth,
            }
            block_rows.append({
                "id": block.id,
                "parent_id": parent_id,
                "page_id": page.id,
                "content": block.content,
                "block_type": "text",
                "properties": json.dumps(props, ensure_ascii=False),
                "order_idx": order,
                "created_at": now,
                "updated_at": now,
            })
            chunk = KnowledgeChunk(id=block.id, knowledge_id=page.id, chunk_index=order, chunk_text=block.content)
            chunk_rows.append(chunk.to_row())

        if not block_rows:
            return
        self._db.insert_blocks(block_rows)
        self._db.insert_chunks(chunk_rows)
        self._db.insert_blocks_fts(block_rows)
        self._db.insert_chunks_fts([
            {"id": row["id"], "knowledge_id": page.id, "chunk_text": row["chunk_text"]}
            for row in chunk_rows
        ])

        texts = [row["content"] for row in block_rows]
        embeddings = []
        try:
            if self._embedding:
                embeddings = self._embedding.embed_batch_with_cache(texts)
        except Exception:
            embeddings = []
        for row, emb in zip(block_rows, embeddings):
            if emb:
                try:
                    self._block_store.add_block_embedding(row["id"], emb)
                except Exception:
                    pass

    def _delete_cache(self, page_id: str) -> None:
        try:
            self._block_store.delete_by_page(page_id)
        except Exception:
            pass
        self._db.delete_knowledge(page_id)

    def _content_from_page(self, page: PageDocument) -> str:
        lines = []

        def walk(blocks: list[OutlineBlock], depth: int):
            for block in blocks:
                lines.append("  " * depth + block.content)
                walk(block.children, depth + 1)

        walk(page.blocks, 0)
        return "\n".join(lines)

    def _page_from_item(self, item: dict) -> PageDocument:
        tags_raw = item.get("tags", "[]")
        if isinstance(tags_raw, str):
            try:
                tags = json.loads(tags_raw)
            except (json.JSONDecodeError, TypeError):
                tags = []
        else:
            tags = tags_raw or []
        chunks = self._db.get_chunks_by_knowledge(item["id"])
        blocks = [OutlineBlock(id=c["id"], content=c.get("chunk_text", "")) for c in chunks]
        if not blocks:
            blocks = [OutlineBlock(content=line.strip()) for line in item.get("content", "").splitlines() if line.strip()]
        return PageDocument(
            id=item["id"],
            title=item.get("title", ""),
            tags=tags,
            metadata={
                "source-type": item.get("source_type", "manual"),
                "source-path": item.get("source_path", ""),
                "file-type": item.get("file_type", "txt"),
                "created-at": item.get("created_at", ""),
                "updated-at": item.get("updated_at", ""),
            },
            blocks=blocks,
        )

    def _coerce_blocks(self, blocks) -> list[OutlineBlock]:
        if isinstance(blocks, str):
            return [OutlineBlock(content=line.strip()) for line in blocks.splitlines() if line.strip()]
        result = []
        for block in blocks or []:
            if isinstance(block, OutlineBlock):
                result.append(block)
            elif isinstance(block, dict):
                result.append(OutlineBlock(
                    id=block.get("id", ""),
                    content=block.get("content", block.get("text", "")),
                    properties=block.get("properties", {}),
                    children=self._coerce_blocks(block.get("children", [])),
                ))
            else:
                result.append(OutlineBlock(content=str(block)))
        return result

    def _page_path(self, title: str, page_id: str) -> Path:
        self.ensure_graph()
        safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", title, flags=re.UNICODE).strip("-") or "untitled"
        return self.graph_dir / "pages" / f"{safe[:60]}--{page_id[:8]}.md"

    def _resolve_page_path(self, path_or_id: str) -> Path:
        candidate = Path(path_or_id)
        if candidate.exists():
            return candidate
        manifest = self._read_manifest()
        entry = manifest.get(path_or_id)
        if entry:
            path = Path(entry.get("path") if isinstance(entry, dict) else entry)
            if path.exists():
                return path
        for path in list((self.graph_dir / "pages").glob("*.md")) + list((self.graph_dir / "journals").glob("*.md")):
            try:
                page = self._parser.parse(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if page.id == path_or_id:
                return path
        existing = self._db.get_knowledge(path_or_id)
        if existing:
            page = self._page_from_item(existing)
            self._parser.ensure_ids(page)
            path = self._page_path(page.title, page.id)
            path.write_text(self._parser.serialize(page), encoding="utf-8")
            return path
        raise FileNotFoundError(f"Page not found in graph: {path_or_id}")

    def _manifest_path(self) -> Path:
        return self.ensure_graph() / ".kb" / "manifest.json"

    def _read_manifest(self) -> dict[str, Any]:
        path = self._manifest_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_manifest(self, pages: dict[str, Any]) -> None:
        path = self._manifest_path()
        path.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
