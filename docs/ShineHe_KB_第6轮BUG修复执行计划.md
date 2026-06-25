# ShineHe-KB 第6轮测试 — 13 项 BUG 完整修复执行计划

> **来源**：2026-06-25 第6轮 MCP 接口稳定性+召回测试（25 轮，40+ 工具）
> **服务版本**：v1.4.0 ｜ **测试人**：ZCode ｜ **状态**：已批准，待执行
> **测试框架**：pytest（`asyncio_mode=auto`，`testpaths=["tests"]`），每项 BUG 配回归测试
> **已确认决策**：#13=查询时过滤 ｜ #10=保留自动发布+加调用参数 ｜ 范围=全部 13 个，分 3 阶段

## 目录

- [总览](#总览)
- [阶段 P0 — 数据正确性（4 项，必须最先修）](#阶段-p0--数据正确性)
- [阶段 P1 — 体验（4 项）](#阶段-p1--体验)
- [阶段 P2 — 健壮性（5 项）](#阶段-p2--健壮性)
- [执行顺序与验收](#执行顺序与验收)
- [改动文件清单](#改动文件清单去重)

---

## 总览

| 阶段 | BUG | 主题 | 优先级 |
|---|---|---|---|
| P0 | #4, #13, #7, #8 | 数据正确性 / 核心功能 | 🔴 高 |
| P1 | #1, #2, #10, #12 | 查询与工具体验 | 🟡 中 |
| P2 | #5, #6, #9, #11, #3 | 健壮性 / 可观测性 | 🟢 低 |

每项 BUG 修复均包含：根因（含已核实文件:行号）→ 改动点 → 回归测试。

---

## 阶段 P0 — 数据正确性

### 🔴 BUG #4：memory 写后 recall 归零

**现象**：`remember_fact` 写入成功（返回 id + operation_id，`summarize_recent_changes` 能统计到，证明数据已落库），但紧接着的 `recall_facts` / `search_decisions` 返回 `[]`。

**根因**（深于初判，已核实）：
- `recall_facts`（`src/services/agent_memory.py:78-85`）→ `search_fts`（`src/repositories/agent_memory_repo.py:134-151`）把**原始 query 串**直接喂给 FTS5 `MATCH ?`。
- 与 `knowledge_fts` 不同，agent_memory FTS 路径**未走 `sanitize_fts_query` + jieba 分词**。CJK 无空格、特殊字符（`:*"`）或 FTS5 语法词会让 MATCH 抛异常（被 try/except 吞掉→走 LIKE）或返空（→走 LIKE 兜底）。
- LIKE 兜底（`agent_memory_repo.py:153-160` `search_like`）用 `pattern = f"%{query}%"` 做**整串连续子串匹配**；当 query 是多词组合（如 `"stability_test 稳定性测试标记"`）而 value 文本中并非连续出现时，LIKE 同样不命中 → 最终返空。
- 现有的 BUG-4 LIKE 兜底（测试 `test_recall_facts_falls_back_when_fts_returns_no_rows`）只覆盖了「FTS 返空」这一触发条件，未覆盖「LIKE 也因多词整串匹配失败」的二段失败。

**改动**：
1. `src/repositories/agent_memory_repo.py` — `search_fts()`（~L134-151）
   - query 先过 `sanitize_fts_query(query, is_tokenized=True)`（复用 `src/utils/chinese_tokenizer.py`），转成 OR 词项 FTS 查询，而非 raw 整串。
2. `src/repositories/agent_memory_repo.py` — `search_like()`（~L153-160）
   - 把 `pattern = f"%{query}%"` 改为**分词后逐词 OR**：`jieba.cut(query)` 后，任一词 `LIKE %词%` 命中即返回（`WHERE value LIKE ? OR key LIKE ? ...` 按词拼接）。
3. `src/services/agent_memory.py` `recall_facts()`（~L78-85）：现有 try/except + 空兜底逻辑保留，配合上面两项改动即可全覆盖。

**回归测试**：`tests/test_agent_memory.py` 加 `test_recall_after_write_multiterm_query`
- 写一条多词 value；用不含原样子串的多词 query 召回 → 应返回该条（当前会返空）。

---

### 🔴 BUG #13：软删除内容泄漏到搜索（标题"未知"孤儿）

**现象**：`delete` 软删后，`search` / `search_fulltext` 仍返回该条目的 blocks/vectors，标题显示为"未知"（孤儿）。

**根因**（已核实）：
- `soft_delete_knowledge`（`src/services/db.py:957`）只置 `knowledge_items.deleted_at`，**不动** `blocks` / `vec_blocks` / `vec_chunks` / `block_fts` / `chunk_fts`。
- 搜索查询全部不过滤 `deleted_at`：
  - `BlockStore.search`（`src/services/block_store.py:154`）向量查询 `vec_blocks JOIN blocks`，无 `knowledge_items` JOIN。
  - `VectorStore.search`（`src/services/vectorstore.py:108`，legacy）同上。
  - `search_blocks_fts`（`src/services/db.py:1375`）`block_fts JOIN blocks`，无过滤。
  - `search_chunks_fts`（`src/repositories/knowledge_repo.py:399`）`chunk_fts JOIN knowledge_chunks`，无过滤。
- "未知"来源：`SearchService.search`（`src/services/search_service.py:173`）对软删条目 `get_knowledge` 返回 None → title 回退为字面量 `"未知"`，但 block 仍被 `BlockStore.search` 返回。
- 唯一正确的是 item 级 FTS（`db.py:860 search_knowledge`）已加 `deleted_at IS NULL`。

**改动**（按决定 = 查询时过滤）：
1. `src/services/block_store.py:154` `BlockStore.search` — `JOIN blocks b` 后追加 `JOIN knowledge_items ki ON ki.id = b.page_id` + `AND ki.deleted_at IS NULL`。
2. `src/services/vectorstore.py:108` `VectorStore.search`（legacy）— 同样加 JOIN + 过滤。
3. `src/services/db.py:1375` `search_blocks_fts` — blocks → 再 JOIN knowledge_items + 过滤。
4. `src/repositories/knowledge_repo.py:399` `search_chunks_fts` — knowledge_chunks 已关联 knowledge_items，补 `AND deleted_at IS NULL`。

> 注：保留软删可逆性——`restore_knowledge` 无需重建 blocks（块仍在，仅被过滤），与 undo 链路解耦。

**回归测试**：`tests/` 新增 `test_search_excludes_soft_deleted.py`
- 创建条目 → 确认 search/search_fulltext 命中 → soft delete → 确认两个搜索均不再返回其 blocks（当前会以"未知"标题返回）。

---

### 🔴 BUG #7：file_ingest / url_ingest 任务 "No handler"

**现象**：`create_ingest_job` 创建的 `file_ingest` / `url_ingest` 任务立即失败：`"No handler for file_ingest"`。

**根因**（已核实，非字符串不匹配）：
- handler 注册正确（`src/services/async_tasks.py:578-591 register_all_tasks`），`create_ingest_job` 发出的 job_type（`mcp_server.py:1528,1541`）与注册 key 完全一致。
- 但注册触发方式是 **模块加载副作用**：`async_tasks.py:591` 在模块末尾调用 `register_all_tasks()`。
- `AsyncWorker.start()`（`src/services/async_worker.py:91-104`）用一个 **bare `except Exception` 吞掉的 import** 来触发注册：

  ```python
  try:
      import src.services.async_tasks  # noqa
  except Exception as exc:
      logger.error("...")   # 静默吞掉，不中止启动
  ```

- 若该 import 失败（或 worker 经由绕过 `start()` 的路径启动），`TaskRegistry._handlers` 永远为空 → 所有认领的任务置 FAILED。

**改动**：
1. `src/services/async_worker.py:91-104` `start()` — 改为**显式调用**：

  ```python
  from src.services.async_tasks import register_all_tasks
  register_all_tasks()   # 不再用 import 副作用
  ```

   **移除 bare except**——注册失败应 fail-fast 抛异常中止启动，而非静默吞掉后让所有任务失败。
2. （可选加固）`async_worker.py:159` `_execute_job`：`get_handler` 返回 None 时，先兜底再调一次 `register_all_tasks()`，仍 None 才置 FAILED（防御 worker 长运行中注册丢失的极端情况）。

**回归测试**：`tests/test_async_ingest.py` 加 `test_worker_start_registers_handlers_explicitly`
- 构造未触发 `async_tasks` import 的环境启动 worker → 断言 `TaskRegistry.get_handler("file_ingest")` / `"url_ingest"` 非 None。

---

### 🔴 BUG #8：reindex_checkpoint 僵尸任务（processing >24h, started_at NULL）

**现象**：`async_jobs` 表中 `id='reindex_checkpoint'` 的行卡在 `status='processing'` 超 24 小时，`started_at` 为 NULL。

**根因**（已核实）：
- `_save_reindex_checkpoint`（`src/services/indexer.py:407-418`）：
  - 用非枚举 status `'processing'`（合法枚举为 `pending/running/completed/failed/cancelled`）。
  - 只写 `(id, job_type, status, params, created_at)`，**不设 `started_at`** → NULL。
  - 用固定 id `'reindex_checkpoint'` + `INSERT OR REPLACE`，reindex 循环每 10 条覆盖一次（`indexer.py:381`）。
- `reindex_all` 的 `finally`（`indexer.py:393-395`）**只恢复 journal mode，从不清除**该行 → 永久残留。
- 全库**无 stuck-job reaper / TTL**：`claim_next_pending_job`（`db.py:2047`）只认 `status='pending'`，`'processing'`/`'running'` 卡死的任务永不被回收。

**改动**：
1. `src/services/indexer.py` `reindex_all`（~L283-404）`finally` 块追加：`DELETE FROM async_jobs WHERE id = 'reindex_checkpoint'`（成功/失败都清，成功后保留无意义）。
2. `_save_reindex_checkpoint`（~L407）：status `'processing'` → 合法的 `'running'`，并补 `started_at`（即使忘清也能被 reaper 识别）。
3. 新增 reaper：`src/services/db.py` 加 `reclaim_stuck_jobs(timeout_hours=6)` —— 把 `status='running'` 且 `started_at < now - timeout` 的任务回退为 `pending`（或标 failed）；`AsyncWorker.start()` 启动时调用一次，清理上次进程崩溃遗留的 running 任务。

**回归测试**：`tests/test_async_ingest.py` 加
- `test_reindex_checkpoint_cleared_on_completion`：reindex 完成后断言 checkpoint 行已删除。
- `test_reclaim_stuck_jobs`：构造一个 started_at 早于 timeout 的 running 任务 → 调 `reclaim_stuck_jobs` → 断言其被回退。

---

## 阶段 P1 — 体验

### 🟡 BUG #1：structured_query 多词 fulltext 返空

**现象**：`{"filter": {"fulltext": "CDN 教材"}}` 返 `[]`，而 `{"fulltext": "教材"}` 正常。

**根因**（已核实）：`src/services/query_executor.py:185` `_compile_fulltext` 调 `sanitize_fts_query(condition.value)` **未传 `is_tokenized=True`**，多词 `"CDN 教材"` 被包成 phrase 查询 `"CDN 教材"`（要求相邻有序）。`knowledge_fts` 用 `unicode61`（不切中文），相邻 token 不存在 → 不命中。

**改动**：`src/services/query_executor.py:185` 改为 `sanitize_fts_query(condition.value, is_tokenized=True)`，多词转 OR 词项；验证 `knowledge_fts` 下 CJK 多词能命中。

**回归测试**：`tests/test_query_revolution_phase3.py` 加 CJK 多词用例（`fulltext: "CDN 教材"` 应返回含两词的条目）。

---

### 🟡 BUG #2：structured_query 忽略 limit

**现象**：DSL 传 `{limit:3}` 但不传 tool 的 `limit` 参数时，返回 meta 显示 `limit:100`。

**根因**（已核实）：`src/mcp_server.py:2053` tool 参数 `limit` 默认 100，`spec.limit = min(spec.limit, limit)` 后执行用 `spec.limit`(3)，但 `mcp_server.py:2059-2065` 的 meta 与 `has_more` **报告的是 tool 的 `limit`(100)** 而非 `spec.limit`。

**改动**：`src/mcp_server.py` `structured_query`（~L2053-2065）：meta 的 `limit=` 改报 `spec.limit`；`has_more` 用 `spec.limit` 比较；DSL 未给 limit 时尊重 spec 自身默认（不强制覆写）。

**回归测试**：`tests/` 加 `test_structured_query_meta_reports_dsl_limit` —— DSL `{limit:3}` 不传 tool limit 参数 → 断言 `meta.limit==3` 且返回 ≤3 条。

---

### 🟡 BUG #10：save_to_wiki 直接 published（按决定 = 加调用参数）

**现象**：`save_to_wiki` 新建页面状态为 `published`，导致 `wiki_submit_review`（需 draft）报 `Cannot submit from status: published`。

**根因**（已核实）：`Config.get("wiki.auto_publish", True)` 默认 True，`wiki_compiler.py:297 save_answer` 一律 `initial_status="published"`，与工作流 `submit_for_review`（`wiki_workflow.py:67`，要求 draft）冲突。

**改动**（保留默认行为，加显式控制）：
1. `src/mcp_server.py` `save_to_wiki`（~L1675）：新增可选参数 `auto_publish: bool | None = None`（None = 沿用 Config 默认），透传给 `save_answer`。
2. `src/services/wiki_compiler.py` `save_answer`（~L297）：

  ```python
  _auto = auto_publish if auto_publish is not None else Config.get("wiki.auto_publish", True)
  initial_status = "published" if _auto else "draft"
  ```

3. 调用方可 `save_to_wiki(q, a, auto_publish=False)` 走 draft→review 流；默认仍自动发布（向后兼容）。

**回归测试**：`tests/` 加 `test_save_to_wiki_with_auto_publish_false_creates_draft`。

---

### 🟡 BUG #12：无 wiki / memory 删除工具

**现象**：`delete` 仅作用于 `knowledge_items`；wiki 页面与 agent_memory 条目无法通过 MCP 删除（本轮测试遗留无法清理）。

**根因**（已核实）：`WikiRepository.delete_page`（`wiki_repo.py:57-62`）与 `AgentMemoryRepository.delete` / `delete_by_key`（`agent_memory_repo.py:71-83`）**已存在且单测覆盖**，只是未暴露为 MCP 工具。

**改动**：`src/mcp_server.py` 新增 2 个工具：
1. `delete_wiki_page(page_id)` — group=`wiki`，side_effect=`destructive`，`requires_confirmation=true` → 调 `container.wiki_repo.delete_page` + 记 operation_log。
2. `delete_memory(item_id=None, key=None)` — group=`memory` → 调 `AgentMemoryRepository.delete` / `delete_by_key` + 记 operation_log。
3. 更新 `kb_capabilities` / `tool_metadata` 元数据与别名表。

**回归测试**：`tests/` 加 `test_delete_wiki_page_tool` + `test_delete_memory_tool`。

---

## 阶段 P2 — 健壮性

### 🟢 BUG #5：summarize 泄露 `<think>` 标签

**根因**：`src/services/agent_memory.py:272-281` `_generate_change_summary` 直接把 LLM 返回赋给 `summary`，未过 `strip_think`（工具 `src/utils/llm_text.py:5` 已存在，rag_pipeline / query_rewriter / graph_builder 等多处复用）。

**改动**：
- `agent_memory.py` import `strip_think` 包裹 `_generate_change_summary` 返回值。
- 同时修 `_extract_tasks_llm`（~L196-203）JSON 解析前的 think 清理。

**回归测试**：`tests/test_agent_memory.py` 加 `test_summarize_strips_think_tags`（mock LLM 返回含 `<think>`，断言 summary 无残留）。

---

### 🟢 BUG #6：restore 后 quality 丢失

**现象**：原 `quality:"ok"` 的条目 delete→restore 后 `quality` 变空。

**根因**（已核实）：
- delete 的 operation_log snapshot（`mcp_server.py:937-943`）只存 `title/tags/content_preview/source_type/file_type`，**不含 quality**。
- `restore_knowledge`（`db.py:1011`）只清 `deleted_at`；`undo` 的 `restore_delete` 分支（`operation_log.py:187`）同样不碰 quality。
- `quality` 从不被版本化（`_save_version` db.py:1178 / knowledge_repo.py:266 只存 `title/content/tags`）。

**改动**（保守：保证有值不丢失，空值属正常不强制回填）：
- `src/mcp_server.py` delete 工具的 `_op_log before` snapshot（~L937）加入 `quality` / `quality_score`（从 `get_knowledge` 读）。
- restore 路径：restore 后用 snapshot 中的 quality 回填（`db.py:1011 restore_knowledge` 或 undo 的 restore_delete 分支 `operation_log.py:187`）。

**回归测试**：`tests/test_undo_operation.py` 加 `test_restore_preserves_quality` —— 创建带 `quality=ok` 条目 → delete → restore → 断言 quality 仍为 "ok"。

---

### 🟢 BUG #9：get_trace token=0（双层）

**现象**：`get_trace` 各阶段 `input_tokens=0` / `output_tokens=0`。

**根因**（已核实，双层）：
1. `LLMService.chat`（`src/services/llm.py:134-164`）**丢弃** `response.usage`（只 `return response.choices[0].message.content`）。
2. `rag_pipeline.py:960-978` 构造 `StageTrace` 时**从不传** `input_tokens/output_tokens`，全用 dataclass 默认 0。
3. rerankers（`rerankers/api.py`、`rerankers/llm.py`）同样不取 usage。

**改动**：
1. `src/services/llm.py`：`chat()` 改为返回 `(content, usage_dict)`，或新增 `chat_with_usage()` 保留 `response.usage`（prompt_tokens/completion_tokens）。**注意向后兼容**：保留 chat 旧返回 content 的契约（用新方法或同步更新所有调用点）。
2. `src/services/rag_pipeline.py:960`：generate 阶段 StageTrace 用拿到的 usage 填 `input_tokens/output_tokens`；rerank 阶段若 reranker API 返回 usage 同样填。

**回归测试**：`tests/` 加 `test_trace_records_llm_tokens` —— mock LLM 返回 usage → 断言 trace generate 阶段 token > 0。

---

### 🟢 BUG #11：save_to_wiki 自动增强（正面特性，加可关闭开关）

**现象**：`save_answer`（`wiki_compiler.py:286-318`）每次必调 LLM（QUERY_SAVE_PROMPT）增强内容（补背景、规范化表格、生成 concept_summary、推断 tags），无跳过开关。

**改动**（与 #10 一并）：
- `src/services/wiki_compiler.py` `save_answer` 加 `enhance: bool = True` 参数；False 时跳过 LLM，直接用原始 answer 存（title 取 question 前 N 字，tags 空，concept_summary 空）。
- `save_to_wiki` MCP 工具透传该参数。
- 补文档说明增强行为。

**回归测试**：`tests/` 加 `test_save_to_wiki_skip_enhance_stores_raw`。

---

### 🟢 BUG #3：hybrid 检索空时 fallback（已确认正面容错，仅补文档）

**现象**：`ask_with_query` 出现 `hybrid_search_empty_fallback_to_fts` 警告。

**现状**：行为正确——hybrid 为空时已自动降级到 FTS（优雅容错）。

**改动**：不改逻辑；仅在文档/注释说明该警告含义（非阻塞，检索已自动降级）。可选：优化 warning 文案为更明确提示。

---

## 执行顺序与验收

| 阶段 | BUG | 关键文件 | 预估测试数 |
|---|---|---|---|
| P0 | #4, #13, #7, #8 | agent_memory_repo, block_store, vectorstore, db, 查询路径, async_worker, async_tasks, indexer | 6-8 |
| P1 | #1, #2, #10, #12 | query_executor, mcp_server(structured_query/save_to_wiki/新工具), wiki_compiler | 5-6 |
| P2 | #5, #6, #9, #11, #3 | agent_memory, llm, rag_pipeline, wiki_compiler, mcp_server | 5 |

**每阶段结束**：运行 `pytest tests/ -q` 全量回归，确保无新破坏。

**全部完成后**：
1. 清理本轮测试遗留的 wiki 页面（deprecated 状态）+ memory 条目（借助新增的 #12 工具）。
2. 处理 #13 修复前的历史孤儿 blocks：修复后搜索不再泄漏，但存量孤儿需手动清或触发一次 reindex（注意此时 reindex checkpoint 已被 #8 修复，可安全使用）。

---

## 改动文件清单（去重）

| 文件 | 涉及 BUG |
|---|---|
| `src/repositories/agent_memory_repo.py` | #4 |
| `src/services/agent_memory.py` | #4, #5 |
| `src/services/block_store.py` | #13 |
| `src/services/vectorstore.py` | #13 |
| `src/services/db.py` | #13, #8(reaper) |
| `src/repositories/knowledge_repo.py` | #13 |
| `src/services/async_worker.py` | #7, #8(reaper 调用) |
| `src/services/async_tasks.py` | #7(显式注册) |
| `src/services/indexer.py` | #8 |
| `src/services/query_executor.py` | #1 |
| `src/mcp_server.py` | #2, #6, #9(trace), #10, #11, #12, 新工具 |
| `src/services/wiki_compiler.py` | #10, #11 |
| `src/services/llm.py` | #9 |
| `src/services/rag_pipeline.py` | #9 |
| `tests/` 多个新增/扩展 | 全部 |
