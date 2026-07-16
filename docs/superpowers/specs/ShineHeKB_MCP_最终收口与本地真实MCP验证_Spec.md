# ShineHeKB MCP 最终收口与本地真实 MCP 验证 Spec

**适用项目：** ShineHeKB / ShineHeKnowledge  
**本地目录：** `D:\ClaudeCodeWorkSpace\projects\knowledge-base`  
**建议分支：** `fix/mcp-final-closure`  
**前置基线：** 第二轮修复后的当前 `master`  
**目标：** 关闭剩余代码风险，并由本地 Agent 通过真实 MCP 完成最终验收。

---

## 1. 收口目标

1. `ask` / `ask_with_query` 超时后真正取消底层任务，而不是仅限制遗留线程。
2. Graph 在 `max_graph_nodes` 边界不会产生空页、自循环 `next_offset` 或无限分页。
3. Structured Query 使用真实生效 limit 正确计算分页元数据。
4. Search/Ask 的 no-answer 门禁覆盖 FTS 回退路径。
5. 完整执行 107 条 Golden 真库评估。
6. 使用本地 Agent 通过真实 `stdio` 和 `streamable-http` MCP 测试。
7. 使用真实 HTTP MCP 多连接完成并发测试。
8. 使用真实 MCP 服务完成长稳测试。
9. 建立本地与远程 CI 最终门禁。

---

## 2. 强制原则

### 2.1 不能作为最终验收的测试

以下结果只能用于开发，不能用于最终验收：

- 直接调用 `src.mcp.tools.*`；
- mock LLM、mock RagPipeline；
- `SimpleNamespace` 伪造 Container；
- lambda 立即返回 ask；
- 只检查工具注册函数或函数签名；
- 只运行单元测试；
- 只执行临时数据库进程内循环；
- 只根据报告文字宣布通过。

### 2.2 最终必须实际调用 MCP

必须由本地 Agent 或独立客户端实际调用：

```text
stdio
streamable-http
```

至少覆盖：

```text
initialize
tools/list
ping
search
search_fulltext
route_query
execute_query
graph_traverse
ask
ask_with_query
kb_health_check
tags
ingest_url
```

### 2.3 数据安全

- 写测试使用临时数据库或独立测试环境。
- 真库准确性测试只读访问正式知识库。
- 测试数据统一使用 `FINAL_CLOSURE_TEST_` 前缀。
- 清理前必须精确列出 ID。
- 禁止修改正式 `data/kb.db`。
- 不得记录 API Key、Token、Cookie 或完整敏感内容。

### 2.4 测试驱动

每个问题必须：

1. 先新增失败测试；
2. 确认修复前稳定失败；
3. 再修改生产代码；
4. 运行定向测试；
5. 运行真实 MCP 测试；
6. 保存原始结果；
7. 独立提交 commit。

---

# Phase 0：基线与环境保护

## 3. 创建分支

```powershell
git checkout master
git pull
git checkout -b fix/mcp-final-closure
```

记录：

```text
baseline_commit_sha
python_version
fastmcp_version
sqlite_version
config_path
stdio_tool_count
http_tool_count
formal_db_size
formal_document_count
formal_block_count
formal_vector_count
thread_count
rss_memory_mb
```

保存：

```text
artifacts/final-closure/baseline.json
```

## 4. MCP 配置

显式配置：

```yaml
mcp:
  tool_profile: full
  experimental_tools_enabled: true
  enable_legacy_aliases: false
```

启动 HTTP MCP：

```powershell
python -m src.mcp_cli --transport streamable-http --host 127.0.0.1 --port 9000
```

stdio 由本地 Agent 的 MCP 配置启动。

### Phase 0 验收

- stdio 和 HTTP 均可连接。
- 两种 Transport 工具集合已保存。
- 正式数据库基线已记录。
- 未修改生产代码。

---

# Phase 1：真正可取消的超时链路

## 5. 当前风险

当前 daemon worker + semaphore 只能限制遗留线程数量，不能终止正在执行的 Provider 调用，也可能永久占用 worker slot。

## 6. 修复要求

### 6.1 统一 Deadline

请求入口建立：

```python
deadline = time.monotonic() + total_timeout
remaining = max(0, deadline - time.monotonic())
```

检索、重写、Embedding、Rerank、LLM、引用和图构建均使用同一剩余 deadline。

### 6.2 Provider timeout

所有 Provider 必须明确配置：

```text
connect_timeout
read_timeout
write_timeout
pool_timeout
total_timeout
retry_limit
```

Provider timeout 不得超过剩余 deadline。

### 6.3 Async 取消

优先：

```python
async with asyncio.timeout(remaining):
    result = await provider_call()
```

超时后必须：

- cancel task；
- await cleanup；
- 关闭 HTTP response；
- 释放连接；
- 结束 trace；
- 不遗留线程。

### 6.4 同步 SDK

不可取消的同步 SDK 必须移入可终止 worker process。timeout 后 terminate + join，不得继续遗弃 daemon thread。

### 6.5 返回语义

只有实际取消成功时：

```json
{"cancelled": true}
```

如后台仍可能继续：

```json
{
  "cancelled": false,
  "background_work_may_continue": true
}
```

## 7. 自动化测试

新增：

```text
tests/stability/test_provider_deadline_propagation.py
tests/stability/test_real_cancellation.py
tests/stability/test_timeout_slot_recovery.py
```

覆盖：

- 1 秒 timeout + 10 秒异步慢服务；
- connect timeout；
- read timeout；
- 连续 50 次 timeout；
- 两个永久卡死任务后的第三次请求；
- slot 恢复；
- timeout 后线程/进程/连接回到基线；
- timeout 后 ping/search 正常；
- `cancelled` 字段真实。

## 8. 真实 MCP 验收

stdio 和 HTTP 分别调用慢 Provider 测试配置，记录：

```text
configured_timeout_ms
wall_clock_elapsed_ms
cancelled
background_work_may_continue
thread_count_before/after
process_count_before/after
ping_after_timeout
search_after_timeout
```

### Phase 1 门槛

- 1 秒 timeout 实际 <= 1.5 秒。
- 连续 50 次后线程增量 <= 1。
- 无永久占用 slot。
- 第三个请求不会被前两个遗留任务永久阻塞。
- stdio/http 均通过。

---

# Phase 2：Graph 极限分页

## 9. 当前风险

当 `offset >= max_graph_nodes` 且服务返回 truncated 时，可能出现：

```text
page_nodes=[]
next_offset=当前 offset
truncated=true
```

造成无限翻页。

## 10. 修复要求

始终满足：

```text
next_offset is null
或
next_offset > current_offset
```

绝不允许 `next_offset == current_offset`。

若当前版本不实现 cursor，到达硬上限时返回：

```json
{
  "truncated": false,
  "meta": {
    "next_offset": null,
    "hard_limit_reached": true,
    "max_graph_nodes": 200
  }
}
```

不得返回重复窗口，也不得声称 total 精确。

## 11. 测试

新增：

```text
tests/stability/test_graph_hard_limit.py
tests/stability/test_graph_pagination_monotonic.py
```

覆盖：

```text
offset=190,195,199,200,205
limit=5
max_graph_nodes=200
```

断言：

- next_offset 严格递增或为 null；
- 不重复页面；
- 20 次自动翻页内终止；
- 无悬空 edge/path；
- stdio/http 一致。

---

# Phase 3：Structured Query 分页

## 12. 修复要求

`structured_query` 与 `execute_query(type="structured")` 必须共用统一分页实现。

数据库多取一条：

```text
effective_limit + 1
```

计算：

```python
has_more = len(rows) > effective_limit
page = rows[:effective_limit]
```

返回：

```text
limit=effective_limit
next_offset=offset+len(page) if has_more else null
truncated=has_more
```

如无法获得精确 total：

```json
{
  "total_estimate": null,
  "total_estimate_is_exact": false
}
```

不得把当前页长度当总量。

## 13. 测试

覆盖：

```text
DSL limit=3, tool limit=100, total=12
DSL limit=100, tool limit=5, total=12
effective limit=5, total=5
effective limit=5, total=6
offset=0/5/10
offset 超出范围
```

断言：

- meta.limit 等于 effective_limit；
- 无重复无遗漏；
- 恰好等于 limit 且无下一页时 truncated=false；
- 两入口一致；
- 有稳定次级排序。


---

# Phase 4：No-answer 与 FTS 回退收口

## 14. 当前风险

语义结果弱时回退 FTS，只要 FTS 有关键词命中就可能被标记为有效结果，导致低相关文档被当成答案依据。

## 15. 统一相关性门禁

Semantic、FTS、Hybrid 必须进入统一证据评估层，至少计算：

```text
semantic_score
fts_score
title_score
numeric_unit_score
phrase_coverage
query_term_coverage
freshness_score
final_relevance_score
```

FTS 不得仅凭“有关键词命中”判定有效。

必须验证：

- 核心实体覆盖；
- 关键短语覆盖；
- 数字单位一致；
- 查询意图与文档类型一致；
- 最终相关分数达到阈值。

例如：

```text
查询：广西电信2025年营收多少亿
候选：营收资金管理办法
```

只有“营收”命中，不足以回答具体数值，应返回 no-answer。

## 16. 实时问题

识别：

```text
今天
当前
现在
最新
股价
实时
```

本地库没有可验证的实时来源时返回：

```text
no_answer
reason=requires_current_external_data
```

## 17. Ask 前置门禁

证据不足时必须在 LLM 生成前阻断：

```json
{
  "answer": "",
  "answer_mode": "no_answer",
  "sources": [],
  "reason": "insufficient_relevant_evidence"
}
```

禁止先生成确定性答案，再事后删除来源。

## 18. 测试

新增：

```text
tests/stability/test_fts_no_answer_gate.py
tests/stability/test_current_information_no_answer.py
tests/stability/test_pre_llm_evidence_gate.py
```

覆盖：

```text
广西电信2025年营收多少亿
中国电信股价今天多少
量子计算最新进展
火星探测任务时间表
60米
60珠/米
6个月无互动
6个月试用期
```

### Phase 4 门槛

- 无答案查询不返回低相关结果。
- FTS 回退受 no-answer 门禁。
- 弱证据不会触发 LLM 编造。
- Citation 仅来自最终有效证据。

---

# Phase 5：107 条 Golden 真库评估

## 19. 数据集

使用：

```text
tests/eval/datasets/stability_round2_queries.jsonl
```

每条至少包含：

```json
{
  "id": "",
  "query": "",
  "category": "",
  "expected_ids": [],
  "expected_no_answer": false,
  "expected_units": [],
  "notes": ""
}
```

缺失字段必须补齐，不得只有 query 文本。

## 20. 真实 MCP 执行

必须通过 MCP 调用：

```text
stdio：107 条 search + 必要 ask
streamable-http：107 条 search + 必要 ask
```

不得直接调用 Python 函数。

## 21. 指标

计算：

```text
Recall@1
Recall@5
MRR
nDCG@10
No-answer Accuracy
False-answer Rate
Citation completeness
Citation correctness
数字单位准确率
Transport 一致率
```

Transport 一致性至少比较：

- no-answer 决策；
- top-5 核心结果；
- route mode；
- citation IDs。

## 22. 门槛

```text
Recall@5 >= 0.90
MRR >= 0.85
nDCG@10 >= 0.85
No-answer Accuracy >= 0.85
False-answer Rate <= 0.10
Citation completeness = 1.00
Citation correctness >= 0.95
数字单位准确率 >= 0.95
Transport 一致率 >= 0.95
```

## 23. 产物

```text
artifacts/final-closure/eval-stdio.jsonl
artifacts/final-closure/eval-http.jsonl
artifacts/final-closure/eval-metrics.json
artifacts/final-closure/eval-errors.jsonl
```

指标未达标时必须列出失败 query ID。

---

# Phase 6：本地 Agent 实际 MCP 端到端验证

## 24. Agent 执行边界

必须使用已经接入 ShineHeKB 的本地 Agent 实际调用 MCP。

Agent 不得读取源码后直接推断通过，也不得用直接 Python 调用替代。

## 25. 场景

### 25.1 工具发现

调用：

```text
tools/list
kb_capabilities
```

验证：

- stdio/http 工具集合一致；
- alias 配置一致；
- Schema 参数一致。

### 25.2 路由原样执行

至少 30 个查询：

1. 调用 `route_query`；
2. 读取 `recommended_tool`；
3. 原样传入 `recommended_arguments`；
4. 不人工修正；
5. 记录首次成功。

### 25.3 Graph 自动分页

Agent 自动：

1. 调第一页；
2. 根据 `next_offset` 继续；
3. 直到 null；
4. 检查节点无重复；
5. 检查无悬空边；
6. 检查 next_offset 不自循环。

### 25.4 No-answer

对无证据问题分别调用：

```text
search
ask
```

确认不会把无关文档包装成答案。

### 25.5 Timeout

调用真实慢测试 Provider：

- 记录墙钟；
- timeout 后立即 ping；
- 再执行 search；
- 检查服务没有被遗留任务占满。

## 26. 原始记录

```text
artifacts/final-closure/agent-mcp-stdio.jsonl
artifacts/final-closure/agent-mcp-http.jsonl
artifacts/final-closure/agent-route-execution.jsonl
artifacts/final-closure/agent-graph-pagination.jsonl
artifacts/final-closure/agent-timeout.jsonl
```

每条包含：

```text
timestamp
transport
tool
arguments
elapsed_ms
response_ok
error_code
response_hash
test_case_id
```

### Phase 6 门槛

- 路由原样执行成功率 100%。
- Graph 自动分页终止率 100%。
- 参数错误返回稳定 validation error。
- stdio/http Schema 和行为一致。

---

# Phase 7：真实 streamable-http 并发

## 27. 测试方式

必须从独立客户端建立多个真实 HTTP MCP 会话，执行：

```text
initialize
tools/call
```

禁止通过线程池直接调用 Python `search()` 或 `ask()`。

## 28. Search 并发

并发档：

```text
1
5
10
20
50
```

每档至少 100 次。

记录：

```text
QPS
P50
P95
P99
max
success
timeout
HTTP error
MCP error
DB lock
session error
```

## 29. Ask 并发

并发档：

```text
1
3
5
10
```

每档至少 30 次。

控制成本时：

- 先使用本地可控 Provider；
- 再使用真实 Provider 每档至少 5 次抽样；
- 报告明确区分 controlled provider 和 real provider。

## 30. 连接行为

覆盖：

- 多会话并发；
- 单会话连续；
- 客户端断开；
- session idle；
- timeout 后重连；
- 服务重启后重新 initialize。

### Phase 7 门槛

- 10 并发 search 无 DB lock。
- 20 并发 search 成功率 >= 99%。
- 5 并发 ask 无进程崩溃。
- timeout 后新请求仍可进入。
- session 可恢复。
- 线程数和连接数可回落。

---

# Phase 8：真实 MCP 长稳

## 31. 时长

至少执行：

```text
streamable-http MCP：2 小时
stdio MCP：30 分钟
```

不能直接调用 Python 函数。

## 32. 每分钟操作

轮换：

```text
ping
search
route_query
execute_query
graph_traverse
list_knowledge
tags
kb_health_check
```

ask 每 5 分钟一次，每 15 分钟执行一次 timeout 场景。

## 33. 每 5 分钟采样

```text
RSS
CPU
thread_count
process_count
open_connection_count
sqlite_connection_count
session_count
P50
P95
P99
errors
timeouts
```

## 34. 失败条件

- 进程退出；
- session 无法恢复；
- DB lock 持续超过 30 秒；
- 线程持续增长；
- 连接持续增长；
- timeout slot 无法恢复；
- P95 比首 10 分钟恶化超过 50%；
- 正式数据库被修改；
- MCP 工具集合漂移。

## 35. 产物

```text
artifacts/final-closure/soak-http.json
artifacts/final-closure/soak-stdio.json
artifacts/final-closure/soak-resource.csv
artifacts/final-closure/soak-errors.jsonl
```

---

# Phase 9：工程门禁与最终报告

## 36. 本地质量门禁

运行：

```powershell
ruff check src tests
mypy src
pytest tests -q
```

要求：

- Ruff 0 error；
- mypy 0 error；
- pytest 0 failed；
- 不允许 `PytestUnhandledThreadExceptionWarning`；
- Windows 子进程 UTF-8 解码异常必须修复；
- 新增 warning=0。

## 37. 远程 CI

如仓库尚无 GitHub Actions，新增：

```text
.github/workflows/ci.yml
```

至少运行：

```text
ruff
mypy
pytest
```

Windows 特有测试可新增：

```text
.github/workflows/windows-tests.yml
```

最终 Commit 必须有成功的远程 CI 状态。

## 38. 最终报告

生成：

```text
docs/reports/mcp-final-closure-YYYY-MM-DD.md
```

报告必须包含：

1. 基线 SHA；
2. 最终 SHA；
3. 分支和提交列表；
4. 每个问题根因；
5. 修复方式；
6. 单元测试；
7. stdio 实际 MCP；
8. HTTP 实际 MCP；
9. 107 条 Golden 指标；
10. 真实并发；
11. 真实长稳；
12. Provider timeout 和取消证据；
13. Graph 极限分页；
14. Structured 分页；
15. 资源变化；
16. 正式数据库变化；
17. CI 状态；
18. 未解决问题；
19. NOT TESTED；
20. 是否达到生产试点门槛。

---

## 39. 最终生产试点门槛

只有全部满足才允许输出：

```text
达到生产试点门槛
```

### Timeout

- 真正取消或终止底层调用；
- 无遗留 slot；
- 50 次 timeout 无资源泄漏；
- stdio/http 均通过。

### Graph

- 极限分页不会自循环；
- next_offset 严格递增或 null；
- 无悬空边和路径；
- 自动分页可终止。

### Structured

- effective limit 正确；
- 多取一条判断下一页；
- total 语义真实；
- 两入口一致。

### Accuracy

```text
Recall@5 >= 0.90
MRR >= 0.85
nDCG@10 >= 0.85
No-answer Accuracy >= 0.85
False-answer Rate <= 0.10
Citation correctness >= 0.95
数字单位准确率 >= 0.95
```

### MCP

- stdio/http 实际工具集一致；
- 路由原样执行成功率 100%；
- 真实 HTTP 并发通过；
- 真实 MCP 长稳通过。

### Engineering

- Ruff、mypy、pytest 全绿；
- 无未处理线程异常；
- 远程 CI 成功；
- 正式数据库无污染。

任一项未满足，必须输出：

```text
未达到生产试点门槛
```

不得使用“基本完成”“总体稳定”“接近通过”等模糊表述。

---

## 40. Agent 自主执行规则

1. Phase 0 完成前不得修改代码。
2. Phase 1～4 均先失败测试后修复。
3. 每个 Phase 独立 commit。
4. Phase 1 未通过前不得高并发测试。
5. Phase 2、3 未通过前不得开始 Agent 自动分页。
6. Phase 4 未通过前不得运行 107 条 ask 评估。
7. 真实 MCP 测试不得用直接 Python 调用替代。
8. 所有失败至少复现一次。
9. 无法执行标记 `NOT TESTED`。
10. 不得修改预期掩盖失败。
11. 不得污染正式数据库。
12. 所有原始证据保存到 artifacts。
13. 报告必须区分：
    - unit test；
    - in-process；
    - stdio MCP；
    - streamable-http MCP；
    - controlled provider；
    - real provider。
14. 所有强制门槛通过后才能结束。

---

## 41. Agent 最终回复格式

最终只输出：

```text
1. 基线 Commit SHA
2. 最终 Commit SHA
3. 分支名
4. 提交列表
5. 修改文件
6. 新增测试
7. Timeout 真取消结果
8. Graph 极限分页结果
9. Structured 分页结果
10. 107 条 Golden 指标
11. stdio 实际 MCP 结果
12. streamable-http 实际 MCP 结果
13. 路由原样执行成功率
14. HTTP 并发 P50/P95/P99
15. stdio/http 长稳结果
16. 线程/进程/连接资源变化
17. Ruff/mypy/pytest
18. CI 状态
19. 正式数据库是否变化
20. 未解决问题
21. NOT TESTED
22. 报告路径
23. 是否达到生产试点门槛
```
