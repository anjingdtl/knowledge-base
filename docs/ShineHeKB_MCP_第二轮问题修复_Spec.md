# ShineHeKB MCP 第二轮问题修复 Spec

**依据报告：** `mcp-stability-round2-2026-07-15.md`  
**基线版本：** ShineHeKB v1.10.2  
**基线 Commit：** `89e41b9fcdf42f8c31b3be6cb71b7d9fc0b629f3`  
**本地目录：** `D:\ClaudeCodeWorkSpace\projects\knowledge-base`  
**目标分支：** `fix/mcp-stability-round2`

> 基线统计以报告总表为准：34/77，首次成功率 44.2%。报告后文出现的 37.9% 不作为验收基线。

---

## 1. 修复目标

本轮必须完成：

1. 修复 Graph 分页、截断、悬空边和错误路径。
2. 修复 `execute_query(type="graph")` 的 limit/offset/next_offset/truncated。
3. 补齐 `graph_traverse` 输入校验。
4. 修复 `route_query` 的可执行下游契约和无 LLM 降级。
5. 修复 `ask`、`ask_with_query` 的真实硬超时及线程泄漏。
6. 统一 stdio 与 streamable-http 的工具集合和参数校验。
7. 修复无答案判断、数字单位混淆和短语上下文错误。
8. 修复 URL 导入错误诊断及其余 P2 边界问题。
9. 完成并发、长稳、准确性和全量回归验收。

---

## 2. 强制规则

### 2.1 开发规则

- 禁止直接修改 `master`。
- 每个 Phase 至少一个独立 commit。
- 每个缺陷必须先写失败测试，再修改生产代码。
- 禁止删除失败测试、放宽断言、更新基线、使用 `skip/xfail` 掩盖问题。
- 不允许只修改工具说明而不修复实际行为。
- 所有关键能力必须通过真实 MCP Transport 验证，不能只测 Python 函数。

推荐提交顺序：

```text
test(mcp): add round2 failing regression coverage
fix(graph): enforce consistent bounded pagination
fix(router): return executable route recommendations
fix(rag): enforce hard timeout without thread leaks
fix(mcp): align stdio and http tool exposure
fix(search): improve no-answer and numeric-unit matching
fix(ingest): preserve structured http diagnostics
fix(mcp): close remaining validation gaps
test(stability): complete round2 acceptance gates
docs: record round2 repair results
```

### 2.2 数据安全

- 所有写测试使用临时数据库。
- 禁止修改正式 `data/kb.db`。
- 清理 `STABILITY_TEST_` memory 前必须精确列出记录，禁止模糊批量删除。
- 日志和报告不得包含 API Key、Token、Cookie 或完整敏感内容。

### 2.3 兼容性

- 保持旧工具名和 envelope 格式稳定。
- `graph_traverse` 继续兼容 JSON 数组字符串。
- `tags()` 无参数行为保持兼容。
- 破坏性变更必须先提供迁移方案。

---

## 3. 阶段门禁

| Phase | 优先级 | 内容 | Gate |
|---|---:|---|---|
| 0 | Gate | 基线与失败回归测试 | 未完成不得改生产代码 |
| 1 | P0 | Graph 分页与一致性 | 必须 100% 通过 |
| 2 | P1 | Graph 参数校验 | 必须 100% 通过 |
| 3 | P1 | Route 契约与无 LLM 降级 | 必须 100% 通过 |
| 4 | P1 | 硬超时和线程回收 | 必须 100% 通过 |
| 5 | P1 | Transport 工具集一致性 | 必须 100% 通过 |
| 6 | P1 | 检索准确性与 no-answer | 达到量化门槛 |
| 7 | P2 | Ingest 与剩余边界 | 必须 100% 通过 |
| 8 | Gate | 全量回归、并发、长稳 | 最终验收 |

---

# Phase 0：建立失败基线

## 4. 必做事项

记录：

- Git SHA；
- Python/FastMCP/SQLite 版本；
- stdio 与 HTTP 工具列表；
- 当前线程数和 RSS；
- 当前 P50/P95/P99；
- 临时测试数据库路径。

新增或补充：

```text
tests/stability/test_graph_validation.py
tests/stability/test_graph_pagination.py
tests/stability/test_execute_graph_pagination.py
tests/stability/test_route_execution_contract.py
tests/stability/test_real_timeout.py
tests/stability/test_transport_tool_parity.py
tests/stability/test_search_no_answer.py
tests/stability/test_search_numeric_units.py
tests/stability/test_ingest_error_contract.py
tests/stability/test_remaining_boundaries.py
```

保存：

```text
artifacts/stability-repair/baseline-results.json
artifacts/stability-repair/baseline-pytest.txt
artifacts/stability-repair/baseline-tools-stdio.json
artifacts/stability-repair/baseline-tools-http.json
```

### Phase 0 验收

- 报告中的所有 P0/P1 均有自动化失败测试。
- 测试在未修复代码上稳定失败。
- 正式数据库无变化。
- Phase 0 不得修改 `src/`。

---

# Phase 1：修复 Graph 分页与一致性

## 5. 涉及问题

- `graph_traverse` 只分页 nodes，edges/paths 仍返回全图。
- `execute_query(type="graph")` 的 limit 未生效。
- 非零 offset 被忽略。
- `next_offset` 缺失。
- `truncated` 判断错误。
- 工具层未将 limit 下沉为图服务 max_nodes。

## 6. 目标文件

优先检查：

```text
src/mcp/tools/graph.py
src/mcp/tools/retrieval.py
src/services/graph_traversal.py
```

允许新增共享模块：

```text
src/services/graph_pagination.py
```

## 7. 统一分页实现

建立共享函数：

```python
paginate_graph_result(result: dict, *, limit: int, offset: int) -> dict
```

两个 Graph 工具必须调用同一实现。

### 7.1 nodes

始终执行：

```python
page_nodes = all_nodes[offset:offset + limit]
```

不得只在 `len(nodes) > limit` 时处理 offset。

### 7.2 has_more

禁止：

```python
len(nodes) == limit
```

应使用真实 total，或多取一条：

```python
has_more = offset + len(page_nodes) < total_nodes
```

无法获得精确总数时，返回：

```text
total_estimate_is_exact=false
```

不得把当前页长度冒充总量。

### 7.3 edges

默认采用页内局部图语义：

```python
page_ids = {node_id(n) for n in page_nodes}
page_edges = [
    e for e in all_edges
    if edge_source(e) in page_ids and edge_target(e) in page_ids
]
```

普通 `edges` 中不得出现 source/target 不在当前 nodes 的记录。

需要跨页引用时，另设：

```text
external_node_refs
```

### 7.4 paths

只保留全部节点都在当前页的 path：

```python
page_paths = [
    p for p in all_paths
    if all(node_id in page_ids for node_id in p)
]
```

### 7.5 服务层 limit

图服务请求至少获取：

```text
offset + limit + 1
```

且不得超过：

```text
rag.max_graph_nodes
```

`GraphTraversalService.traverse` 必须真正执行最大节点约束。

## 8. 返回契约

data：

```json
{
  "nodes": [],
  "edges": [],
  "paths": [],
  "truncated": false
}
```

meta：

```json
{
  "limit": 5,
  "offset": 0,
  "next_offset": 5,
  "total_estimate": 12,
  "total_estimate_is_exact": true
}
```

## 9. Phase 1 测试

覆盖：

```text
total=8, limit=10, offset=5
total=10, limit=10, offset=0
total=12, limit=5, offset=0
total=12, limit=5, offset=5
total=12, limit=5, offset=10
total=12, limit=5, offset=20
```

断言：

- nodes 数量正确；
- 页间无重复、合并无遗漏；
- `truncated/next_offset` 正确；
- 所有 edge 两端都在 nodes；
- 所有 path 节点都在 nodes；
- 两个 Graph 工具结果一致；
- `limit=5` 不得返回超过 5 个节点。

### Phase 1 验收

- 两个 P0 全部关闭。
- 无悬空边和跨页路径。
- stdio/http 实际调用均通过。

---

# Phase 2：Graph 输入校验

## 10. `start_ids`

兼容：

```python
start_ids: str | list[str]
```

规则：

1. 字符串按 JSON 数组解析；
2. 解析后必须为 list；
3. 数组非空；
4. 每项必须是非空字符串；
5. trim 空格；
6. 去重并保持顺序；
7. 超过最大起点数返回验证错误。

错误必须为：

```text
VALIDATION_ERROR
```

不得把类型错误归为 `QUERY_PARSE_ERROR`。

## 11. 数值约束

```text
1 <= limit <= rag.max_graph_nodes
offset >= 0
0 <= max_depth <= rag.max_graph_depth
start_type in {"knowledge","block"}
```

非法值必须在进入图服务前被拒绝。

## 12. Schema

MCP Schema 应声明 minimum/maximum。未知参数必须报错，不能静默忽略。

### Phase 2 验收

- A 组 11/11 通过。
- 所有错误 code、message、details 稳定。
- stdio/http 行为一致。


---

# Phase 3：修复 `route_query` 可执行契约

## 13. 涉及问题

- 没有 `recommended_tool`；
- 没有 `recommended_arguments`；
- LLM 不可用时 graph/hybrid 全部降级为 structured；
- `file_type` 被错误映射为 property key=`type`；
- “标签为企微的所有文档”提取了错误 tag；
- 路由结果不能原样调用下游工具。

## 14. 目标文件

优先检查：

```text
src/mcp/tools/retrieval.py
src/services/agentic_router.py
src/models/query_dsl.py
```

## 15. 新返回契约

成功时至少返回：

```json
{
  "mode": "structured",
  "query_spec": {},
  "traverse": null,
  "recommended_tool": "execute_query",
  "recommended_arguments": {
    "type": "structured",
    "query_spec": {}
  },
  "recommended_flow": [
    {
      "tool": "execute_query",
      "arguments": {}
    }
  ],
  "routing_source": "rules",
  "fallback_used": true,
  "explanation": ""
}
```

### Structured

```text
recommended_tool=execute_query
```

参数必须可原样调用。

### Hybrid

```text
recommended_tool=ask_with_query
```

参数至少包含：

```json
{
  "question": "原问题",
  "search_query": "检索词"
}
```

禁止推荐：

```text
execute_query(type="hybrid")
```

### Graph

已解析出知识 ID 时：

```text
recommended_tool=graph_traverse
```

无法直接解析 ID 时，返回机器可读多步流程：

```json
{
  "recommended_tool": "search",
  "recommended_arguments": {
    "query": "实体名称"
  },
  "recommended_flow": [
    {
      "tool": "search",
      "arguments": {"query": "实体名称"},
      "output_binding": "knowledge_ids"
    },
    {
      "tool": "graph_traverse",
      "arguments": {
        "start_ids": "$knowledge_ids",
        "max_depth": 2
      }
    }
  ]
}
```

## 16. 无 LLM 降级规则

LLM 不可用时仍需规则分类。

### Graph 信号

```text
引用了哪些
与……有什么关联
关系
依赖
上下游
链接到
被哪些文档引用
```

### Hybrid 信号

```text
总结
分析
建议
未来怎么发展
主要问题
综合判断
对比
原因
```

### Structured 信号

```text
列出
所有
标签为
file_type
source_type
更新时间
创建时间
数量
```

Graph 信号不得自动降级为 structured。

## 17. 字段映射

“file_type 为 pdf”必须生成：

```json
{
  "filter": {
    "file_type": "pdf"
  }
}
```

不得生成 property key=`type`。

## 18. Tag 提取

对：

```text
标签为企微的所有文档
```

必须提取：

```text
企微
```

维护后缀停用规则：

```text
的所有文档
的文档
相关文档
全部内容
```

## 19. Phase 3 测试

至少 30 个参数化查询：

- structured 10；
- graph 10；
- hybrid 10；
- 至少 15 个在 LLM unavailable 下执行。

自动链路：

1. 调用 `route_query`；
2. 获取 `recommended_tool`；
3. 原样调用 `recommended_arguments`；
4. 首次成功；
5. 不出现 Schema 错误。

### Phase 3 验收

- E 组全部通过。
- 30 个案例首次执行成功率 100%。
- 无 LLM 时 graph/hybrid 分类正确率 >= 90%。
- 不再生成错误字段和错误 tag。

---

# Phase 4：修复硬超时与线程泄漏

## 20. 涉及问题

- 1 秒 timeout，`ask` 实际 21.9 秒；
- `ask_with_query` 实际 10.2 秒；
- 连续 5 次超时，线程数 27→52；
- 3 并发慢请求全部客户端超时；
- 当前临时 ThreadPoolExecutor 无法取消运行中的线程。

## 21. 目标文件

优先检查：

```text
src/mcp/tools/retrieval.py
src/services/rag_pipeline.py
src/application/retrieval_commands.py
src/services/answer_service.py
src/providers/*
src/clients/*
```

## 22. 架构要求

### 22.1 禁止临时线程池硬超时

不得继续使用：

```python
with ThreadPoolExecutor(max_workers=1) as pool:
    future = pool.submit(...)
    future.result(timeout=...)
```

Python 无法安全杀死正在执行的同步线程，context 退出还可能等待线程结束。

### 22.2 统一 Deadline

请求入口建立：

```python
deadline = time.monotonic() + total_timeout
```

每个阶段只获取剩余时间：

```python
remaining = deadline - time.monotonic()
```

检索、重排、LLM、引用构建不得各自重新获得完整 timeout。

### 22.3 Provider 层 timeout

所有 LLM/Embedding/Reranker 请求必须配置：

```text
connect_timeout
read_timeout
write_timeout
pool_timeout
total_timeout
retry_count
```

Provider timeout 不得超过当前剩余 deadline。

### 22.4 可取消执行

优先：

1. 将 verified 路径统一为 async；
2. 使用 `asyncio.timeout()` 或 `asyncio.wait_for()`；
3. 使用可取消的异步 HTTP 客户端；
4. timeout 时取消任务并等待资源清理；
5. 不创建新的后台线程。

对于不可取消的 legacy 同步调用：

- 优先改造成有 provider read timeout 的 async；
- 无法保证时使用可终止 worker process；
- timeout 后终止 process；
- 禁止继续用一次一线程方式托管。

### 22.5 超时返回

返回结构至少包含：

```json
{
  "route": {
    "mode": "timeout",
    "configured_timeout_ms": 1000,
    "elapsed_ms": 1100,
    "cancelled": true
  }
}
```

不能等待 20 秒后再返回“1 秒超时”。

## 23. 资源回收

timeout 后确认：

- HTTP response 关闭；
- async task 取消；
- executor/process 无残留；
- DB 连接释放；
- trace 正常结束；
- 后续 ping/search 可用。

## 24. Phase 4 测试

### 单次

配置 1 秒、模拟 10 秒慢响应：

```text
实际返回 <= 1.5 秒
```

### 连续 50 次

- 最终线程数相对基线增量 <= 2；
- 无持续增长；
- 无僵尸进程；
- ping/search 正常。

### 并发 10 次

- 每个请求在 timeout+20% 范围内返回；
- 服务不永久阻塞；
- 无线程爆炸；
- 后续调用正常。

### Phase 4 验收

- F 组全部通过。
- 1 秒 timeout 实际 <= 1.5 秒。
- 50 次连续 timeout 无线程泄漏。
- 并发 timeout 后服务正常。
- 未完成前禁止执行高并发真实 Provider 压测。

---

# Phase 5：统一 stdio 与 streamable-http 工具暴露

## 25. 涉及问题

- stdio 49 个工具；
- HTTP 97 个工具；
- HTTP 多出 48 个命名空间别名；
- 未知参数静默忽略；
- profile/alias 策略不一致。

## 26. 目标文件

检查：

```text
src/mcp/server.py
src/mcp/http_server.py
src/mcp/tool_registry.py
src/mcp/tool_profiles.py
src/services/mcp_launcher.py
```

## 27. 单一暴露源

两种 transport 必须调用同一个函数：

```python
get_exposed_tool_definitions(
    profile=profile,
    experimental_enabled=...,
    legacy_aliases_enabled=...,
    write_policy=...
)
```

禁止分别注册或分别过滤。

## 28. 别名策略

当：

```text
legacy_aliases_enabled=false
```

两种 transport 均不得暴露：

```text
kb.search
ops.ping
memory.remember
graph.traverse
```

显式开启时，两种 transport 同时暴露，并在 `kb_capabilities` 中声明。

## 29. 严格参数

未知参数必须返回 MCP/JSON-RPC 参数验证错误，建议开启 extra=`forbid` 或等价行为。

### Phase 5 验收

- 相同配置下 stdio/http 工具名集合完全一致。
- `tools/list` 连续 20 次稳定。
- 未知参数报验证错误。
- profile、experimental、write policy、legacy aliases 语义一致。
- L 组全部通过。


---

# Phase 6：修复无答案、数字单位和短语混淆

## 30. 涉及问题

- “60 米”错误匹配“60珠/米”；
- “6个月无互动”错误匹配“6个月试用期”；
- 无答案准确率 0%；
- Recall@5 75%；
- 数字单位准确率 67%。

## 31. 目标文件

优先检查：

```text
src/services/search.py
src/services/hybrid_search.py
src/services/reranker.py
src/services/query_parser.py
src/services/answer_service.py
src/utils/tokenization.py
```

## 32. 数字与单位解析

保留并结构化提取：

- 数字；
- 百分号；
- 日期；
- 时间单位；
- 长度单位；
- 数量单位；
- 数字+单位完整短语。

示例：

```json
{
  "number": "60",
  "unit": "米",
  "phrase": "60米"
}
```

不得只按数字 `60` 召回全部候选。

## 33. 单位一致性排序

候选特征至少包含：

```text
exact_number_unit_match
number_match_unit_mismatch
context_phrase_match
```

排序原则：

1. 完整数值+单位短语命中；
2. 数值和单位分别命中；
3. 只有数字命中；
4. 相同数字但单位冲突时施加强惩罚。

查询“60米”时，“60珠/米”不得作为有效高相关命中。

## 34. 上下文短语

对：

```text
6个月无互动
6个月试用期
```

提高相邻短语和上下文词覆盖权重，不能只依据“6个月”判定相关。

## 35. No-answer Gate

### Search

当所有候选低于校准阈值时返回：

```json
{
  "data": [],
  "meta": {
    "no_match": true,
    "reason": "all_candidates_below_threshold",
    "top_score": 0.12,
    "threshold": 0.35
  }
}
```

阈值必须由验证集校准，不得随意写死且不评估 Recall。

### Ask

证据不足时：

```text
answer_mode=no_answer
```

不得利用不相关来源编造答案。

对于“今天股价”“最新进展”等本地知识库无法验证的实时问题，应明确无答案。

## 36. Golden 数据集

建立不少于 100 条：

```text
tests/eval/datasets/stability_round2_queries.jsonl
```

覆盖：

- 精确关键词；
- 同义表达；
- 数字和百分比；
- 日期、金额、时间、长度、数量；
- 表格字段；
- 跨文档问题；
- 无答案；
- 相似干扰项。

必须包含报告中的全部 12 个准确性案例。

## 37. Phase 6 验收

```text
Recall@5 >= 0.90
MRR >= 0.85
No-answer Accuracy >= 0.85
Citation completeness = 1.00
数字单位准确率 >= 0.95
```

额外断言：

- “60米”不把“60珠/米”当有效结果；
- “6个月无互动”优先命中企微无效粉丝相关内容；
- 营收、今日股价、火星探测等无证据问题返回空结果或 no-answer；
- 不得以提高阈值为代价导致 Recall 大幅下降。

---

# Phase 7：修复 Ingest 与剩余 P2

## 38. URL 导入错误诊断

### 目标文件

检查：

```text
src/mcp/tools/ingest.py
src/services/url_ingest.py
src/services/ingest_jobs.py
src/clients/http.py
```

### 稳定错误结构

```json
{
  "code": "HTTP_ERROR",
  "message": "URL returned HTTP 404",
  "details": {
    "url": "https://example.test/not-found",
    "status_code": 404,
    "stage": "fetch",
    "retryable": false,
    "redirect_chain": []
  }
}
```

至少分类：

```text
SSRF_BLOCKED
DNS_ERROR
CONNECT_TIMEOUT
READ_TIMEOUT
TLS_ERROR
HTTP_ERROR
REDIRECT_ERROR
EMPTY_CONTENT
PARSE_ERROR
EMBEDDING_ERROR
DATABASE_ERROR
UNKNOWN
```

301/302 应正确跟随，或返回明确重定向错误，不得误报 SSL。

使用受控 HTTP 测试服务或 mock transport 覆盖：

- 200；
- 301/302；
- 404；
- 500；
- TLS；
- connect/read timeout；
- redirect loop；
- empty body。

## 39. `extract_tasks_from_doc`

以下输入返回 `VALIDATION_ERROR`：

```text
content=""
content="   "
content 与 doc_id 都缺失
content 与 doc_id 同时存在
doc_id=""
```

补充：

- doc_id 主 content 为空时检查 blocks/chunks；
- PDF blocks 和 Excel 多 Sheet 纳入回归；
- 同一 doc_id 重复提取行为必须明确，不能意外重复写入而不说明。

## 40. Tags

规则：

```text
limit is None 或 limit >= 1
offset >= 0
```

`offset=-1` 必须拒绝。

超大 limit 应受最大页大小或 payload 限制。无参数完整列表保持兼容，但预计超过 `mcp.max_payload_bytes` 时必须截断或要求分页。

## 41. 健康检查延迟

保留只读契约，增加可解释延迟结构：

```json
{
  "latency": {
    "window_size": 50,
    "sample_count": 50,
    "p50_ms": 0,
    "p95_ms": 0,
    "p99_ms": 0,
    "by_operation": {
      "search": {},
      "ask": {},
      "ask_with_query": {}
    }
  }
}
```

要求：

- 明确窗口和样本数；
- 区分 search/ask；
- timeout 修复后重新测量；
- 不重新引入清理缓存或 trace 的写副作用。

## 42. 未知参数统一处理

所有 MCP 工具：

- 未声明参数返回验证错误；
- 不得静默忽略；
- stdio/http 一致；
- error envelope 稳定。

### Phase 7 验收

- H、I、J 组全部通过。
- HTTP 状态码、stage、retryable 可用。
- 所有 P2 关闭，或形成明确的不修复决策记录。

---

# Phase 8：全量回归、并发、长稳和发布门禁

## 43. 定向测试

至少运行：

```powershell
pytest tests/stability/test_graph_validation.py -q
pytest tests/stability/test_graph_pagination.py -q
pytest tests/stability/test_execute_graph_pagination.py -q
pytest tests/stability/test_route_execution_contract.py -q
pytest tests/stability/test_real_timeout.py -q
pytest tests/stability/test_transport_tool_parity.py -q
pytest tests/stability/test_search_no_answer.py -q
pytest tests/stability/test_search_numeric_units.py -q
pytest tests/stability/test_ingest_error_contract.py -q
pytest tests/stability/test_remaining_boundaries.py -q
```

## 44. 全项目回归

```powershell
ruff check src tests
mypy src
pytest tests -q
```

若存在历史 mypy 基线错误：

1. 精确列出；
2. 证明不是本轮引入；
3. 增加“错误数量不得增加”门禁；
4. 不得把新增错误归入历史基线。

## 45. 真实 MCP 回归

通过 stdio 和 streamable-http 执行核心 A～L 案例，验证：

- 工具集合一致；
- 参数 Schema 一致；
- 返回 envelope 一致；
- Graph 分页一致；
- Route 推荐可执行；
- timeout 一致；
- 未知参数一致报错。

## 46. 并发测试

在确认 HTTP 支持真实并发或启用多 worker 后执行。

### Search

```text
并发：1、5、10、20、50
每档：100 次
```

### RAG

```text
并发：1、3、5、10
每档：30 次
```

记录：

```text
QPS
P50/P95/P99
最大延迟
timeout
database locked
429
线程数
RSS
```

最低门槛：

- 10 并发 search 无数据库锁错误；
- 5 并发 ask 无进程崩溃；
- 无线程持续增长；
- 错误均有稳定分类。

## 47. 两小时长稳

每分钟调用：

```text
ping
search
ask
list_knowledge
tags
kb_health_check
```

每 5 分钟记录：

```text
RSS
线程数
句柄数
数据库连接数
缓存大小
P50/P95/P99
错误累计
```

失败条件：

- 内存持续单调增长且无法回落；
- 线程持续增长；
- P95 相比首 10 分钟恶化超过 50%；
- 未处理异常；
- 进程退出；
- DB lock 无法恢复。

---

## 48. 最终验收门槛

### Graph

- 分页测试全部通过；
- 无悬空边和错误路径；
- limit/offset/next_offset/truncated 正确；
- 两个 Graph 工具行为一致。

### Routing

- 返回 `recommended_tool/recommended_arguments`；
- 30 个路由案例首次执行成功率 100%；
- LLM 不可用时仍正确路由。

### Timeout

- 1 秒 timeout 实际 <= 1.5 秒；
- 连续 50 次线程增量 <= 2；
- 并发 timeout 后服务正常。

### Transport

- stdio/http 工具集合完全一致；
- 未知参数稳定报错；
- profile 和 alias 策略一致。

### Retrieval

```text
Recall@5 >= 0.90
MRR >= 0.85
No-answer Accuracy >= 0.85
Citation completeness = 1.00
数字单位准确率 >= 0.95
```

### Ingest

- 404/500/301/TLS/timeout 均有稳定分类；
- status_code、stage、retryable 完整；
- 不产生半成品数据。

### Stability

- 10 并发 search 无 DB lock；
- 5 并发 ask 无崩溃；
- 两小时长稳无明显泄漏；
- 正式数据库无污染。

任一强制项未通过，必须明确：

```text
未达到生产试点门槛
```

不得使用“基本稳定”“大致通过”等模糊结论。

---

## 49. 交付物

### 代码与测试

- 修复代码；
- 所有回归测试；
- golden accuracy dataset；
- 并发与长稳脚本。

### 原始结果

```text
artifacts/stability-repair/final-results.json
artifacts/stability-repair/latency.csv
artifacts/stability-repair/errors.jsonl
artifacts/stability-repair/resource-usage.csv
artifacts/stability-repair/tools-stdio.json
artifacts/stability-repair/tools-http.json
artifacts/stability-repair/pytest-output.txt
artifacts/stability-repair/soak-results.json
```

### 报告

```text
docs/reports/mcp-stability-round2-repair-YYYY-MM-DD.md
```

报告必须包含：

1. 基线 SHA 和最终 SHA；
2. 提交列表和修改文件；
3. 每个问题的根因；
4. 修复方式；
5. 回归测试；
6. 首次/最终成功率；
7. P50/P95/P99；
8. 并发和长稳结果；
9. 准确性指标；
10. 未解决问题；
11. 风险与回滚方案；
12. 是否达到生产试点门槛。

---

## 50. Agent 自主推进规则

1. Phase 0 完成前不得修改生产代码。
2. Phase 1 未全部通过，不得进入其他功能修复。
3. Phase 4 未解决前，不得执行高并发真实 Provider 测试。
4. 每个 Phase 完成后提交一次 commit。
5. 每次提交前运行对应定向测试。
6. 发现新 P0 时暂停后续阶段，先复现并修复。
7. 需要破坏兼容性时，先写迁移方案。
8. 无法执行的项目标记 `NOT TESTED`，不得写成 PASS。
9. 失败不得通过修改测试预期解决。
10. 完成全量测试和两小时长稳后才能结束。

---

## 51. Agent 最终回复格式

最终只输出：

```text
1. 基线 Commit SHA
2. 最终 Commit SHA
3. 分支名
4. 提交列表
5. 修改文件
6. 新增测试文件
7. P0 修复结果
8. P1 修复结果
9. P2 修复结果
10. 首次成功率
11. 最终成功率
12. P50/P95/P99
13. 并发结果
14. 两小时长稳结果
15. Recall@5/MRR/No-answer/单位准确率
16. stdio/http 工具数与差异
17. 未解决问题
18. NOT TESTED
19. 报告路径
20. 是否达到生产试点门槛
```
