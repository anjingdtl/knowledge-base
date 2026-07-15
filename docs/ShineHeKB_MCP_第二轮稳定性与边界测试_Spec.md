# ShineHeKB MCP 第二轮稳定性与边界测试 Spec

## 1. 目标

针对 `anjingdtl/knowledge-base` 当前 `master` 分支进行第二轮 MCP 稳定性、边界、并发、长稳与准确性测试。

本阶段只做测试和分析，不修改 `src/` 下生产代码。所有失败必须保留原始证据并至少复现一次。

重点验证：

1. Graph 分页、截断、节点/边/路径一致性。
2. Structured Query 分页元数据。
3. `route_query` 到下游工具的端到端契约。
4. `ask`、`ask_with_query` 的真实墙钟超时。
5. 健康检查的只读契约。
6. 参数类型和边界值。
7. URL、文件、异步导入的失败可诊断性。
8. SQLite 并发、资源泄漏与长时间运行。
9. 数字、百分比、日期、单位和无答案准确性。

---

## 2. 执行原则

### 禁止

- 修改 `src/` 生产代码。
- 修改数据库结构。
- 更新基线以掩盖失败。
- 将失败标记为 `skip`、`xfail`。
- 删除失败日志。
- 只用内部 mock 就宣称真实 MCP、并发或超时测试通过。
- 使用正式 `data/kb.db`。

### 允许

- 在 `tests/` 下新增测试。
- 使用临时数据库和临时目录。
- 启动独立 MCP 进程。
- 使用 fake LLM、慢速 Stub、真实 Provider 分层测试。
- 生成 JSON、CSV、Markdown 和日志证据。

### 数据隔离

所有写操作必须使用临时数据库，例如：

```python
tmp_path / "kb-test.db"
```

每个测试结束后确认：

- 临时进程关闭；
- SQLite 连接释放；
- 无残留线程；
- 无测试数据写入正式数据库；
- 无敏感配置写入日志或提交。

---

## 3. 环境记录

报告必须记录：

- Git commit SHA；
- 项目版本；
- Python、FastMCP、SQLite 版本；
- 操作系统、CPU、内存；
- MCP transport：`stdio`、`streamable-http`；
- LLM、Embedding、Reranker provider；
- 是否使用真实 API；
- 文档、Block、Vector、Tag 数量；
- 所有相关 timeout、retry、并发配置。

---

## 4. 测试组 A：Graph 参数校验

### A-01 `start_ids`

调用：

```text
graph_traverse(start_ids='"abc"')
graph_traverse(start_ids='{"id":"abc"}')
graph_traverse(start_ids='123')
graph_traverse(start_ids='[1,2]')
graph_traverse(start_ids='[]')
graph_traverse(start_ids='[""]')
graph_traverse(start_ids='["valid-id",1]')
```

期望：

- 必须是非空 `list[str]`；
- 非法输入返回 `VALIDATION_ERROR`；
- 不进入图遍历服务；
- 错误信息包含正确示例。

### A-02 数值边界

测试：

```text
limit=-1,0,1,max,max+1
offset=-1,0,1
max_depth=-1,0,max,max+1
```

期望：

- 非法值返回 `VALIDATION_ERROR`；
- 不允许负数触发反向切片；
- 不允许超过 capabilities 声明上限；
- error details 包含参数名和收到值。

---

## 5. 测试组 B：`graph_traverse` 分页

构造固定顺序的 12 个节点、至少 15 条边和多条路径。

### B-01

```text
total=8, limit=10, offset=5
```

期望返回第 6～8 个节点，共 3 个。

### B-02

```text
total=10, limit=10, offset=0
```

期望：

- 10 个节点；
- `truncated=false`；
- `next_offset=null`。

### B-03

```text
total=12, limit=5, offset=0
```

期望：

- 5 个节点；
- `truncated=true`；
- `next_offset=5`。

### B-04

```text
total=12, limit=5, offset=5
```

期望：

- 第 6～10 个节点；
- 与第一页无重复；
- `next_offset=10`。

### B-05

```text
total=12, limit=5, offset=10
```

期望：

- 2 个节点；
- `truncated=false`；
- `next_offset=null`。

### B-06

```text
total=12, limit=5, offset=20
```

期望空列表，不报内部异常。

### B-07 边和路径一致性

分页后必须保证：

- edge 的 source/target 都在当前返回节点内，或明确标记为外部节点；
- path 不引用被分页裁掉的节点；
- 不返回悬空边；
- 如返回全局边/路径，字段必须明确区分 `page_nodes`、`global_edges`、`global_paths`。

---

## 6. 测试组 C：`execute_query(type="graph")`

重复 B 组全部场景，并验证：

1. `len(nodes) > limit` 时真正截断。
2. `len(nodes) == limit` 时不能据此推断有下一页。
3. 服务层 `truncated=true` 不得被工具层覆盖。
4. `limit` 应下沉为图服务 `max_nodes`，不得固定使用默认值。
5. `total_estimate` 不得把当前页数量冒充精确总量。

建议采用“多取一条”或真实 total 判断下一页。

---

## 7. 测试组 D：Structured Query 分页

准备至少 12 条排序确定的数据。

### D-01

```text
query_spec.limit=100
tool limit=5
```

实际 limit 应为 5。

### D-02

```text
query_spec.limit=3
tool limit=100
```

当匹配数大于 3 时，期望：

- 返回 3 条；
- `truncated=true`；
- `next_offset=3`。

### D-03

结果数恰好等于 limit 且无更多结果：

- `truncated=false`；
- `next_offset=null`。

禁止仅用 `len(results) == limit` 判断下一页。

### D-04 多页一致性

依次请求 `offset=0,5,10`，验证：

- 无重复；
- 无遗漏；
- 排序稳定；
- 同值排序有确定次级键。

### D-05 total 语义

确认 `total_estimate` 是当前页数、近似总数还是精确总数。若只是当前页数量，必须判定为契约问题。

---

## 8. 测试组 E：路由端到端契约

至少准备 30 个查询。

### Structured

```text
列出所有 file_type 为 pdf 的知识条目
标签为企微的所有文档
```

期望：

```text
mode=structured
recommended_tool=execute_query
```

### Graph

```text
文档 A 引用了哪些页面
页面 A 与页面 B 有什么关联
```

期望：

```text
mode=graph
recommended_tool=execute_query 或 graph_traverse
```

### Hybrid

```text
广西电信企微未来应该怎么发展
总结企微集约运营的主要问题
```

期望：

```text
mode=hybrid
recommended_tool=ask_with_query、ask 或 search
```

禁止推荐 `execute_query(type="hybrid")`。

### 自动执行

1. 调用 `route_query`；
2. 读取 `recommended_tool`；
3. 将 `recommended_arguments` 原样传给下游工具；
4. 第一次调用必须成功；
5. 不允许人工改参后重试才算通过。

验收：参数型首次成功率 100%。

---

## 9. 测试组 F：真实超时与线程泄漏

### F-01 Legacy Pipeline

Stub：

```python
def query(...):
    time.sleep(10)
```

配置：

```text
rag.ask.total_timeout=1
```

墙钟测量期望：

- 1.5 秒内返回；
- 返回 timeout 结构；
- 不等待底层线程自然结束。

### F-02 Verified Ask

对 `ask_verified` 做同样测试。

### F-03 连续超时

连续 50 次超时请求，记录 `threading.active_count()`：

- 线程数不能持续增长；
- 无大量后台 LLM 线程残留。

### F-04 并发超时

同时 10 个慢请求：

- 每个在配置时间内返回；
- 后续 `ping`、`search` 仍可执行；
- 不因线程池耗尽永久阻塞。

### F-05 Provider 层

验证 HTTP 客户端分别设置：

- connect timeout；
- read timeout；
- total timeout；
- retry 上限。

模拟连接建立后不返回正文，验证 read timeout。

---

## 10. 测试组 G：健康检查只读性

### G-01 副作用

插入过期 embedding cache 和 trace，记录调用前行数，执行：

```text
kb_health_check()
```

若工具仍声明 `readOnlyHint=true`、`side_effect=read`，期望：

- 行数不变；
- 不执行 DELETE；
- 不提交清理事务。

### G-02 幂等性

连续 20 次调用：

- 除动态时间外结果稳定；
- 数据库不变化；
- 无额外写事务。

### G-03 高频探针

并发 20 个 health check，同时执行 search：

- 不出现 `database is locked`；
- search 不被清理事务阻塞；
- P95 不明显恶化。

---

## 11. 测试组 H：Tags 分页

构造 205 个排序确定标签。

请求：

```text
limit=50, offset=0
limit=50, offset=50
limit=50, offset=100
limit=50, offset=150
limit=50, offset=200
```

验证：

- 无重复、无遗漏；
- 最后一页 5 条；
- `count=205`；
- `next_offset`、`truncated` 正确；
- 排序稳定。

异常值：

```text
limit=0
limit=-1
offset=-1
limit=1000000
offset=1000000
```

还要验证无参数返回完整列表时是否可能超过 `mcp.max_payload_bytes`。

---

## 12. 测试组 I：文档任务提取

### I-01

直接 `content` 正常提取并存储。

### I-02

通过真实导入后使用 `doc_id`，覆盖：

- Markdown；
- PDF；
- DOCX；
- XLSX；
- TXT。

禁止只手工插入 `knowledge_items.content`。

### I-03 Excel

验证多 Sheet 是否全部提取，来源是否包含 Sheet 和位置。

### I-04 PDF/Block

当主 content 为空但 blocks/chunks 有内容时，仍应正确提取。

### I-05 输入边界

```text
content 和 doc_id 都缺失
content 和 doc_id 同时存在
doc_id 不存在
doc_id 为空
content 为空
```

### I-06 重复执行

同一 doc_id 连续提取两次，报告是否重复创建任务及去重策略。

---

## 13. 测试组 J：导入失败诊断

测试：

1. 正常 HTML；
2. 301/302；
3. 404；
4. 500；
5. TLS 错误；
6. 连接/读取超时；
7. 反爬页面；
8. 空正文；
9. JS 渲染页；
10. localhost/私网地址；
11. 超大页面；
12. 非 HTML 内容。

每个失败必须验证：

- `status=failed`；
- 稳定错误码；
- 可读错误信息；
- 失败阶段；
- URL 与时间；
- 不泄露 API Key；
- 不产生半成品；
- 重试不产生重复数据。

分类至少包括：

```text
SSRF_BLOCKED
DNS_ERROR
CONNECT_TIMEOUT
READ_TIMEOUT
TLS_ERROR
HTTP_ERROR
EMPTY_CONTENT
PARSE_ERROR
EMBEDDING_ERROR
DATABASE_ERROR
UNKNOWN
```

---

## 14. 测试组 K：并发与长稳

### K-01 Search 并发

并发数：`1、5、10、20、50`，每档 100 次。

记录：

- 成功率；
- QPS；
- P50/P95/P99；
- 最大延迟；
- CPU、内存；
- SQLite lock 错误。

### K-02 RAG 并发

并发数：`1、3、5、10`，每档至少 30 次 ask。

记录：

- 429；
- timeout；
- 空 answer；
- warnings；
- token；
- 检索、重排、LLM 分阶段耗时。

### K-03 混合读写

持续 10 分钟：

- 10 search；
- 3 ask；
- 2 ingest；
- 2 list_jobs；
- 2 health check。

验收：

- 无崩溃；
- 无数据库损坏；
- 无不可恢复锁；
- ingest 后可检索；
- 重启后数据一致。

### K-04 两小时长稳

每分钟执行 ping、search、ask、list_knowledge、tags、health check。

每 5 分钟记录：

- RSS；
- 线程数；
- 句柄数；
- 数据库连接数；
- 缓存大小；
- P95；
- 错误累计。

失败条件：

- 内存持续增长无法回落；
- 线程持续增长；
- P95 比首 10 分钟恶化超过 50%；
- 未处理异常；
- 进程退出。

---

## 15. 测试组 L：真实 MCP Transport

必须分别通过：

```text
stdio
streamable-http
```

验证：

- MCP Schema 与 Python 签名一致；
- 可选参数标记正确；
- 未知参数错误稳定；
- 返回可 JSON 序列化；
- payload 限制生效；
- 客户端断开后任务可取消；
- HTTP 写策略生效。

至少使用：

- 标准 MCP Inspector；
- Trae SOLO CN 或当前业务 Agent。

---

## 16. 准确性补充

构造不少于 100 条业务查询，覆盖：

- 精确词；
- 同义表达；
- 数字/百分比；
- 日期；
- 表格字段；
- 跨文档综合；
- 无答案；
- 相似干扰项。

必须包含：

```text
60%
60 珠/米
60 秒
60 户
2026 年 60%
```

指标：

- Recall@5；
- MRR；
- nDCG@10；
- No-answer Accuracy；
- Citation completeness；
- 数字单位准确率；
- 首位来源正确率。

建议门槛：

```text
Recall@5 >= 0.90
MRR >= 0.85
No-answer Accuracy >= 0.85
Citation completeness = 1.00
数字单位准确率 >= 0.95
```

---

## 17. 统计口径

必须报告：

```text
初始调用次数
初始成功次数
初始失败次数
首次成功率
重试次数
重试成功次数
总尝试次数
最终成功率
```

计算：

```text
首次成功率 = 初始成功案例数 / 独立测试案例数
最终成功率 = 重试后成功案例数 / 独立测试案例数
```

失败分类：

```text
SCHEMA_ERROR
VALIDATION_ERROR
QUERY_ERROR
TIMEOUT
PROVIDER_RATE_LIMIT
NETWORK_ERROR
DATABASE_LOCK
INGEST_ERROR
ASSERTION_FAILURE
INTERNAL_ERROR
PROCESS_CRASH
```

---

## 18. 交付物

### 测试代码

```text
tests/stability/test_graph_pagination.py
tests/stability/test_structured_pagination.py
tests/stability/test_route_execution_contract.py
tests/stability/test_real_timeout.py
tests/stability/test_health_readonly.py
tests/stability/test_ingest_diagnostics.py
tests/stability/test_concurrency.py
tests/stability/test_long_run.py
tests/stability/test_mcp_transport.py
```

### 原始证据

```text
artifacts/stability/raw-results.json
artifacts/stability/latency.csv
artifacts/stability/errors.jsonl
artifacts/stability/resource-usage.csv
artifacts/stability/pytest-output.txt
```

### 报告

```text
docs/reports/mcp-stability-round2-YYYY-MM-DD.md
```

报告必须包含：

1. 环境与 commit SHA；
2. 测试总览；
3. 首次和最终成功率；
4. P50/P95/P99；
5. 每个失败案例；
6. 可复现命令；
7. 原始异常；
8. 根因和置信度；
9. P0/P1/P2 优先级；
10. 未完成项；
11. `NOT TESTED` 项；
12. 是否建议进入修复阶段。

---

## 19. 验收标准

只有同时满足以下条件，才能判定“核心 MCP 能力具备生产试点条件”：

- 所有分页测试通过；
- Graph 无悬空边和错误路径；
- 路由结果可原样调用下游工具；
- 参数型首次成功率 100%；
- 健康检查无未声明写副作用；
- 硬超时误差不超过配置值 20%；
- 连续超时无线程泄漏；
- 10 并发 search 无数据库锁错误；
- 5 并发 RAG 无进程崩溃；
- 两小时长稳无明显资源泄漏；
- 导入失败有稳定分类和完整诊断；
- 引用均可回溯；
- 正式数据库未被测试污染。

未执行项目必须标记：

```text
NOT TESTED
```

不得标记为 PASS。

---

## 20. 执行顺序

1. 阅读当前实现、配置和已有测试；
2. 先执行 A～G、L；
3. 再执行 H～J；
4. 再执行 K；
5. 最后执行准确性补充；
6. 每阶段保存原始输出和提交测试代码；
7. 失败至少复现一次；
8. 输出报告；
9. 停止，不修改生产代码；
10. 等待人工确认后再制定修复方案。
