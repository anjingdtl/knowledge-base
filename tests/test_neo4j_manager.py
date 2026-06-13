"""Neo4j local deployment safety tests."""

import zipfile

import pytest

from src.services import neo4j_manager


def test_safe_extract_rejects_parent_path(tmp_path):
    archive = tmp_path / "neo4j.zip"
    target = tmp_path / "install"
    escaped = tmp_path / "escaped.txt"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escaped.txt", "unexpected")

    with pytest.raises(RuntimeError, match="不安全"):
        neo4j_manager._extract_zip_safely(archive, target)

    assert not escaped.exists()


def test_start_requires_compatible_java(monkeypatch, tmp_path):
    neo4j_home = tmp_path / "neo4j"
    bin_dir = neo4j_home / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "neo4j.bat").write_text("@echo off", encoding="utf-8")
    manager = neo4j_manager.Neo4jManager(neo4j_home=neo4j_home)

    monkeypatch.setattr(manager, "is_running", lambda: False)
    monkeypatch.setattr(neo4j_manager, "_find_java_executable", lambda: None)
    monkeypatch.setattr(
        neo4j_manager.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("Neo4j started without Java"),
    )

    with pytest.raises(RuntimeError, match="Java 17"):
        manager.start()
