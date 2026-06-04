# MCP-First 完全体改造进度

> 截止 2026-06-04，commit `0b20777`
> 规范文档: `docs/superpowers/specs/2026-06-04-mcp-first-complete-rag-spec.md`
> 实施计划: `C:\Users\Administrator\.claude\plans\twinkling-wiggling-diffie.md`

## 总览

6 个 Sprint（严格顺序），已完成 4 个，剩余 2 个。

| Sprint | Phase | 目标 | Commit | 状态 |
|--------|-------|------|--------|------|
| 1 | 0+1 | MCP 验收基线 + 工具契约标准化（envelope） | `764b1d5` | ✅ |
| 2 | 2+3 | RAG 完全体 + Agentic Query 入口 | `ee98e07` | ✅ |
| 3 | 4 | 写操作安全闭环（soft delete + undo） | `b55bb37` | ✅ |
| 4 | 5 | 大文件异步任务（auto-routing to job） | `0b20777` | ✅ |
| 5 | 6 | Embedding-time Contextual Headers | — | 🔲 |
| 6 | 7 | MCP 文档 + Prompt 契约 | — | 🔲 |

## 已完成的功能清单

### Sprint 1: Envelope 统一

- 所有 MCP 工具返回 `{ok, data, error, meta, operation_id, dry_run}` envelope
- 稳定错误码: `NOT_FOUND` / `VALIDATION_ERROR` / `PERMISSION_DENIED` / `INGEST_FAILED` / `INTERNAL_ERROR` 等
- 读工具分页元数据: `truncated` / `limit` / `offset` / `next_offset` / `total_estimate`
- `kb_capabilities` 工具上线（能力清单 + limits + recommended_flows）
- `_op_log` 返回 `log_id`，写入 envelope `operation_id`

**新增文件**: `src/utils/envelope.py`

### Sprint 2: RAG 完全体

- `ask` 返回 7 字段: `answer / sources / source_graph / route / query_plan / block_contexts / warnings`
- 新增 3 个 MCP 工具: `route_query` / `execute_query` / `ask_with_query`
- `AgenticRouter.route()` 返回结构化: `mode / query_spec / traverse / explanation`
- `structured_query` / `explain_query` / `graph_traverse` 返回 dict（不再返回 JSON string）

### Sprint 3: 写操作安全闭环

- `deleted_at` 软删除（alembic migration `e001_soft_delete_knowledge`）
- `db.py`: `soft_delete_knowledge` / `restore_knowledge` / `purge_knowledge`，所有读路径默认 `include_deleted=False`
- `knowledge_repo`: `delete(hard=False)` / `restore()` / `purge()`
- `OperationLogService.undo()`: 支持 `update` / `create` / `delete` / `ingest` 四种反向操作
- 5 个新 MCP 工具: `restore_knowledge` / `preview_operation` / `get_operation_log` / `undo_operation` / `list_recent_operations`
- 所有写工具支持 `dry_run`

**新增文件**: `alembic/versions/e001_soft_delete_knowledge.py`, `tests/test_undo_operation.py`

### Sprint 4: 大文件异步任务

- `_file_ingest_handler` / `_url_ingest_handler`: 逐条导入 + 进度上报 + cancel 检查
- 结构化返回: `created_items / skipped_items / failed_items / sheet_count / page_count / block_count`
- `_estimate_file_complexity`: 文件大小 / sheet 数 / 页数 / 段落数多维度判定
- `ingest_file` 大文件自动路由: 超阈值返回 `job_id` + `routed_async=True`
- 4 个新 MCP 工具: `create_ingest_job` / `get_job` / `list_jobs` / `cancel_job`
- `config.yaml` 新增 `ingest.*` 阈值 + `jobs.*` 配置
- 修复 `AsyncJob.from_db` double JSON parse 预存 bug

**新增文件**: `tests/test_async_ingest.py`

## 测试状态

```
379 passed, 1 skipped, 3 failed
```

3 个 `test_librarian_schema.py` 失败是**预存 bug**（中文 schema normalization），从 Sprint 1 起就存在，与本次改造无关。

---

## 待完成工作

### Sprint 5: Phase 6 — Embedding-time Contextual Headers

**目标**: Block 向量索引时动态拼装父链上下文，提升检索质量。`blocks.content` 保持原文不被污染。

**需要修改的文件**:

| 文件 | 改动 |
|------|------|
| `config.yaml` | 新增 `rag.embedding_context.*` 配置块 |
| `src/services/embedding.py` | 新增 `build_embedding_text(block)` 方法，调用 `BlockContextService.build_context()` 拼装 embedding 文本 |
| `src/services/indexer.py` | `reindex_all(dry_run=True)` 返回 `affected_items / affected_blocks / estimated_batches`；正式 reindex 使用新 embedding 文本 |
| `src/mcp_server.py` | `read` 工具新增参数 `include_blocks / include_embedding_preview / include_effective_properties / include_linked_summaries` |

**config.yaml 新增配置**（参考计划文件）:
```yaml
rag:
  embedding_context:
    enabled: true
    include_parent_chain: true
    include_links: true
    include_siblings: false
    max_chars: 1200
```

**验收标准**:
- `blocks.content` 不被 contextual header 污染
- `embedding_text` 含父链 + 链接摘要
- `reindex_all(dry_run=True)` 不动 DB
- 新增 `tests/test_embedding_context.py`

### Sprint 6: Phase 7 — MCP 文档 + Prompt 契约

**目标**: 围绕 Agent 工作流重写 MCP 使用文档和 prompt 模板。

**新增文件**:
- `docs/mcp/agent-usage.md`
- `docs/mcp/tool-contract.md`
- `docs/mcp/query-dsl.md`
- `docs/mcp/safety-and-undo.md`
- `docs/mcp/ingest-jobs.md`

**mcp_server.py 新增 4 个 prompt 模板**:
- `kb_agent_research(question)` — 知识库研究标准流程
- `kb_safe_update(item_id, fields)` — 安全更新流程
- `kb_import_and_verify(file_path)` — 导入并校验流程
- `kb_query_with_sources(question)` — 带溯源问答流程

**验收标准**:
- 5 个文档齐全
- 4 个 prompt 模板注册成功
- `kb_capabilities.recommended_flows` 与文档一致

## 关键架构信息

### 依赖注入容器

所有入口通过 `create_container()` → `AppContainer` 初始化:

```
Config → Database → VectorStore → BlockStore → Embedding/LLM → Repositories → 业务服务(lazy)
```

Container 位于 `src/core/container.py`，每个业务服务通过 `@property` lazy init。

### MCP 工具总数

当前约 **40+ 工具**、**3 个资源**、**1 个 prompt 模板**。Sprint 6 将新增 4 个 prompt。

### Envelope 工具函数

`src/utils/envelope.py` 提供 `ok()` / `fail()` / `dry_run_preview()` / `attach_operation_id()`。

### 操作日志

`src/services/operation_log.py` — `OperationLogService.log()` 返回 `log_id`，`undo()` 支持 update/create/delete/ingest。`_UNDOABLE_KINDS` 白名单控制可撤销的操作类型。

### 异步任务

- `src/services/async_task.py` — `AsyncTaskService` 数据模型 + DB 操作
- `src/services/async_worker.py` — `TaskRegistry`（注册表 + cancel 标记）+ `AsyncWorker`（线程池执行器）
- `src/services/async_tasks.py` — 所有 handler 实现（6 个: reindex_all / wiki_compile / wiki_lint / wiki_site_generate / file_ingest / url_ingest）

### 软删除模式

`knowledge_items.deleted_at` 列（TEXT, nullable），所有读路径默认 `include_deleted=False`。`delete()` 默认软删，`purge()` 硬删。MD 文件移到 `.trash/` 目录。

### 运行命令

```bash
# 测试
pytest tests/ -q                                    # 全量
pytest tests/test_async_ingest.py -v               # Sprint 4
pytest tests/test_undo_operation.py -v             # Sprint 3

# MCP Server
python run_mcp.py                                   # stdio
shinehe-mcp -t streamable-http --port 9000          # HTTP

# 数据库迁移
alembic upgrade head
```
