"""MigrationService 测试(spec §10)。"""
from pathlib import Path

import pytest

from src.services.db import Database
from src.services.migrator import MigrationService


def _insert_file_knowledge(kid, source_path, content="doc body"):
    Database.insert_knowledge({
        "id": kid, "title": "T", "content": content,
        "source_type": "file", "source_path": source_path, "file_type": "md",
        "file_size": len(content), "content_hash": "h", "file_created_at": "",
        "file_modified_at": "", "tags": "[]", "version": 1,
        "created_at": "2026-07-01T00:00:00", "updated_at": "2026-07-01T00:00:00",
    })


@pytest.fixture
def patch_migrator_config(tmp_path, monkeypatch):
    """patch migrator 看到的配置目录(隔离 conftest 的 storage.data_dir=tmp_path)。"""
    def _cfg(raw_dir=None, data_dir=None, mode="legacy"):
        monkeypatch.setattr(
            "src.services.migrator.Config.get",
            lambda key, default=None: {
                "knowledge_workflow.raw_dir": str(raw_dir or tmp_path / "raw"),
                "knowledge_workflow.mode": mode,
                "storage.data_dir": str(data_dir or tmp_path / "data"),
            }.get(key, default),
        )
    return _cfg


def test_plan_scans_knowledge_without_writing(tmp_path, patch_migrator_config):
    """plan() 只扫描,不写盘。"""
    raw_file = tmp_path / "original.md"
    raw_file.write_text("# Doc\ndoc body", encoding="utf-8")
    _insert_file_knowledge("k1", str(raw_file))
    patch_migrator_config()

    svc = MigrationService(project_dir=tmp_path)
    plan = svc.plan()
    assert plan["knowledge_count"] >= 1
    assert plan["exportable"] >= 1
    assert not (tmp_path / "raw").exists()  # plan 不写盘


def test_apply_exports_sources_and_backs_up_data(tmp_path, patch_migrator_config):
    """apply() 备份 data/ + 导出源到 raw/。"""
    raw_file = tmp_path / "original.md"
    raw_file.write_text("# Doc\ndoc body", encoding="utf-8")
    _insert_file_knowledge("k1", str(raw_file))
    data_dir = tmp_path / "data"
    data_dir.mkdir()  # 让备份触发

    patch_migrator_config(data_dir=data_dir)
    svc = MigrationService(project_dir=tmp_path)
    result = svc.apply()
    assert result["exported"] >= 1
    assert (tmp_path / "raw" / "original.md").exists()
    assert result["backup_created"] is True


def test_apply_skips_missing_source_files(tmp_path, patch_migrator_config):
    """source_path 指向不存在的文件 → 跳过。"""
    _insert_file_knowledge("k1", str(tmp_path / "ghost.md"))  # 不存在
    patch_migrator_config()
    svc = MigrationService(project_dir=tmp_path)
    result = svc.apply()
    assert result["exported"] == 0
    assert result["skipped_missing"] >= 1
