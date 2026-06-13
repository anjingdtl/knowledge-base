# MCP-First 完全体 Structured/Graph RAG Spec

> 项目主入口从“用户在 GUI 内管理知识库”调整为“AI Agent 通过 MCP 稳定调用知识库”。GUI 保留为本地管理、调试和人工校验界面，但不再是第一产品面。

## 背景

现有重构已经把项目推进到 Structured/Graph RAG 的基础形态：

- 已有 File-First / Block-first 存储路径，`knowledge_items`、`blocks`、`entity_refs` 能表达 Page、Block 和链接关系。
- 已有 `BlockContextService`，支持命中 Block 后补充父链、相邻兄弟和链接摘要。
- 已有 `source_graph` 构建服务，RAG 结果可以携带局部来源图谱。
- 已有 `QuerySpec`、`QueryExecutor`、`QueryExplainer`、`GraphTraversalService`、`AgenticRouter`，具备结构化查询和图遍历地基。
- 已有 `operation_logs` 表、仓库和服务，部分 MCP 写工具已经有审计埋点和 `dry_run`。
- 已有 async job 基础设施，可承接大文件导入和长任务。

但从 MCP-first 角度看，仍未达到“Agent 可稳定长期调用”的完全体：

- MCP 返回格式不统一，有的工具返回 list/dict，有的返回 JSON 字符串，有的失败时抛异常。
- `ask` 返回仍偏人类问答，缺少稳定的 route、query_plan、block_contexts、warnings 等 Agent 可消费字段。
- `structured_query`、`graph_traverse` 结果仍不够 Block-first，分页、截断和大 payload 保护不完整。
- 写操作的 `operation_id`、preview、undo、audit query 还没有闭环到所有 MCP 写工具。
- 大 Excel / PDF / DOCX 导入还没有统一为 MCP job 协议，Agent 轮询和校验链路不完整。
- Embedding-time contextual header 还未形成可配置、可解释、可 dry_run 的索引策略。
- MCP 使用文档和 prompt 契约还没有围绕 Agent 工作流重写。

## 目标

最终目标是让 Agent 通过 MCP 能稳定完成以下任务：

1. 导入、检索、问答、结构化查询、图谱遍历。
2. 预览变更、执行变更、审计变更，并在主要场景下恢复误操作。
3. 获取可解释 RAG 结果：答案、来源 Block、source graph、route、query_plan、block_contexts、warnings。
4. 面对大文件、大图谱、大批量操作时不假死、不误删、不返回不可消费的大 payload。
5. 通过稳定 prompt 模板和工具契约，让 Claude Code、Cursor、Cline、Roo Code、Windsurf 等 Agent 都能使用同一调用范式。

## 非目标

- 不引入外部向量库或图数据库；继续使用 SQLite / sqlite-vec / FTS5。
- 不把 GUI 作为首要产品形态；GUI 只同步展示 MCP 已具备的核心状态和调试视图。
- 不破坏旧 MCP 工具名；旧工具名保留，返回结构逐步兼容升级。
- 不在首轮追求复杂多用户权限系统；MCP 本地信任模型下优先做 preview、audit、undo 和限制大 payload。

## 设计原则

1. **MCP contract first**：所有新能力先定义 MCP 工具契约，再决定 API/GUI 是否需要展示。
2. **Agent-readable**：返回值必须是 JSON-compatible dict/list，不让 Agent 解析自然语言状态。
3. **Block-first provenance**：召回、引用、图谱和 structured query 都保留到 Block 粒度。
4. **Safety by default**：高风险写操作默认支持 dry_run、operation log、soft delete 和可恢复快照。
5. **Bounded payload**：所有大结果都必须有 `limit`、`offset`、`truncated`、`next_offset`、`total_estimate`。
6. **Original text untouched**：`blocks.content` 保存原文；embedding context 和 RAG context 动态拼接。
7. **Progressive compatibility**：旧工具名不删，新增字段和 envelope 尽量兼容现有 Agent 配置。

## 统一 MCP 返回 Envelope

所有 MCP 工具最终统一为以下结构：

成功：

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "limit": 20,
    "offset": 0,
    "truncated": false,
    "next_offset": null,
    "total_estimate": 20
  }
}
```

失败：

```json
{
  "ok": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "knowledge item not found",
    "details": {
      "item_id": "..."
    }
  }
}
```

dry_run：

```json
{
  "ok": true,
  "dry_run": true,
  "would_change": {},
  "meta": {}
}
```

写操作：

```json
{
  "ok": true,
  "operation_id": "...",
  "data": {},
  "meta": {}
}
```

## Public MCP Interfaces

### 新增工具

- `kb_capabilities`
- `route_query`
- `execute_query`
- `ask_with_query`
- `get_source_graph`
- `get_operation_log`
- `undo_operation`
- `preview_operation`
- `list_recent_operations`
- `create_ingest_job`
- `get_job`
- `list_jobs`
- `cancel_job`

### 强化现有工具

- `ask`
- `search`
- `search_fulltext`
- `structured_query`
- `explain_query`
- `graph_traverse`
- `ingest_file`
- `ingest_url`
- `create`
- `read`
- `update`
- `delete`
- `reindex_all`
- `query_operation_logs`
- wiki 写操作相关工具

## 分阶段方案

### Phase 0：MCP 验收基线

目标：先固定 MCP-first 的验收标准，避免后续重构只改善 GUI 或内部服务，无法证明 Agent 可用。

范围：

- 建立 MCP contract tests，覆盖核心读写工具。
- 建立 schema snapshot，记录每个工具的参数和返回 envelope。
- 所有读工具测试：
  - 返回 JSON-compatible payload。
  - 失败返回 `ok=false` 和稳定 error code。
  - 大结果返回分页/截断信息。
- 所有写工具测试：
  - 正常执行。
  - `dry_run` 不改变数据库。
  - 写入 operation log。
  - 错误路径返回 Agent-readable error。

验收：

- `pytest tests/test_mcp_contract.py -q`
- `pytest tests/test_operation_safety.py -q`
- 新增测试先红后绿，不以人工 GUI 验证替代 MCP 测试。

### Phase 1：MCP Tool Contract 标准化

目标：统一 MCP 工具返回格式，消除 Agent 解析不稳定。

范围：

- 增加 envelope helper，统一 `ok/data/meta/error/operation_id/dry_run/truncated`。
- 将 `structured_query`、`explain_query`、`graph_traverse` 从 JSON 字符串改为 dict envelope。
- `search`、`search_fulltext`、`list_knowledge`、`tags` 增加分页 meta。
- `read` 不再直接抛异常；不存在时返回 `ok=false`。
- 新增 `kb_capabilities`：
  - 当前工具清单。
  - 数据库、向量、FTS、图谱、异步任务能力。
  - payload 限制。
  - 推荐调用顺序。

验收：

- 所有 MCP 工具返回 JSON-compatible dict/list。
- 旧工具名全部保留。
- 失败路径有稳定 `error.code`，例如 `NOT_FOUND`、`VALIDATION_ERROR`、`PERMISSION_DENIED`、`INGEST_FAILED`。

### Phase 2：MCP RAG 完全体

目标：让 `ask` 结果不只是自然语言答案，而是 Agent 可继续推理和溯源的结构化对象。

`ask` 新参数：

```python
include_graph: bool = True
include_context: bool = True
max_sources: int = 5
max_graph_nodes: int = 50
```

`ask` 返回 `data`：

```json
{
  "answer": "...",
  "sources": [
    {
      "knowledge_id": "...",
      "block_id": "...",
      "title": "...",
      "text_preview": "...",
      "score": 0.91,
      "source_path": "..."
    }
  ],
  "source_graph": {
    "nodes": [],
    "edges": [],
    "truncated": false
  },
  "route": {
    "mode": "hybrid",
    "reason": "fallback to hybrid search"
  },
  "query_plan": {
    "steps": [],
    "tools": []
  },
  "block_contexts": [],
  "warnings": []
}
```

范围：

- `sources` 必须定位到 Block，而不是只定位到知识条目。
- `source_graph` 默认只返回局部图，限制节点和边数量。
- `block_contexts` 包含父链、当前 Block、相邻兄弟、链接摘要。
- RAG pipeline 将 route 和 query_plan 写入结果 metadata。
- `structured_query` 和 `graph_traverse` 的结果可直接作为 `ask_with_query` 输入上下文。

验收：

- `ask` 始终返回 `answer/sources/source_graph/route/query_plan/block_contexts/warnings`。
- 每个 source 至少包含 `knowledge_id`、`block_id`、`title`、`text_preview`。
- source graph 超过限制时 `truncated=true`。

### Phase 3：MCP Agentic Query / Graph Query

目标：收敛查询入口，让 Agent 能先解释、再执行、再问答。

新增工具：

- `route_query(question)`：只路由和解释，不执行。
- `execute_query(query_spec)`：执行 DSL。
- `ask_with_query(question, query_spec)`：用显式 DSL 控制 RAG。

路由优先级：

1. 显式 DSL 直接执行。
2. 含 `#tag` / `::property` / `[[link]]` 走结构化查询。
3. 关系、引用、多跳类问题走图遍历。
4. 模糊总结类问题走 hybrid。

`explain_query` 返回：

```json
{
  "matched_filters": [],
  "expanded_tags": [],
  "link_targets": [],
  "sql_summary": {},
  "graph_traversal": {},
  "fallback_reason": ""
}
```

范围：

- `structured_query` 默认 `include_blocks=true` 时返回 Block 级结果。
- `graph_traverse` 支持 `max_depth`、`max_nodes`、`limit`、`offset`。
- `execute_query` 接收统一 `query_spec`：
  - `type=structured`
  - `type=graph`
  - `type=hybrid`
  - `type=compound`

验收：

- 逻辑查询不调用向量检索。
- 图查询不返回全库图，只返回 bounded local graph。
- Agent 可以调用 `route_query -> execute_query -> ask_with_query` 完成可解释问答。

### Phase 4：MCP 写操作安全闭环

目标：所有 MCP 写操作可预览、可审计、尽量可恢复。

所有写工具支持 `dry_run`：

- `create`
- `update`
- `delete`
- `ingest_file`
- `ingest_url`
- `reindex_all`
- `save_to_wiki`
- wiki workflow
- wiki restore
- property schema mutation
- tag relation mutation

高风险操作返回 `operation_id`：

- update
- delete
- purge
- reindex
- batch 操作
- schema mutation
- tag relation mutation

新增工具：

- `preview_operation(operation, payload)`
- `get_operation_log(operation_id)`
- `list_recent_operations(limit, source, target_type)`
- `undo_operation(operation_id)`

审计字段：

- `source="mcp"`
- `operator`
- `target_type`
- `target_id`
- `snapshot_before`
- `snapshot_after`
- `diff`
- `created_at`

删除策略：

- `delete` 默认 soft delete。
- `purge` 必须显式调用。
- `undo_operation` 至少恢复 update/delete 的主要场景。

验收：

- 每次 MCP 写操作后，Agent 可用 `operation_id` 查询完整审计记录。
- `dry_run=True` 不改变数据库、文件图谱或索引。
- delete 后可恢复，purge 后不可恢复且必须有明确 warning。

### Phase 5：MCP 大文件与异步任务

目标：大 Excel / PDF / DOCX 导入不阻塞 GUI，也不让 MCP 调用超时或返回不可消费 payload。

策略：

- 小文件 `ingest_file` 可同步返回受控结果。
- 大 Excel / PDF / DOCX 默认返回 `job_id`。
- Agent 用 job 工具轮询，不依赖 GUI 状态。

统一 job 工具：

- `create_ingest_job`
- `get_job`
- `list_jobs`
- `cancel_job`

导入结果包含：

- `created_items`
- `skipped_items`
- `failed_items`
- `sheet_count`
- `page_count`
- `block_count`
- `operation_id`

大文件判定：

- 文件大小超过配置阈值。
- Excel sheet 数或总行数超过配置阈值。
- PDF 页数超过配置阈值。
- DOCX 段落/表格数量超过配置阈值。

验收：

- 大 Excel 返回 `job_id`，不会同步解析到卡死。
- 每个 sheet 独立可追踪。
- job 完成后可通过 `operation_id` 查询导入审计。
- failed item 不影响已成功 sheet/page 的审计记录。

### Phase 6：Embedding-time Contextual Headers

目标：提升向量召回质量，同时保持原文存储纯净。

新增配置：

```yaml
rag:
  embedding_context:
    enabled: true
    include_parent_chain: true
    include_links: true
    include_siblings: false
    max_chars: 1200
```

策略：

- `blocks.content` 始终保存原文。
- embedding text 动态构造：

```text
父链: ...
关联知识: ...
当前内容: ...
```

- `reindex_all(dry_run=True)` 返回：
  - `affected_items`
  - `affected_blocks`
  - `embedding_context_enabled`
  - `estimated_batches`

`read` 增强：

- `include_blocks`
- `include_embedding_preview`
- `include_effective_properties`
- `include_linked_summaries`

验收：

- 向量化文本包含父链/链接摘要。
- `blocks.content` 不被 contextual header 污染。
- Agent 能通过 `read(..., include_embedding_preview=True)` 判断召回为何命中。

### Phase 7：MCP-first 文档与 Prompt 契约

目标：让 Agent 不是“知道有工具”，而是知道“如何安全、可解释地调用工具”。

新增文档：

- `docs/mcp/agent-usage.md`
- `docs/mcp/tool-contract.md`
- `docs/mcp/query-dsl.md`
- `docs/mcp/safety-and-undo.md`
- `docs/mcp/ingest-jobs.md`

新增 MCP prompt 模板：

- `kb_agent_research`
- `kb_safe_update`
- `kb_import_and_verify`
- `kb_query_with_sources`

推荐调用范式：

研究：

```text
kb_capabilities -> route_query -> execute_query/ask -> get_source_graph -> read
```

安全更新：

```text
read -> preview_operation -> update(dry_run=true) -> update -> get_operation_log
```

导入校验：

```text
kb_capabilities -> create_ingest_job/ingest_file -> get_job -> structured_query -> ask
```

溯源问答：

```text
route_query -> ask(include_graph=true, include_context=true) -> get_source_graph -> read
```

验收：

- Agent 只看 MCP 文档即可完成导入、查询、写入、审计和恢复。
- prompt 明确要求写操作前 dry_run，写操作后查 operation log。

## 测试计划

### MCP contract tests

- 所有工具返回 JSON-compatible payload。
- 所有失败路径返回 `ok=false` 和稳定 error code。
- 大结果返回 `truncated=true` 和分页信息。
- schema snapshot 能捕获工具参数或返回字段破坏性变化。

### RAG tests

- `ask` 返回 answer、sources、source_graph、route、query_plan。
- source 定位到 Block。
- 逻辑查询不调用向量检索。
- source_graph 节点能定位到 Block 和 Page。

### Safety tests

- 每个 MCP 写操作写入 operation log。
- dry_run 不改变数据库。
- delete soft delete，purge 才硬删。
- undo_operation 能恢复 update/delete 主要场景。

### Ingest tests

- 大 Excel 返回 job_id 或受控同步结果。
- 每个 sheet 独立可追踪。
- 导入后 LinkDiscovery 和 BlockContext 可用。
- 导入失败有 failed_items，不吞错。

### Regression

```bash
pytest tests -q
```

重点回归：

- `tests/test_mcp_server.py`
- `tests/test_operation_safety.py`
- `tests/test_full_pipeline_e2e.py`
- `tests/test_query_revolution_phase3.py`
- `tests/test_api.py`

## 验收定义

项目达到 MCP-first 完全体时，应满足：

1. Agent 可以通过 `kb_capabilities` 理解可用能力、限制和推荐流程。
2. 所有 MCP 工具返回 envelope，失败不需要解析异常字符串。
3. `ask` 返回可继续消费的结构化 RAG payload。
4. structured / graph 查询保留 Block 粒度。
5. 所有写操作可 dry_run、可审计，并返回 `operation_id`。
6. delete 默认 soft delete，主要误操作可 undo。
7. 大文件导入走 job，不阻塞、不假死。
8. 大图谱和大查询严格分页/截断。
9. embedding context 可解释，且不污染原始 Block 内容。
10. MCP 文档和 prompt 模板能指导 Agent 按安全顺序调用工具。

## 推荐实施顺序

1. Phase 0 和 Phase 1 必须优先完成，因为它们决定后续所有 Agent 调用是否稳定。
2. Phase 2 和 Phase 3 可并行推进，但 `ask` 的 source/schema 应先定稿。
3. Phase 4 应在任何批量写操作扩展前完成。
4. Phase 5 优先解决 Excel/PDF/DOCX 大文件导入导致 GUI/MCP 卡死的问题。
5. Phase 6 在 contract 和 safety 稳定后实施，避免 reindex 风险不可追踪。
6. Phase 7 作为收口阶段，但 prompt 模板可以从 Phase 1 起同步维护。
