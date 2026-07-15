# GUI MCP 启动与标签补标可靠性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 GUI 在数据库版本落后时安全地迁移后启动 MCP，并让标签补标在 LLM 慢/失败时可取消且必定恢复界面。

**Architecture:** MCP 启动前只读检查数据库启动计划；GUI 经用户确认后在后台调用现有的 `migrate_database()`（其本身会备份、升级并校验），成功才调用既有启动器。标签链路把单条 LLM 请求限制为一个独立短超时，工作线程检查取消请求，并以 `finally` 发出收尾信号。

**Tech Stack:** Python 3.10+、PySide6、pytest、SQLite/Alembic、OpenAI-compatible LLM client。

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/services/mcp_launcher.py` | 检查 MCP 启动所需的数据库状态，并执行已有的安全迁移工作流。 |
| `src/gui/main_window.py` | 在 GUI 线程外迁移/启动 MCP，展示确认与结果。 |
| `src/services/tag_inference.py` | 向 LLM 调用透传每条自动标签请求的超时。 |
| `src/gui/knowledge_view.py` | 支持可中断的自动标签线程，确保所有结束路径恢复 UI。 |
| `tests/test_mcp_gui_status.py` | MCP 数据库预检与迁移委托的回归测试。 |
| `tests/test_critical_bugfix_e2e.py` | LLM 标签推断超时参数的回归测试。 |
| `tests/test_knowledge_view_autotag.py` | 自动标签取消和异常收尾的 GUI 回归测试。 |

### Task 1: Make GUI MCP startup migration-aware

**Files:**
- Modify: `src/services/mcp_launcher.py`
- Modify: `src/gui/main_window.py`
- Modify: `tests/test_mcp_gui_status.py`

- [ ] **Step 1: Write failing launcher tests**

```python
def test_migration_requirement_returns_actionable_message(monkeypatch):
    monkeypatch.setattr(mcp_launcher, "inspect_database_bootstrap", lambda *a, **kw: Plan("block"))
    assert "数据库迁移" in mcp_launcher.get_migration_requirement()

def test_migrate_database_for_mcp_delegates_to_safe_workflow(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_launcher.Config, "get_db_path", lambda: tmp_path / "kb.db")
    monkeypatch.setattr(mcp_launcher, "migrate_database", lambda path: {"ok": True, "backup": "backup.sqlite"})
    assert "备份" in mcp_launcher.migrate_database_for_mcp()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_gui_status.py -q`

Expected: FAIL because `get_migration_requirement` and `migrate_database_for_mcp` do not exist.

- [ ] **Step 3: Implement the minimal preflight and migration helpers**

```python
def get_migration_requirement() -> str | None:
    plan = inspect_database_bootstrap(Config.get_db_path(), config=Config, project_root=_PROJECT_ROOT)
    if plan.action != "block":
        return None
    return f"MCP 启动前需要数据库迁移：{plan.migration_status.message}"

def migrate_database_for_mcp() -> str:
    result = migrate_database(Config.get_db_path())
    if not result.get("ok"):
        raise RuntimeError("数据库迁移未完成")
    return f"数据库已安全迁移，备份：{result['backup']}"
```

Add a `QThread` worker to `main_window.py`; it optionally calls `migrate_database_for_mcp()`, then calls `start()`, and emits exactly one result signal.  Before creating that worker, `_toggle_mcp()` asks for confirmation when `get_migration_requirement()` is non-empty; rejecting restores the unchecked button state.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_mcp_gui_status.py tests/test_settings_dialog_service_poll.py -q`

Expected: PASS.

### Task 2: Bound and cancel GUI auto-tag work

**Files:**
- Modify: `src/services/tag_inference.py`
- Modify: `src/gui/knowledge_view.py`
- Modify: `tests/test_critical_bugfix_e2e.py`
- Create: `tests/test_knowledge_view_autotag.py`

- [ ] **Step 1: Write failing regression tests**

```python
def test_infer_tags_by_llm_passes_explicit_timeout():
    with patch("src.services.llm.LLMService") as service:
        service.return_value.chat.return_value = '[]'
        infer_tags_by_llm("title", "content", [], timeout=12)
    assert service.return_value.chat.call_args.kwargs["timeout"] == 12

def test_autotag_worker_finishes_after_interruption(qapp, monkeypatch):
    worker = AutoTagWorker(items=[{"id": "x", "title": "x", "tags": []}], use_llm=True)
    worker.requestInterruption()
    completed = []
    worker.finished.connect(lambda *args: completed.append(args))
    worker.run()
    assert completed == [(0, 0, 1)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_critical_bugfix_e2e.py tests/test_knowledge_view_autotag.py -q`

Expected: FAIL because the tag inference function has no timeout parameter and the worker does not stop on an interruption request.

- [ ] **Step 3: Implement minimal bounded worker behavior**

```python
def infer_tags_by_llm(..., timeout: float | None = None) -> list[dict]:
    response = llm.chat(messages, max_tokens_override=300, silent=True, timeout=timeout)

def infer_tags(..., llm_timeout: float | None = None) -> list[dict]:
    llm_results = infer_tags_by_llm(..., timeout=llm_timeout)
```

`AutoTagWorker.run()` reads `tagging.llm_timeout` with a 12-second default, checks `isInterruptionRequested()` before each item, catches unexpected worker errors, and emits `finished(tagged, skipped, total)` from `finally`.  The progress dialog exposes a Cancel button connected to `requestInterruption`; `_on_autotag_finished()` reports cancellation and always re-enables the action.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_critical_bugfix_e2e.py tests/test_knowledge_view_autotag.py -q`

Expected: PASS.

### Task 3: Validate the repaired runtime against the real database

**Files:**
- Modify: `tests/test_mcp_gui_status.py` and `tests/test_knowledge_view_autotag.py` only if the preceding focused tests reveal a missing regression.

- [ ] **Step 1: Run the safe migration workflow on the configured database**

Run: `python -m src.cli db migrate`

Expected: exit code 0; output reports a backup path and `at_head: true`.

- [ ] **Step 2: Verify MCP can bind using the GUI-equivalent entry point**

Run: `python run_mcp.py -t streamable-http --host 127.0.0.1 --port 19090`

Expected: server remains running and accepts a TCP connection; terminate the diagnostic process after observing readiness.

- [ ] **Step 3: Run the focused regression suite and static checks**

Run: `pytest tests/test_mcp_gui_status.py tests/test_settings_dialog_service_poll.py tests/test_critical_bugfix_e2e.py tests/test_knowledge_view_autotag.py -q && ruff check src/services/mcp_launcher.py src/gui/main_window.py src/services/tag_inference.py src/gui/knowledge_view.py tests/test_mcp_gui_status.py tests/test_critical_bugfix_e2e.py tests/test_knowledge_view_autotag.py`

Expected: exit code 0.

- [ ] **Step 4: Commit**

```bash
git add src/services/mcp_launcher.py src/gui/main_window.py src/services/tag_inference.py src/gui/knowledge_view.py tests/test_mcp_gui_status.py tests/test_critical_bugfix_e2e.py tests/test_knowledge_view_autotag.py docs/superpowers/plans/2026-07-15-gui-mcp-and-tagging-reliability.md
git commit -m "fix(gui): recover mcp startup and auto-tag progress"
```
