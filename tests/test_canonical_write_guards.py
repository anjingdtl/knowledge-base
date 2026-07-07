"""Canonical Wiki v2 架构守卫:禁止业务服务绕过 WikiRepository 直接写 canonical。

canonical_v2 启用前,现状豁免由 ALLOWED_DIRECT_WRITES 锁定;Phase 4 改造后
逐步从此 allowlist 移除,测试随之收紧。新增的绕过调用(不在 allowlist)会立即失败。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# 直接写 canonical 的调用点。"绕过"指未经 src/services/wiki_repository.py 的
# WikiRepository 直接落库/落盘 wiki 知识。键=(相对 src 的模块路径,被调用名)。
ALLOWED_DIRECT_WRITES: dict[tuple[str, str], str] = {
    ("services/wiki_compiler.py", "insert_wiki_page"):
        "A轨 SQLite 写,Phase 4 T4.3 降级为适配器后移除",
    ("services/wiki_compiler.py", "update_wiki_page"):
        "A轨 SQLite 写,Phase 4 T4.3 降级为适配器后移除",
    ("services/wiki_entity_updater.py", "write_markdown"):
        "B轨 FS 写,Phase 4 T4.1 改造经 WikiRepository 后移除",
    ("services/knowledge_workflow.py", "write_markdown"):
        "B轨 FS 写,Phase 4 T4.1 改造经 WikiRepository 后移除",
    ("services/wiki_source_compiler.py", "write_markdown"):
        "B轨 FS 写,Phase 4 T4.1 改造经 WikiRepository 后移除",
    ("services/wiki_index_compiler.py", "write_markdown"):
        "index.md 生成,Phase 4 评估是否经 Repository",
    ("services/wiki_log_compiler.py", "write_text"):
        "log.md 直接 write_text 非原子写(C4),Phase 4 改造后移除",
}

# 守卫覆盖的模块 + 各自禁止的"直接写"调用名
GUARDED: dict[str, set[str]] = {
    "services/wiki_compiler.py": {"insert_wiki_page", "update_wiki_page"},
    "services/wiki_entity_updater.py": {"write_markdown"},
    "services/knowledge_workflow.py": {"write_markdown"},
    "services/wiki_source_compiler.py": {"write_markdown"},
    "services/wiki_index_compiler.py": {"write_markdown"},
    "services/wiki_log_compiler.py": {"write_text"},  # C4: 非原子写,Phase 4 处理
}


def _find_calls(tree: ast.AST, names: set[str]) -> list[str]:
    """返回模块中出现的被禁调用名(去重)。"""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in names:
                found.add(node.func.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in names:
                found.add(node.func.id)
    return sorted(found)


def _scan() -> list[tuple[str, str]]:
    """扫描所有守卫模块,返回未豁免的直接写调用 (module, name)。"""
    offenders: list[tuple[str, str]] = []
    for rel, names in GUARDED.items():
        path = SRC / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for hit in _find_calls(tree, names):
            if (rel, hit) not in ALLOWED_DIRECT_WRITES:
                offenders.append((rel, hit))
    return offenders


def test_guard_catches_unlisted_direct_write():
    """守卫机制本身有效:能识别直接写调用。"""
    tree = ast.parse("import x\nx.insert_wiki_page({})\nx.write_markdown(p, {}, 'b')")
    found = _find_calls(tree, {"insert_wiki_page", "write_markdown"})
    assert found == ["insert_wiki_page", "write_markdown"]


def test_current_direct_writes_are_allowlisted():
    """v1.5.2 现状:所有直接写调用必须在 allowlist 内。新增绕过会失败。"""
    offenders = _scan()
    assert offenders == [], (
        "发现未豁免的直接 canonical 写(若为已知现状,加入 ALLOWED_DIRECT_WRITES;若为新代码,改走 WikiRepository): "
        + ", ".join(f"{m}:{n}" for m, n in offenders)
    )


def test_allowlist_entries_actually_exist():
    """allowlist 每条都对应真实调用,避免空壳豁免漂移。"""
    for (rel, name) in ALLOWED_DIRECT_WRITES:
        path = SRC / rel
        assert path.exists(), f"allowlist 指向不存在的模块: {rel}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert _find_calls(tree, {name}), f"allowlist 条目 {rel}:{name} 在模块中找不到对应调用(已迁移?请移除该豁免)"
