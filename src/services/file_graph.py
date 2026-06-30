"""File-first Markdown graph service."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from src.models.knowledge import KnowledgeChunk, KnowledgeItem
from src.services.markdown_outline import MarkdownOutlineParser, OutlineBlock, PageDocument


class FileGraphService:
    """Owns the local Markdown graph and rebuilds DB/vector caches from it.

    图数据写入本地 SQLite 表，并由 SQLiteGraphBackend 动态读取。
    """

    def __init__(self, config, db, block_store, embedding=None, graph_backend=None):
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
        # 一次性批量加载所有 knowledge 的 chunks — 替代逐条 get_chunks_by_knowledge
        # 触发的 N+1（曾经 100 条 = 101 次 SQL，导出 1k 条时秒级变分钟级）。
        knowledge_ids = [it["id"] for it in items]
        chunks_by_kid = (
            self._db.get_chunks_by_knowledge_batch(knowledge_ids)
            if knowledge_ids and hasattr(self._db, "get_chunks_by_knowledge_batch")
            else {}
        )

        planned = []
        for item in items:
            page = self._page_from_item(item, chunks=chunks_by_kid.get(item["id"]))
            path = self._page_path(page.title, page.id)
            planned.append({"id": page.id, "title": page.title, "path": str(path)})
            if not dry_run:
                if backup and path.exists():
                    backup_path = root / ".kb" / "backups" / path.name
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup_path)
                path.write_text(self._parser.serialize(page), encoding="utf-8", errors="surrogatepass")
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

    def sync_page(self, path_or_id: str, content_hash: str | None = None) -> dict:
        path = self._resolve_page_path(path_or_id)
        text = path.read_text(encoding="utf-8")
        page = self._parser.parse(text)
        if not page.title:
            page.title = path.stem.split("--", 1)[0]
        changed = self._parser.ensure_ids(page)
        if changed:
            path.write_text(self._parser.serialize(page), encoding="utf-8", errors="surrogatepass")
            text = path.read_text(encoding="utf-8")

        content = self._content_from_page(page)
        now = datetime.now().isoformat()
        stat = path.stat()
        # 优先使用调用方传入的 content_hash（导入时去重用），
        # 否则从 content（_content_from_page 输出）计算，确保与知识内容一致。
        effective_hash = content_hash or hashlib.sha256(
            content.encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        item = KnowledgeItem(
            id=page.id,
            title=page.title,
            content=content,
            source_type=page.metadata.get("source-type", "file_graph"),
            source_path=str(path),
            file_type=page.metadata.get("file-type", "md"),
            file_size=stat.st_size,
            content_hash=effective_hash,
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
        try:
            from src.services.link_discovery import LinkDiscoveryService
            LinkDiscoveryService(db=self._db).discover_links(page.id)
        except Exception:
            pass

        manifest = self._read_manifest()
        manifest[page.id] = {"path": str(path), "mtime": stat.st_mtime}
        self._write_manifest(manifest)
        return {"id": page.id, "title": page.title, "path": str(path), "blocks": len(list(self._parser.iter_blocks(page.blocks)))}

    def create_page(self, title: str, blocks, tags=None, metadata=None, content_hash: str | None = None) -> str:
        now = datetime.now().isoformat()
        meta_in = metadata or {}
        page = PageDocument(
            title=title,
            tags=tags or [],
            metadata={
                "source-type": meta_in.get("source_type", "manual"),
                "source-path": meta_in.get("source_path", ""),
                # BUG-7 fix: 补 file-type 键。原实现丢弃了入参的 file_type，
                # 导致 sync_page（见上方 file_type=page.metadata.get("file-type","md")）
                # 一律 fallback 为 "md"，list_knowledge(file_type="pdf") 查不到 PDF 条目。
                # 默认值 "md" 与 sync_page 的 fallback 保持一致。
                "file-type": meta_in.get("file_type", "md"),
                "file-created-at": meta_in.get("file_created_at", now),
                "file-modified-at": meta_in.get("file_modified_at", now),
                "created-at": now,
                "updated-at": now,
            },
            blocks=self._coerce_blocks(blocks),
        )
        self._parser.ensure_ids(page)
        path = self._page_path(page.title, page.id)
        path.write_text(self._parser.serialize(page), encoding="utf-8", errors="surrogatepass")
        self.sync_page(str(path), content_hash=content_hash)
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
        path.write_text(self._parser.serialize(page), encoding="utf-8", errors="surrogatepass")
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

    # ---- 回收站管理 ----

    def list_trash(self) -> list[dict]:
        """列出回收站中的所有 MD 文件"""
        trash_dir = self.ensure_graph() / ".trash"
        items = []
        for path in sorted(trash_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = path.stat()
            # 从文件内容读取真实标题（title:: 行）
            title = self._read_title_from_file(path)
            items.append({
                "filename": path.name,
                "title": title,
                "size": stat.st_size,
                "deleted_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        return items

    def _read_title_from_file(self, path: Path) -> str:
        """从 MD 文件前几行中提取 title:: 值，回退到文件名解析"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("title::"):
                        return line[len("title::"):].strip()
                    # 遇到正文区（非 key:: 行且非空行）时停止扫描
                    if line and not line.endswith("::") and ":: " not in line and not line.startswith("#"):
                        break
        except Exception:
            pass
        # 回退：从文件名解析
        stem = path.stem
        return stem.split("--", 1)[0] if "--" in stem else stem

    def restore_page(self, trash_filename: str) -> dict:
        """从回收站恢复单条知识（完整索引重建）"""
        trash_path = self.ensure_graph() / ".trash" / trash_filename
        if not trash_path.exists():
            raise FileNotFoundError(f"回收站文件不存在: {trash_filename}")
        dest = self.graph_dir / "pages" / trash_filename
        if dest.exists():
            stem = dest.stem
            dest = dest.with_name(f"{stem}-{int(datetime.now().timestamp())}{dest.suffix}")
        shutil.move(str(trash_path), str(dest))
        result = self.sync_page(str(dest))
        return {"restored": True, **result}

    def purge_page(self, trash_filename: str) -> None:
        """永久删除回收站中的文件"""
        trash_path = self.ensure_graph() / ".trash" / trash_filename
        if trash_path.exists():
            trash_path.unlink()

    def empty_trash(self) -> int:
        """清空回收站，返回删除的文件数"""
        trash_dir = self.ensure_graph() / ".trash"
        count = 0
        for path in trash_dir.glob("*.md"):
            path.unlink()
            count += 1
        return count

    def read_page(self, page_id: str) -> PageDocument:
        path = self._resolve_page_path(page_id)
        page = self._parser.parse(path.read_text(encoding="utf-8"))
        self._parser.ensure_ids(page)
        return cast(PageDocument, page)

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
        self._db.insert_chunks(chunk_rows)
        self._db.insert_blocks(block_rows)
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

    def _page_from_item(self, item: dict, chunks: list[dict] | None = None) -> PageDocument:
        tags_raw = item.get("tags", "[]")
        if isinstance(tags_raw, str):
            try:
                tags = json.loads(tags_raw)
            except (json.JSONDecodeError, TypeError):
                tags = []
        else:
            tags = tags_raw or []
        if chunks is None:
            # 兼容老调用方：单独查询这条 item 的 chunks
            chunks = self._db.get_chunks_by_knowledge(item["id"])
        blocks = [
            OutlineBlock(id=c["id"], content=str(c.get("chunk_text") or ""))
            for c in chunks
        ]
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
                    content=str(block.get("content") or block.get("text") or ""),
                    properties=block.get("properties", {}),
                    children=self._coerce_blocks(block.get("children", [])),
                ))
            elif hasattr(block, "children") and hasattr(block, "block_type") and hasattr(block, "content"):
                # StructuredBlock — 保留层级关系转换为 OutlineBlock 树
                result.append(self._structured_to_outline(block))
            else:
                result.append(OutlineBlock(content=str(block)))
        return result

    def _structured_to_outline(self, sb) -> OutlineBlock:
        """将 StructuredBlock 转换为 OutlineBlock，递归保留 children 层级"""
        props = dict(sb.properties) if sb.properties else {}
        props["block_type"] = sb.block_type
        block = OutlineBlock(
            content=sb.content,
            properties=props,
        )
        if sb.children:
            block.children = [self._structured_to_outline(child) for child in sb.children]
        return block

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
            raw_path = entry.get("path") if isinstance(entry, dict) else entry
            if raw_path:
                path = Path(str(raw_path))
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
            path.write_text(self._parser.serialize(page), encoding="utf-8", errors="surrogatepass")
            return path
        raise FileNotFoundError(f"Page not found in graph: {path_or_id}")

    def _manifest_path(self) -> Path:
        return self.ensure_graph() / ".kb" / "manifest.json"

    def _read_manifest(self) -> dict[str, Any]:
        path = self._manifest_path()
        if not path.exists():
            return {}
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_manifest(self, pages: dict[str, Any]) -> None:
        path = self._manifest_path()
        path.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
