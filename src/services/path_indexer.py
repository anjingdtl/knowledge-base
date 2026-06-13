"""路径索引服务 — 扫描目录、检测变更、增量索引"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.models.indexing import FileFingerprint, IndexResult, ManifestDiff
from src.repositories.indexed_file_repo import IndexedFileRepository, _normalize_path
from src.services.db import Database
from src.utils.config import Config

logger = logging.getLogger(__name__)

# 支持的文档扩展名（与 file_parser.parse_file 对应）
SUPPORTED_EXTENSIONS = {
    ".pdf", ".pptx", ".ppt", ".docx", ".doc",
    ".xlsx", ".xls", ".csv",
    ".txt", ".md", ".html", ".htm",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".go", ".rs",
    ".rb", ".php", ".cs", ".swift", ".kt", ".scala", ".sh", ".bat",
    ".sql", ".r", ".m",
    ".json", ".yaml", ".yml", ".xml", ".toml",
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp",
}

# 跳过的目录名
SKIP_DIRS = {
    ".git", "node_modules", "venv", "__pycache__",
    ".venv", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".eggs", "dist", "build",
    ".idea", ".vscode", ".vs",
}

# 异步阈值
ASYNC_FILE_COUNT_THRESHOLD = 200
ASYNC_TOTAL_BYTES_THRESHOLD = 100_000_000  # 100 MB


def _file_sha256(path: Path) -> str:
    """计算文件 SHA-256"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
    except OSError as e:
        logger.warning("Cannot read %s: %s", path, e)
        return ""
    return h.hexdigest()


def _is_supported(path: Path) -> bool:
    """检查文件扩展名是否受支持"""
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def _is_hidden(path: Path) -> bool:
    """检查是否为隐藏文件/目录"""
    return path.name.startswith(".")


class PathIndexService:
    """路径索引服务

    扫描文件/目录 → 检测变更 → 增量调用 parser + indexer。
    """

    def __init__(self, db=None, config=None, indexed_file_repo=None):
        self._db = db or Database
        self._config = config or Config
        self._repo: IndexedFileRepository = (
            indexed_file_repo or IndexedFileRepository(db=self._db)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_path(
        self,
        path: Path,
        recursive: bool = True,
        dry_run: bool = False,
        force: bool = False,
    ) -> IndexResult:
        """主入口 — 索引文件或目录"""
        path = Path(os.path.normcase(os.path.normpath(str(path.resolve()))))

        if path.is_file():
            return self._index_single_file(path, dry_run=dry_run, force=force)

        if path.is_dir():
            manifest = self.scan_manifest(path, recursive=recursive)
            # 检查是否需要异步处理
            if self._should_use_async(manifest):
                return self._submit_async_job(path, manifest, recursive)
            diff = self.compute_diff(manifest, path, force=force)
            return self.apply_diff(diff, dry_run=dry_run)

        logger.warning("Path does not exist or is not a file/dir: %s", path)
        return IndexResult()

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def scan_manifest(
        self, root: Path, recursive: bool = True
    ) -> list[FileFingerprint]:
        """扫描目录并构建文件指纹列表"""
        root = Path(os.path.normcase(os.path.normpath(str(root))))
        fingerprints: list[FileFingerprint] = []

        if recursive:
            walker = self._walk_recursive(root)
        else:
            walker = self._walk_flat(root)

        for file_path in walker:
            try:
                stat = file_path.stat()
            except OSError:
                continue
            # 先用 size+mtime 做快速比较
            fp = FileFingerprint(
                path=file_path,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256="",  # 延迟计算
            )
            fingerprints.append(fp)

        return fingerprints

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def compute_diff(
        self,
        manifest: list[FileFingerprint],
        root: Path,
        force: bool = False,
    ) -> ManifestDiff:
        """将扫描清单与 indexed_files 表对比"""
        diff = ManifestDiff()
        manifest_paths: set[str] = set()

        for fp in manifest:
            norm = _normalize_path(str(fp.path))
            manifest_paths.add(norm)
            existing = self._repo.get(norm)

            if existing is None:
                # 新文件 — 计算 hash
                fp.sha256 = _file_sha256(fp.path)
                diff.created.append(fp)
            elif force:
                fp.sha256 = _file_sha256(fp.path)
                diff.modified.append(fp)
            elif (
                existing["size"] == fp.size
                and existing["mtime_ns"] == fp.mtime_ns
            ):
                # size/mtime 未变 → 跳过
                fp.sha256 = existing["sha256"]
                diff.unchanged.append(fp)
            else:
                # size/mtime 变了 → 计算 hash 确认
                fp.sha256 = _file_sha256(fp.path)
                if fp.sha256 == existing["sha256"]:
                    diff.unchanged.append(fp)
                else:
                    diff.modified.append(fp)

        # 在 DB 中但不在磁盘上的 → deleted
        root_norm = _normalize_path(str(root))
        db_records = self._repo.list_by_root(root_norm)
        for rec in db_records:
            if rec["status"] == "deleted":
                continue
            if rec["path"] not in manifest_paths:
                diff.deleted.append(rec["path"])

        return diff

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply_diff(
        self, diff: ManifestDiff, dry_run: bool = False
    ) -> IndexResult:
        """应用差异：创建/更新/删除知识条目"""
        result = IndexResult()

        # --- Created ---
        for fp in diff.created:
            if dry_run:
                result.created += 1
                continue
            try:
                kid = self._ingest_file(fp.path)
                self._record_indexed(fp, kid, "indexed")
                result.created += 1
            except Exception as e:
                logger.warning("Failed to index %s: %s", fp.path, e)
                self._record_failed(fp, str(e))
                result.failed.append({"path": str(fp.path), "error": str(e)})

        # --- Modified ---
        for fp in diff.modified:
            if dry_run:
                result.updated += 1
                continue
            try:
                existing = self._repo.get(str(fp.path))
                existing_kid = existing.get("knowledge_id") if existing else None  # type: str | None
                kid = self._reingest_file(fp.path, existing_kid)
                self._record_indexed(fp, kid, "indexed")
                result.updated += 1
            except Exception as e:
                logger.warning("Failed to reindex %s: %s", fp.path, e)
                self._record_failed(fp, str(e))
                result.failed.append({"path": str(fp.path), "error": str(e)})

        # --- Unchanged ---
        result.skipped = len(diff.unchanged)

        # --- Deleted ---
        for norm_path in diff.deleted:
            if dry_run:
                result.deleted += 1
                continue
            try:
                existing = self._repo.get(norm_path)
                if existing and existing.get("knowledge_id"):
                    self._soft_delete_knowledge(existing["knowledge_id"])
                self._repo.mark_deleted(norm_path)
                result.deleted += 1
            except Exception as e:
                logger.warning("Failed to mark deleted %s: %s", norm_path, e)
                result.failed.append({"path": norm_path, "error": str(e)})

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _index_single_file(
        self, path: Path, dry_run: bool = False, force: bool = False
    ) -> IndexResult:
        """索引单个文件"""
        if not _is_supported(path):
            logger.info("Unsupported file type: %s", path.suffix)
            return IndexResult(skipped=1)

        result = IndexResult()
        try:
            stat = path.stat()
        except OSError as e:
            result.failed.append({"path": str(path), "error": str(e)})
            return result

        fp = FileFingerprint(
            path=path,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            sha256=_file_sha256(path),
        )

        norm = _normalize_path(str(path))
        existing = self._repo.get(norm)

        if existing and not force:
            if existing["sha256"] == fp.sha256:
                result.skipped = 1
                return result

        if dry_run:
            if existing:
                result.updated = 1
            else:
                result.created = 1
            return result

        try:
            if existing and existing.get("knowledge_id"):
                kid = self._reingest_file(path, existing["knowledge_id"])
            else:
                kid = self._ingest_file(path)
            self._record_indexed(fp, kid, "indexed")
            if existing:
                result.updated = 1
            else:
                result.created = 1
        except Exception as e:
            logger.warning("Failed to index single file %s: %s", path, e)
            self._record_failed(fp, str(e))
            result.failed.append({"path": str(path), "error": str(e)})

        return result

    def _ingest_file(self, path: Path) -> str:
        """解析文件并创建知识条目，返回 knowledge_id"""
        import json
        import uuid

        from src.models.knowledge import KnowledgeItem
        from src.services.file_parser import parse_file
        from src.services.indexer import index_knowledge_item

        parsed_list = parse_file(str(path))
        if not parsed_list:
            raise ValueError(f"No content parsed from {path}")

        parsed = parsed_list[0]

        content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
        file_created_at = ""
        file_modified_at = ""
        try:
            file_created_at = datetime.fromtimestamp(
                os.path.getctime(path), tz=timezone.utc
            ).isoformat()
        except OSError:
            pass
        try:
            file_modified_at = datetime.fromtimestamp(
                os.path.getmtime(path), tz=timezone.utc
            ).isoformat()
        except OSError:
            pass

        item_id = str(uuid.uuid4())
        item = KnowledgeItem(
            id=item_id,
            title=parsed.title,
            content=parsed.content,
            source_type="file",
            source_path=str(path),
            file_type=parsed.file_type,
            file_size=os.path.getsize(path),
            content_hash=content_hash,
            file_created_at=file_created_at,
            file_modified_at=file_modified_at,
        )
        conn = self._db.get_conn()
        conn.execute(
            """INSERT INTO knowledge_items
               (id, title, content, source_type, source_path, file_type, file_size,
                content_hash, file_created_at, file_modified_at, tags, version,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.id, item.title, item.content, item.source_type,
                item.source_path, item.file_type, item.file_size,
                item.content_hash, item.file_created_at, item.file_modified_at,
                json.dumps(item.tags, ensure_ascii=False), item.version,
                item.created_at, item.updated_at,
            ),
        )
        conn.commit()

        index_knowledge_item(item)
        return item_id

    def _reingest_file(self, path: Path, existing_kid: str | None) -> str:
        """重新解析文件并更新知识条目"""
        from src.models.knowledge import KnowledgeItem
        from src.services.file_parser import parse_file
        from src.services.indexer import index_knowledge_item

        parsed_list = parse_file(str(path))
        if not parsed_list:
            raise ValueError(f"No content parsed from {path}")

        parsed = parsed_list[0]
        content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()

        if existing_kid:
            conn = self._db.get_conn()
            now = datetime.now().isoformat()
            conn.execute(
                """UPDATE knowledge_items SET
                     title=?, content=?, source_path=?, file_type=?,
                     file_size=?, content_hash=?, updated_at=?
                   WHERE id=?""",
                (
                    parsed.title, parsed.content, str(path), parsed.file_type,
                    os.path.getsize(path), content_hash, now, existing_kid,
                ),
            )
            conn.commit()
            # 重建索引
            item = KnowledgeItem(
                id=existing_kid,
                title=parsed.title,
                content=parsed.content,
                source_type="file",
                source_path=str(path),
                file_type=parsed.file_type,
                file_size=os.path.getsize(path),
                content_hash=content_hash,
            )
            index_knowledge_item(item)
            return existing_kid
        else:
            return self._ingest_file(path)

    def _soft_delete_knowledge(self, kid: str) -> None:
        """软删除知识条目"""
        conn = self._db.get_conn()
        now = datetime.now().isoformat()
        try:
            conn.execute(
                "UPDATE knowledge_items SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                (now, kid),
            )
            conn.commit()
        except Exception as e:
            logger.warning("Failed to soft-delete knowledge %s: %s", kid, e)

    def _record_indexed(
        self, fp: FileFingerprint, kid: str, status: str
    ) -> None:
        """记录成功索引"""
        self._repo.upsert({
            "path": str(fp.path),
            "knowledge_id": kid,
            "size": fp.size,
            "mtime_ns": fp.mtime_ns,
            "sha256": fp.sha256,
            "status": status,
            "last_indexed_at": datetime.now().isoformat(),
            "last_error": None,
        })

    def _record_failed(self, fp: FileFingerprint, error: str) -> None:
        """记录失败"""
        norm = _normalize_path(str(fp.path))
        existing = self._repo.get(norm)
        if existing:
            self._repo.mark_failed(norm, error)
        else:
            self._repo.upsert({
                "path": str(fp.path),
                "size": fp.size,
                "mtime_ns": fp.mtime_ns,
                "sha256": fp.sha256,
                "status": "failed",
                "last_error": error,
            })

    # ------------------------------------------------------------------
    # Walk helpers
    # ------------------------------------------------------------------

    def _walk_recursive(self, root: Path):
        """递归遍历，跳过隐藏/忽略目录"""
        for dirpath, dirnames, filenames in os.walk(root):
            # 过滤掉跳过目录
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not _is_hidden(Path(d))
            ]
            for fname in filenames:
                fp = Path(dirpath) / fname
                if _is_hidden(fp):
                    continue
                if _is_supported(fp):
                    yield fp

    def _walk_flat(self, root: Path):
        """只遍历当前目录"""
        try:
            for entry in root.iterdir():
                if entry.is_file() and not _is_hidden(entry) and _is_supported(entry):
                    yield entry
        except PermissionError:
            pass

    # ------------------------------------------------------------------
    # Async decision
    # ------------------------------------------------------------------

    def _should_use_async(self, manifest: list[FileFingerprint]) -> bool:
        """判断是否应该使用异步处理"""
        if len(manifest) > ASYNC_FILE_COUNT_THRESHOLD:
            return True
        total_bytes = sum(fp.size for fp in manifest)
        if total_bytes > ASYNC_TOTAL_BYTES_THRESHOLD:
            return True
        return False

    def _submit_async_job(
        self, root: Path, manifest: list[FileFingerprint], recursive: bool
    ) -> IndexResult:
        """提交异步扫描任务"""
        from src.services.async_task import AsyncTaskService

        file_paths = [str(fp.path) for fp in manifest]
        job_id = AsyncTaskService.create_job(
            job_type="path_scan",
            params={
                "root": str(root),
                "file_paths": file_paths,
                "recursive": recursive,
            },
        )
        logger.info(
            "Submitted async path_scan job %s for %d files", job_id, len(manifest)
        )
        return IndexResult(job_id=job_id, mode="async")
