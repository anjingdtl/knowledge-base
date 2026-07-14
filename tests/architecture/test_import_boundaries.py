"""Import boundary guards — Phase-3 maintainability.

Forbidden:
  retrieval → mcp
  answering → mcp
  repositories → container
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src"


def _iter_py_files(package: str):
    base = ROOT / package
    if not base.exists():
        return
    for path in base.rglob("*.py"):
        if path.name == "__init__.py" and path.stat().st_size == 0:
            continue
        yield path


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
    return mods


def _assert_no_import(package: str, forbidden_prefix: str):
    offenders = []
    for path in _iter_py_files(package):
        for mod in _imports_of(path):
            if mod == forbidden_prefix or mod.startswith(forbidden_prefix + "."):
                offenders.append(f"{path.relative_to(ROOT)} → {mod}")
    assert not offenders, "Forbidden imports:\n" + "\n".join(offenders)


def test_retrieval_does_not_import_mcp():
    _assert_no_import("retrieval", "src.mcp")
    _assert_no_import("retrieval", "src.mcp_server")


def test_answering_does_not_import_mcp():
    _assert_no_import("answering", "src.mcp")
    _assert_no_import("answering", "src.mcp_server")


def test_repositories_do_not_import_container():
    _assert_no_import("repositories", "src.core.container")


def test_retrieval_does_not_import_graph_or_memory_services():
    # Soft boundary: retrieval package should not pull graph/memory services
    offenders = []
    for path in _iter_py_files("retrieval"):
        for mod in _imports_of(path):
            if mod in {
                "src.services.agent_memory",
                "src.services.graph_builder",
                "src.services.unified_graph",
            } or mod.startswith("src.services.graph_backend"):
                offenders.append(f"{path.relative_to(ROOT)} → {mod}")
    assert not offenders, offenders


def test_new_packages_avoid_get_active_container():
    """Closure packages must not call get_active_container (WP3 whitelist)."""
    for package in ("answering", "retrieval", "application", "storage"):
        for path in _iter_py_files(package):
            text = path.read_text(encoding="utf-8")
            assert "get_active_container(" not in text, path
            assert "Database._instance" not in text, path


def test_service_groups_exposed_on_container():
    from src.compatibility import container_access
    from src.core.container import AppContainer, get_active_container

    assert hasattr(AppContainer, "groups")
    # property exists on class
    assert isinstance(AppContainer.groups, property)
    assert callable(container_access.get_active_container)
    assert get_active_container() is container_access.get_active_container()
