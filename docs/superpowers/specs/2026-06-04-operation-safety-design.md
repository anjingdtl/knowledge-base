# 操作安全设计（Operation Safety）

> 补齐 MCP 模式下 AI Agent 无限制写权限的操作可追溯性和变更可见性。

## 背景

系统在 MCP 模式下暴露 14 个写工具给 AI 编码工具，但存在三重安全缺陷：
1. **不可追溯** — AI Agent 做了什么完全不可审计
2. **不可见** — 返回值只有"操作成功"，用户无法感知变更内容
3. **不可逆** — title/tags 变更、block 变更、Wiki 删除无法恢复

## 分期

| 优先级 | 内容 | 预估改动量 |
|--------|------|-----------|
| P1 | operation_logs — 操作审计日志 | 新增 1 服务 + 1 repo + DB 表 + 14 处写操作埋点 |
| P2 | Write Diff Preview — 变更摘要增强 | 改 3 个 MCP 工具返回值 + 2 个 API 路由 |
| P3 | MCP dry_run — 预览模式 | 改 3 个 MCP 工具增加 dry_run 参数 |
| P4 | undo 扩展 — 版本快照覆盖全字段 | 改 _save_version + Wiki 删除加 trash |

## P1：操作审计日志（operation_logs）

### 新增数据库表

```sql
CREATE TABLE IF NOT EXISTS operation_logs (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    operator TEXT DEFAULT 'system',
    source TEXT NOT NULL DEFAULT 'mcp',
    snapshot_before TEXT,
    snapshot_after TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oplog_target ON operation_logs(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_oplog_time ON operation_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_oplog_operation ON operation_logs(operation);
```

字段说明：
- `operation`: create / update / delete / ingest / reindex / wiki_create / wiki_update / wiki_delete / workflow_transition / tag_relation / property_schema / job
- `target_type`: knowledge / wiki_page / block / tag_relation / property_schema / job
- `target_id`: 目标对象 ID
- `operator`: 操作者标识（MCP 模式默认 "system"，API 模式为 username）
- `source`: mcp / api / gui
- `snapshot_before`: JSON 快照（delete 时记录完整旧数据，update 时记录变更字段旧值）
- `snapshot_after`: JSON 快照（create 时记录新数据，update 时记录变更字段新值）
- `metadata`: 附加信息（如 file_path、tags 等）

### 新增服务

`src/services/operation_log.py` — `OperationLogService`

```python
class OperationLogService:
    def log(self, operation, target_type, target_id,
            operator="system", source="mcp",
            before=None, after=None, metadata=None) -> str

    def query(self, target_type=None, target_id=None,
              operation=None, source=None,
              limit=50, offset=0) -> list[dict]

    def get_by_target(self, target_type, target_id, limit=20) -> list[dict]
```

### 新增仓库

`src/repositories/operation_log_repo.py` — `OperationLogRepository`

标准 CRUD：insert, query (带筛选), get_by_target, count, cleanup (按时间清理旧日志)

### 埋点位置

在以下 14 个写操作入口记录日志：

1. **MCP create** → `operation="create"`, `target_type="knowledge"`, before=None, after={"title", "content", "tags"}
2. **MCP update** → `operation="update"`, before=旧值, after=新值（仅变更字段）
3. **MCP delete** → `operation="delete"`, before=完整条目快照, after=None
4. **MCP ingest_file** → `operation="ingest"`, after=导入结果
5. **MCP ingest_url** → `operation="ingest"`, after=导入结果
6. **MCP reindex_all** → `operation="reindex"`, target_type="system", target_id="all"
7. **MCP save_to_wiki** → `operation="wiki_create"`, target_type="wiki_page"
8. **MCP wiki_submit_review/approve/reject/deprecate** → `operation="workflow_transition"`, before=旧状态, after=新状态
9. **MCP wiki_restore_version** → `operation="wiki_update"`, before=旧版本号, after=恢复版本号

DI 容器注册 `OperationLogService` 为 lazy 服务。

### 配置

config.yaml 新增：

```yaml
safety:
  operation_log:
    enabled: true
    retention_days: 90
```

---

## P2：变更摘要增强（Write Diff Preview）

### MCP update 增强返回值

当前：
```python
return {"message": "知识更新成功", "updated_fields": list(fields.keys())}
```

改为：
```python
return {
    "message": "知识更新成功",
    "updated_fields": list(fields.keys()),
    "changes": {
        "title": {"before": "旧标题", "after": "新标题"},
        "content": {"before": "旧内容前200字...", "after": "新内容前200字..."},
        "tags": {"before": ["旧标签"], "after": ["新标签"]}
    },
    "version": new_version
}
```

### MCP delete 增强返回值

当前：
```python
return {"message": "知识删除成功", "id": item_id}
```

改为：
```python
return {
    "message": "知识删除成功",
    "id": item_id,
    "deleted_item": {
        "title": "被删条目标题",
        "tags": ["标签"],
        "content_preview": "内容前200字..."
    },
    "version": last_version
}
```

### API 路由同步增强

`src/api/routes/knowledge.py` 的 `update_knowledge` 和 `delete_knowledge` 返回同样结构。

---

## P3：MCP dry_run 预览模式

### 适用工具

仅 3 个高风险工具：`delete`、`update`、`reindex_all`

### delete dry_run

新增参数 `dry_run: bool = False`：

```python
def delete(item_id: str, dry_run: bool = False) -> dict:
```

dry_run=True 时：
- 不执行删除
- 返回将被删除的条目信息和关联数据（blocks 数、chunks 数、entity_refs 数、versions 数）
- 返回 `{"dry_run": True, "would_delete": {...}, "warning": "此操作不可逆"}`

### update dry_run

新增参数 `dry_run: bool = False`：

dry_run=True 时：
- 不执行更新
- 返回 changes diff（同 P2 的 changes 结构）
- 返回 `{"dry_run": True, "would_change": {...}}`

### reindex_all dry_run

新增参数 `dry_run: bool = False`：

dry_run=True 时：
- 不执行重建
- 返回将被重建的知识条目数量
- 返回 `{"dry_run": True, "would_reindex": count}`

---

## P4：undo 扩展（版本快照覆盖全字段）

### 问题

当前 `_save_version` 仅在 `content` 变更时触发，title-only 或 tags-only 变更不创建版本快照。

### 方案

1. **放宽触发条件**：`update_knowledge` 中，只要 fields 包含 title/content/tags 任一字段，均触发版本快照
2. **版本快照已包含 title+content+tags**：检查 `_save_version` 方法，当前已保存这三个字段，只需放宽调用条件即可
3. **Wiki 删除加 trash**：仿照 `file_graph.delete_page` 的 trash 机制，`Database.delete_wiki_page` 改为先标记 `status='deleted'`，保留数据 30 天，purge 方法清理

### 具体改动

`src/services/db.py` `update_knowledge` 方法：

```python
# 改前：
if old and old.get("content") != fields.get("content"):
    cls._save_version(item_id, old)

# 改后：
_version_fields = {"title", "content", "tags"}
if old and _version_fields & set(fields.keys()):
    cls._save_version(item_id, old)
```

Wiki 删除加 trash 机制：新增 `status='deleted'`，`delete_wiki_page` 改为 soft delete，新增 `purge_wiki_page` 硬删除。

---

## 不做的部分

- **通用 UndoManager/UndoStack** — 过度工程化，当前版本恢复 + trash 机制已覆盖主要场景
- **操作速率限制** — 属于性能/安全范畴，与操作可追溯性无关
- **变更通知机制** — 无实际需求场景
- **批量操作保护** — 通过 dry_run 和 operation_log 间接解决

## 成功标准

1. 所有 14 个 MCP 写操作均记录到 operation_logs 表
2. MCP update/delete 返回值包含变更前后对比
3. delete/update/reindex_all 支持 dry_run=True 预览
4. title/tags 变更也触发版本快照
5. Wiki 删除改为 soft delete，30 天后可 purge
6. 既有测试全部通过，新增操作安全相关测试覆盖
