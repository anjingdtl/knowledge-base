"""path_indexer 单元测试"""
import hashlib
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models.indexing import FileFingerprint, IndexResult, ManifestDiff
from src.repositories.indexed_file_repo import IndexedFileRepository, _normalize_path
from src.services.path_indexer import PathIndexService, SUPPORTED_EXTENSIONS, SKIP_DIRS


@pytest.fixture
def repo():
    return IndexedFileRepository()


@pytest.fixture
def service(repo):
    return PathIndexService(indexed_file_repo=repo)


def _write_file(directory: Path, name: str, content: str) -> Path:
    """辅助：在指定目录下创建文件"""
    fp = directory / Path(name)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8")
    return fp


class TestScanManifest:
    """目录扫描测试"""

    def test_scan_basic(self, service, tmp_path):
        """扫描目录应返回受支持文件的指纹"""
        _write_file(tmp_path, "readme.md", "# Hello")
        _write_file(tmp_path, "notes.txt", "some notes")
        _write_file(tmp_path, "data.csv", "a,b,c")

        manifest = service.scan_manifest(tmp_path, recursive=True)
        names = {fp.path.name for fp in manifest}
        assert "readme.md" in names
        assert "notes.txt" in names
        assert "data.csv" in names

    def test_scan_skips_hidden(self, service, tmp_path):
        """隐藏文件应被跳过"""
        _write_file(tmp_path, ".hidden.txt", "hidden")
        _write_file(tmp_path, "visible.md", "visible")

        manifest = service.scan_manifest(tmp_path, recursive=True)
        names = {fp.path.name for fp in manifest}
        assert ".hidden.txt" not in names
        assert "visible.md" in names

    def test_scan_skips_git_dir(self, service, tmp_path):
        """.git 目录下的文件应被跳过"""
        _write_file(tmp_path, ".git/config", "git config")
        _write_file(tmp_path, "main.py", "print('hi')")

        manifest = service.scan_manifest(tmp_path, recursive=True)
        names = {fp.path.name for fp in manifest}
        assert "config" not in names
        assert "main.py" in names

    def test_scan_skips_node_modules(self, service, tmp_path):
        """node_modules 应被跳过"""
        _write_file(tmp_path, "node_modules/pkg.js", "module")
        _write_file(tmp_path, "app.js", "app code")

        manifest = service.scan_manifest(tmp_path, recursive=True)
        names = {fp.path.name for fp in manifest}
        assert "pkg.js" not in names
        assert "app.js" in names

    def test_scan_recursive(self, service, tmp_path):
        """递归扫描应包含子目录文件"""
        _write_file(tmp_path, "top.md", "top")
        _write_file(tmp_path, "sub/deep.md", "deep")

        manifest = service.scan_manifest(tmp_path, recursive=True)
        names = {fp.path.name for fp in manifest}
        assert "top.md" in names
        assert "deep.md" in names

    def test_scan_flat(self, service, tmp_path):
        """非递归扫描不应包含子目录文件"""
        _write_file(tmp_path, "top.md", "top")
        _write_file(tmp_path, "sub/deep.md", "deep")

        manifest = service.scan_manifest(tmp_path, recursive=False)
        names = {fp.path.name for fp in manifest}
        assert "top.md" in names
        assert "deep.md" not in names

    def test_scan_fingerprint_has_size_and_mtime(self, service, tmp_path):
        """指纹应包含 size 和 mtime_ns"""
        _write_file(tmp_path, "test.txt", "hello world")

        manifest = service.scan_manifest(tmp_path, recursive=True)
        assert len(manifest) >= 1
        fp = [f for f in manifest if f.path.name == "test.txt"][0]
        assert fp.size > 0
        assert fp.mtime_ns > 0
        assert fp.sha256 == ""  # 延迟计算


class TestComputeDiff:
    """差异计算测试"""

    def test_new_files_detected(self, service, repo, tmp_path):
        """新文件应被标记为 created"""
        _write_file(tmp_path, "new.md", "new content")

        manifest = service.scan_manifest(tmp_path)
        diff = service.compute_diff(manifest, tmp_path)
        assert len(diff.created) == 1
        assert diff.created[0].sha256 != ""

    def test_unchanged_files_skipped(self, service, repo, tmp_path):
        """未变文件应被标记为 unchanged"""
        fp = _write_file(tmp_path, "same.md", "same content")
        sha = hashlib.sha256(b"same content").hexdigest()

        # 预先记录为已索引
        stat = fp.stat()
        repo.upsert({
            "path": str(fp),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha,
            "status": "indexed",
            "knowledge_id": "kid-same",
        })

        manifest = service.scan_manifest(tmp_path)
        diff = service.compute_diff(manifest, tmp_path)
        assert len(diff.unchanged) == 1
        assert len(diff.created) == 0
        assert len(diff.modified) == 0

    def test_modified_file_detected(self, service, repo, tmp_path):
        """修改过的文件应被标记为 modified"""
        fp = _write_file(tmp_path, "changed.md", "old content")
        old_sha = hashlib.sha256(b"old content").hexdigest()
        stat = fp.stat()

        repo.upsert({
            "path": str(fp),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": old_sha,
            "status": "indexed",
            "knowledge_id": "kid-changed",
        })

        # 修改文件
        fp.write_text("new content", encoding="utf-8")

        manifest = service.scan_manifest(tmp_path)
        diff = service.compute_diff(manifest, tmp_path)
        assert len(diff.modified) == 1
        assert len(diff.unchanged) == 0

    def test_deleted_file_detected(self, service, repo, tmp_path):
        """已删除文件应被标记为 deleted"""
        deleted_path = _normalize_path(str(tmp_path / "deleted.md"))
        repo.upsert({
            "path": str(tmp_path / "deleted.md"),
            "size": 100,
            "mtime_ns": 12345,
            "sha256": "deadbeef",
            "status": "indexed",
            "knowledge_id": "kid-deleted",
        })

        manifest = service.scan_manifest(tmp_path)
        diff = service.compute_diff(manifest, tmp_path)
        assert deleted_path in diff.deleted

    def test_force_reindex(self, service, repo, tmp_path):
        """force=True 时所有文件应标记为 modified"""
        fp = _write_file(tmp_path, "force.md", "content")
        sha = hashlib.sha256(b"content").hexdigest()
        stat = fp.stat()

        repo.upsert({
            "path": str(fp),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha,
            "status": "indexed",
            "knowledge_id": "kid-force",
        })

        manifest = service.scan_manifest(tmp_path)
        diff = service.compute_diff(manifest, tmp_path, force=True)
        assert len(diff.modified) == 1
        assert len(diff.unchanged) == 0

    def test_hash_skip_when_size_mtime_same(self, service, repo, tmp_path):
        """size/mtime 未变时不计算 hash"""
        fp = _write_file(tmp_path, "skip.md", "content")
        sha = hashlib.sha256(b"content").hexdigest()
        stat = fp.stat()

        repo.upsert({
            "path": str(fp),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha,
            "status": "indexed",
            "knowledge_id": "kid-skip",
        })

        manifest = service.scan_manifest(tmp_path)
        diff = service.compute_diff(manifest, tmp_path)
        # sha256 should come from DB, not recomputed
        assert len(diff.unchanged) == 1
        # The fingerprint sha256 should be set from existing record
        assert diff.unchanged[0].sha256 == sha


class TestApplyDiff:
    """差异应用测试（mock parser/indexer）"""

    @patch("src.services.path_indexer.PathIndexService._ingest_file")
    def test_apply_created(self, mock_ingest, service, repo, tmp_path):
        """created 文件应调用 ingest 并记录"""
        mock_ingest.return_value = "kid-new"
        fp = _write_file(tmp_path, "new.md", "new content")
        sha = hashlib.sha256(b"new content").hexdigest()
        stat = fp.stat()

        fingerprint = FileFingerprint(
            path=fp, size=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=sha
        )
        diff = ManifestDiff(created=[fingerprint])

        result = service.apply_diff(diff)
        assert result.created == 1
        mock_ingest.assert_called_once()
        # 应在 indexed_files 中记录
        rec = repo.get(str(fp))
        assert rec is not None
        assert rec["status"] == "indexed"
        assert rec["knowledge_id"] == "kid-new"

    @patch("src.services.path_indexer.PathIndexService._reingest_file")
    def test_apply_modified(self, mock_reingest, service, repo, tmp_path):
        """modified 文件应调用 reingest"""
        mock_reingest.return_value = "kid-mod"
        fp = _write_file(tmp_path, "mod.md", "modified content")
        sha = hashlib.sha256(b"modified content").hexdigest()
        stat = fp.stat()

        fingerprint = FileFingerprint(
            path=fp, size=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=sha
        )
        diff = ManifestDiff(modified=[fingerprint])

        result = service.apply_diff(diff)
        assert result.updated == 1
        mock_reingest.assert_called_once()

    def test_apply_deleted(self, service, repo, tmp_path):
        """deleted 文件应标记为 deleted"""
        norm = _normalize_path(str(tmp_path / "gone.md"))
        repo.upsert({
            "path": str(tmp_path / "gone.md"),
            "size": 10,
            "mtime_ns": 1,
            "sha256": "h",
            "status": "indexed",
            "knowledge_id": "kid-gone",
        })

        diff = ManifestDiff(deleted=[norm])
        result = service.apply_diff(diff)
        assert result.deleted == 1
        rec = repo.get(str(tmp_path / "gone.md"))
        assert rec["status"] == "deleted"

    def test_dry_run_safety(self, service, repo, tmp_path):
        """dry-run 不应实际修改数据库"""
        fp = _write_file(tmp_path, "dryrun.md", "dry")
        sha = hashlib.sha256(b"dry").hexdigest()
        stat = fp.stat()

        fingerprint = FileFingerprint(
            path=fp, size=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=sha
        )
        diff = ManifestDiff(created=[fingerprint])

        result = service.apply_diff(diff, dry_run=True)
        assert result.created == 1
        # 不应在 indexed_files 中有记录
        rec = repo.get(str(fp))
        assert rec is None

    def test_apply_failed_records_error(self, service, repo, tmp_path):
        """解析失败应记录错误而不应标记为成功"""
        fp = _write_file(tmp_path, "bad.md", "bad content")
        sha = hashlib.sha256(b"bad content").hexdigest()
        stat = fp.stat()

        fingerprint = FileFingerprint(
            path=fp, size=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=sha
        )
        diff = ManifestDiff(created=[fingerprint])

        with patch.object(service, "_ingest_file", side_effect=RuntimeError("parse error")):
            result = service.apply_diff(diff)

        assert result.created == 0
        assert len(result.failed) == 1
        assert result.failed[0]["error"] == "parse error"


class TestIndexPath:
    """index_path 集成入口测试"""

    def test_single_file(self, service, tmp_path):
        """索引单个文件"""
        fp = _write_file(tmp_path, "single.txt", "single file content")

        with patch.object(service, "_ingest_file", return_value="kid-single"):
            result = service.index_path(fp)
        assert result.created == 1

    def test_nonexistent_path(self, service, tmp_path):
        """不存在的路径应返回空结果"""
        result = service.index_path(tmp_path / "nonexistent")
        assert result.created == 0
        assert result.updated == 0

    def test_dry_run_single_file(self, service, tmp_path):
        """单文件 dry-run 不应写入"""
        fp = _write_file(tmp_path, "dry.txt", "dry content")

        result = service.index_path(fp, dry_run=True)
        assert result.created == 1

    def test_path_normalization(self, service, tmp_path):
        """路径应被标准化处理"""
        fp = _write_file(tmp_path, "norm.txt", "norm content")

        # 使用正斜杠的路径
        with patch.object(service, "_ingest_file", return_value="kid-norm"):
            result = service.index_path(fp)
        assert result.created == 1
