# MCP-First 完全体改造进度

> 截止 2026-06-04，commit `0b20777`
> 规范文档: `docs/superpowers/specs/2026-06-04-mcp-first-complete-rag-spec.md`
> 实施计划: `C:\Users\Administrator\.claude\plans\twinkling-wiggling-diffie.md`

## 总览

6 个 Sprint（严格顺序），已完成 6 个，剩余 0 个。

| Sprint | Phase | 目标 | Commit | 状态 |
|--------|-------|------|--------|------|
| 1 | 0+1 | MCP 验收基线 + 工具契约标准化（envelope） | `764b1d5` | ✅ |
| 2 | 2+3 | RAG 完全体 + Agentic Query 入口 | `ee98e07` | ✅ |
| 3 | 4 | 写操作安全闭环（soft delete + undo） | `b55bb37` | ✅ |
| 4 | 5 | 大文件异步任务（auto-routing to job） | `0b20777` | ✅ |
| 5 | 6 | Embedding-time Contextual Headers | working tree | ✅ |
| 6 | 7 | MCP 文档 + Prompt 契约 | working tree | ✅ |

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

### Sprint 5: Embedding-time Contextual Headers

- `config.yaml` 新增 `rag.embedding_context.*` 配置块:
  `enabled / include_parent_chain / include_links / include_siblings / max_chars`
- `EmbeddingService.build_embedding_text(block)` 上线，embedding-time 动态拼装父链、链接摘要、可选相邻块上下文
- `index_knowledge_item()` 向量化时使用 contextual embedding text，`blocks.content` 和 FTS 仍保持原文
- `reindex_all(dry_run=True)` 返回 `affected_items / affected_blocks / embedding_context_enabled / estimated_batches`
- `read` 工具新增可选参数:
  `include_blocks / include_embedding_preview / include_effective_properties / include_linked_summaries`
- `include_embedding_preview=True` 时在 block 级返回 `{enabled, text, char_count, config}`

**新增文件**: `tests/test_embedding_context.py`

### Sprint 6: MCP 文档 + Prompt 契约

- 新增 5 份 MCP-first Agent 文档:
  `docs/mcp/agent-usage.md`,
  `docs/mcp/tool-contract.md`,
  `docs/mcp/query-dsl.md`,
  `docs/mcp/safety-and-undo.md`,
  `docs/mcp/ingest-jobs.md`
- 新增 4 个 MCP prompt 模板:
  `kb_agent_research`,
  `kb_safe_update`,
  `kb_import_and_verify`,
  `kb_query_with_sources`
- 保留旧 prompt `kb_qa`，向后兼容既有客户端
- 新增只读工具 `get_source_graph`，让 research/qna flow 中的溯源步骤指向真实 MCP 工具
- `ask` 支持 `include_graph / include_context / max_sources / max_graph_nodes` 可选参数
- `kb_capabilities.recommended_flows` 与文档中的 research/safe_update/import/qna 流程保持一致

**新增文件**: `tests/test_mcp_docs_prompts.py`

## 测试状态

```
379 passed, 1 skipped, 3 failed
```

3 个 `test_librarian_schema.py` 失败是**预存 bug**（中文 schema normalization），从 Sprint 1 起就存在，与本次改造无关。

本轮 Sprint 5 验证:

```bash
pytest tests/test_embedding_context.py -q
pytest tests/test_mcp_contract.py -q
pytest tests/test_operation_safety.py -q
pytest tests/test_mcp_rag_full.py -q
pytest tests/test_mcp_server.py -q
pytest tests/test_full_pipeline_e2e.py -q
```

本轮 Sprint 6 验证:

```bash
pytest tests/test_mcp_docs_prompts.py tests/test_mcp_server.py tests/test_mcp_contract.py tests/test_mcp_rag_full.py tests/test_operation_safety.py tests/test_embedding_context.py -q
```

---

## 待完成工作

当前规范内 6 个 Sprint 已全部完成。剩余已知风险是 `test_librarian_schema.py` 的 3 个预存失败（中文 schema normalization），与 MCP-first 改造无关。

## 关键架构信息

### 依赖注入容器

所有入口通过 `create_container()` → `AppContainer` 初始化:

```
Config → Database → VectorStore → BlockStore → Embedding/LLM → Repositories → 业务服务(lazy)
```

Container 位于 `src/core/container.py`，每个业务服务通过 `@property` lazy init。

### MCP 工具总数

当前约 **40+ 工具**、**3 个资源**、**5 个 prompt 模板**。

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
