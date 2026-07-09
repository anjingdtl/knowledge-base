"""Canonical Wiki v2 架构守卫:禁止业务服务绕过 WikiRepository 直接写 canonical。

canonical_v2 启用前,现状豁免由 ALLOWED_DIRECT_WRITES 锁定;Phase 4 改造后
逐步从此 allowlist 移除,测试随之收紧。新增的绕过调用(不在 allowlist)会立即失败。

C1 扩展:把 C0 审计暴露的 11 处越界写(api routes / wiki_lint / wiki_workflow /
wiki_log_compiler.open)纳入守卫视野并登记为过渡 allowlist(Phase 4 移除),
同时新增 open(...,"a"/"w") 追加/覆盖写探测,堵住 AST 只认方法调用的盲区。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# 直接写 canonical 的调用点。"绕过"指未经 src/services/wiki_repository.py 的
# WikiRepository 直接落库/落盘 wiki 知识。键=(相对 src 的模块路径, 写签名)。
# 写签名 = 方法名(insert_wiki_page 等)或 "open_write"(open(...,"a"/"w") 追加/覆盖写)。
ALLOWED_DIRECT_WRITES: dict[tuple[str, str], str] = {
    # --- T0.2 既有 7 条(FS/SQLite 写,Phase 4 移除)---
    ("services/wiki_compiler.py", "insert_wiki_page"):
        "A轨 SQLite 写,Phase 4 T4.3 降级为适配器后移除",
    ("services/wiki_compiler.py", "update_wiki_page"):
        "A轨 SQLite 写,Phase 4 T4.3 降级为适配器后移除",
    ("services/wiki_source_compiler.py", "write_markdown"):
        "B轨 FS 写,Phase 4 T4.1 改造经 WikiRepository 后移除",
    ("services/wiki_index_compiler.py", "write_markdown"):
        "index.md 生成,Phase 4 评估是否经 Repository",
    ("services/wiki_log_compiler.py", "write_text"):
        "log.md rebuild 全量写,Phase 4 改造后移除",
    # --- C1 新增:C0 审计暴露的越界写,登记为过渡 allowlist(Phase 4 移除)---
    ("api/routes/wiki.py", "insert_wiki_page"):
        "A轨 SQLite 写,Phase 4 路由经 WikiRepository 后移除",
    ("api/routes/wiki.py", "update_wiki_page"):
        "A轨 SQLite 写,Phase 4 路由经 WikiRepository 后移除",
    ("api/routes/wiki.py", "delete_wiki_page"):
        "A轨 SQLite 写,Phase 4 路由经 WikiRepository 后移除",
    ("services/wiki_lint.py", "update_wiki_page"):
        "A轨 SQLite lint 回写,Phase 4 经 WikiRepository 后移除",
    ("services/wiki_workflow.py", "update_wiki_page"):
        "A轨 SQLite 工作流写,Phase 4 经 WikiRepository 后移除",
    ("services/wiki_log_compiler.py", "open_write"):
        "log.md append 用 Path.open('a') 追加写,Phase 4 改造后移除",
}

# 守卫覆盖的模块 + 各自禁止的"直接写"方法名
GUARDED: dict[str, set[str]] = {
    "services/wiki_compiler.py": {"insert_wiki_page", "update_wiki_page"},
    "services/wiki_source_compiler.py": {"write_markdown"},
    "services/wiki_index_compiler.py": {"write_markdown"},
    "services/wiki_log_compiler.py": {"write_text"},
    # C1 新增:C0 审计暴露的盲区模块
    "api/routes/wiki.py": {"insert_wiki_page", "update_wiki_page", "delete_wiki_page"},
    "services/wiki_lint.py": {"update_wiki_page"},
    "services/wiki_workflow.py": {"update_wiki_page"},
}

# 额外检查 open(...,"a"/"w"/"x"/"+") 写的模块(堵 AST 只认方法调用的盲区)
OPEN_WRITE_GUARDED: set[str] = {
    "services/wiki_log_compiler.py",  # append() 用 Path.open("a") 追加写 log.md
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


def _open_write_mode(node: ast.Call) -> str | None:
    """从 open(...) / Path.open(...) 调用中提取 mode 字符串,无法判定时返回 None。

    builtin open(path, mode, ...): mode 是第 2 个位置参数(args[1])。
    Path.open(mode, ...): self 即路径,mode 是第 1 个位置参数(args[0])。
    两者都可用 mode= 关键字(优先)。
    """
    func = node.func
    is_builtin_open = isinstance(func, ast.Name) and func.id == "open"
    # 关键字 mode 优先
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    # 位置参数:builtin open → args[1];Path.open(Attribute)→ args[0]
    if is_builtin_open:
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
            return node.args[1].value
    else:
        if len(node.args) >= 1 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
    return None


def _find_open_writes(tree: ast.AST) -> bool:
    """探测 open(path, mode) 或 Path.open(mode) 调用,mode 含写标志(w/a/x/+)即命中。"""
    _WRITE_FLAGS = ("w", "a", "x", "+")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_open = (
            (isinstance(func, ast.Name) and func.id == "open")
            or (isinstance(func, ast.Attribute) and func.attr == "open")
        )
        if not is_open:
            continue
        mode = _open_write_mode(node)
        if mode is None:
            # 无显式 mode:builtin open 默认 "r"(读),Path.open 默认 "r"(读) → 不算写
            continue
        if any(flag in mode for flag in _WRITE_FLAGS):
            return True
    return False


def _scan() -> list[tuple[str, str]]:
    """扫描所有守卫模块,返回未豁免的直接写调用 (module, signature)。"""
    offenders: list[tuple[str, str]] = []
    # 方法调用维度
    for rel, names in GUARDED.items():
        path = SRC / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for hit in _find_calls(tree, names):
            if (rel, hit) not in ALLOWED_DIRECT_WRITES:
                offenders.append((rel, hit))
    # open 写维度
    for rel in OPEN_WRITE_GUARDED:
        path = SRC / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _find_open_writes(tree) and (rel, "open_write") not in ALLOWED_DIRECT_WRITES:
            offenders.append((rel, "open_write"))
    return offenders


def test_guard_catches_unlisted_direct_write():
    """守卫机制本身有效:能识别直接写调用。"""
    tree = ast.parse("import x\nx.insert_wiki_page({})\nx.write_markdown(p, {}, 'b')")
    found = _find_calls(tree, {"insert_wiki_page", "write_markdown"})
    assert found == ["insert_wiki_page", "write_markdown"]


def test_open_write_detection():
    """open 写探测机制有效:区分写模式(w/a/x/+)与读模式(r)。"""
    write_tree = ast.parse(
        'p.open("a", encoding="utf-8")\n'
        'open(path, "w")\n'
        'f = open(x, mode="wb")\n'
        'open(y, "x+")\n'
    )
    assert _find_open_writes(write_tree) is True
    read_tree = ast.parse(
        'open(path, "r")\n'
        'open(path, "rb")\n'
        'open(path)\n'  # 无 mode → 默认读
    )
    assert _find_open_writes(read_tree) is False


def test_current_direct_writes_are_allowlisted():
    """现状:所有直接写调用必须在 allowlist 内。新增绕过会失败。"""
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
        if name == "open_write":
            assert _find_open_writes(tree), (
                f"allowlist 条目 {rel}:{name} 在模块中找不到 open 写调用(已迁移?请移除该豁免)"
            )
        else:
            assert _find_calls(tree, {name}), (
                f"allowlist 条目 {rel}:{name} 在模块中找不到对应调用(已迁移?请移除该豁免)"
            )


# ---------------------------------------------------------------------------
# C6:wiki_v2 新服务禁止直接 import 全局单例(仅最外层 adapter 可解析后注入)
# ---------------------------------------------------------------------------
WIKI_V2_SERVICE_MODULES: set[str] = {
    "services/wiki_repository.py",
    "services/wiki_projection.py",
    "services/wiki_claim_extractor.py",
    "services/wiki_claim_matcher.py",
    "services/wiki_merge_engine.py",
    "services/wiki_page_locator.py",
    "services/wiki_query_service.py",
}

# 禁止直接 import 的全局单例(完整模块路径)
FORBIDDEN_GLOBAL_SINGLETONS: set[str] = {
    "src.core.container.get_active_container",
    "src.utils.config.Config",
    "src.services.db.Database",
}


def test_wiki_v2_services_no_global_singleton_imports():
    """C6:新 wiki_v2 服务不得直接 import 全局单例;仅最外层 adapter(container)可解析后注入。"""
    for rel in WIKI_V2_SERVICE_MODULES:
        path = SRC / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    assert full not in FORBIDDEN_GLOBAL_SINGLETONS, (
                        f"{rel} 违规 import 全局单例 {full}(C6:改用构造函数注入)"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in FORBIDDEN_GLOBAL_SINGLETONS, (
                        f"{rel} 违规 import {alias.name}(C6:改用构造函数注入)"
                    )
