# ShineHeKB MCP 生产试点前最终验收收尾 Spec

**项目：** ShineHeKB / ShineHeKnowledge  
**项目目录：** `D:\ClaudeCodeWorkSpace\projects\knowledge-base`  
**建议分支：** `fix/mcp-production-pilot-final-validation`  
**建议报告：** `docs/reports/mcp-production-pilot-final-validation-YYYY-MM-DD.md`  
**建议产物目录：** `artifacts/production-pilot-final-validation`  
**前置版本：** v1.10.3 / 当前 `master`  
**目标：** 修正现有验收体系的统计失真，补齐正式向量检索、真实 Provider、路由语义和非协作任务终止能力，形成可信的生产试点结论。

---

# 1. 背景与本轮边界

v1.10.3 已完成以下主要修复：

- Graph 极限分页与 `next_offset` 自循环；
- Graph 页内边、路径一致性；
- Structured Query `effective_limit + 1` 分页；
- FTS 与 Semantic 统一 no-answer 门禁；
- stdio / streamable-http 实际 MCP 调用；
- HTTP 多会话并发；
- 2 小时 HTTP 与 30 分钟 stdio 长稳；
- Ruff、mypy、pytest 和 CI；
- timeout 返回语义由“假取消”改为诚实标记。

这些内容原则上不再大规模重构。本轮重点不是重复证明 Transport 能运行，而是补齐以下未完成项：

1. 现有 Golden 数据大量缺少独立 ground truth；
2. Recall/MRR/nDCG 对无 expected IDs 的样本自动计满分；
3. Citation 指标属于 no-answer 场景下的真空满分；
4. 数字单位样本在空结果时仍可能计为通过；
5. 路由只验证“可调用”，未验证路由模式和任务完成；
6. 正式向量索引存在 `Vector index is EMPTY`；
7. 正式 semantic / hybrid / reranker 链路未完成验收；
8. 真实 Provider 并发与回答质量抽样未完成；
9. 非协作同步 SDK timeout 后仍可能后台继续；
10. 现有“达到生产试点门槛”结论缺乏可信评估依据。

---

# 2. 最终目标

本轮完成后必须能可信回答：

```text
检索结果是否真的找对了？
无答案时是否真的拒答？
回答中的引用是否真的支持答案？
路由是否真的选对了工具和模式？
正式向量索引是否真的工作？
真实 Provider 在并发和超时下是否稳定？
超时后底层不可取消调用是否真的终止？
```

只有所有强制门槛通过，才能输出：

```text
达到生产试点门槛
```

否则必须输出：

```text
未达到生产试点门槛
```

---

# 3. 强制执行原则

## 3.1 真实 Ground Truth

禁止使用以下方式直接生成最终标准答案：

- 用当前 `search` 结果反填 `expected_ids`；
- 用正式库 LIKE/FTS 自动取前五作为 ground truth；
- 用当前模型回答作为标准答案；
- expected IDs 为空时把工具调用成功视为检索正确；
- `hit_or_empty` 样本计入 Recall/MRR/nDCG；
- 自动生成 PAD 样本填充数量。

自动检索只能作为人工标注候选，不能作为最终正确答案。

## 3.2 指标分母必须严格

每项指标必须定义明确的适用样本集合。

例如：

```text
Recall@5：
仅计算 expected_ids 非空的 retrieval 样本。

No-answer Accuracy：
仅计算 expected_no_answer=true 的样本。

Citation correctness：
仅计算产生真实非空答案、且存在人工标注证据的 ask 样本。

数字单位准确率：
仅计算有明确 expected_units / forbidden_units / expected_ids 的数字单位样本。
```

禁止把不适用样本默认记为 1。

## 3.3 真实 MCP 与真实 Provider

开发阶段可使用 mock，但最终验收必须区分：

```text
unit
in-process
stdio MCP
streamable-http MCP
controlled provider
real provider
formal vector path
formal FTS path
```

报告不得混淆。

## 3.4 数据安全

- 正式 `data/kb.db` 仅允许只读评估；
- 测试写操作使用临时环境；
- 正式向量索引修复前后必须备份索引元数据；
- 禁止删除正式知识数据；
- 禁止打印 API Key；
- 所有请求日志脱敏。

---

# Phase 0：基线冻结与结论回退

## 4. 基线记录

从当前 `master` 创建：

```powershell
git checkout master
git pull
git checkout -b fix/mcp-production-pilot-final-validation
```

记录：

```text
baseline_commit_sha
version
python_version
fastmcp_version
formal_db_size
formal_document_count
formal_block_count
formal_embedding_count
formal_vector_index_count
vector_backend
embedding_model
reranker_model
llm_model
stdio_tool_count
http_tool_count
current_report_decision
```

保存：

```text
artifacts/production-pilot-final-validation/baseline.json
```

## 5. 结论回退

在本轮完成前，报告和 README 中不得继续无条件宣称：

```text
达到生产试点门槛
```

应改为：

```text
生产试点验收进行中
```

或：

```text
可进入受控内测，生产试点结论待最终验收
```

不得修改历史报告原始证据，但可增加复查说明。

### Phase 0 门槛

- 建立独立分支；
- 保存基线；
- 正式 DB 未变化；
- 尚未修改生产代码；
- 明确旧指标不作为最终验收依据。

---

# Phase 1：重建可信 Golden 数据集

## 6. 数据集拆分

禁止继续使用单一 107 条混合数据直接计算所有指标。

建立以下文件：

```text
tests/eval/datasets/production_pilot_retrieval.jsonl
tests/eval/datasets/production_pilot_no_answer.jsonl
tests/eval/datasets/production_pilot_numeric_units.jsonl
tests/eval/datasets/production_pilot_routing.jsonl
tests/eval/datasets/production_pilot_answer_citations.jsonl
```

## 7. Retrieval 数据格式

每条必须包含：

```json
{
  "id": "RET-001",
  "query": "用户真实查询",
  "category": "keyword|semantic|synonym|multi_constraint|long_query",
  "expected_ids": ["人工确认的知识ID"],
  "acceptable_ids": [],
  "forbidden_ids": [],
  "difficulty": "easy|medium|hard",
  "annotation_source": "human",
  "annotator_notes": "",
  "corpus_snapshot_sha": ""
}
```

要求：

- `expected_ids` 不得为空；
- 至少一个人工确认的主要相关文档；
- 可接受替代放在 `acceptable_ids`；
- 明确误命中放在 `forbidden_ids`；
- 不允许 `hit_or_empty`；
- 不允许 PAD；
- 每条由人工检查标题和正文。

## 8. No-answer 数据格式

```json
{
  "id": "NOA-001",
  "query": "",
  "expected_no_answer": true,
  "reason": "requires_current_external_data|not_in_corpus|insufficient_specific_evidence|ambiguous",
  "known_distractor_ids": [],
  "annotation_source": "human",
  "annotator_notes": ""
}
```

至少覆盖：

- 实时信息；
- 库外事实；
- 有关键词但无答案；
- 数值问题缺少对应值；
- 时间、实体和范围不完整；
- 容易被泛关键词误命中的查询。

## 9. 数字单位数据格式

```json
{
  "id": "NUM-001",
  "query": "60 米",
  "expected_ids": ["人工确认ID"],
  "expected_units": ["米"],
  "forbidden_units": ["珠/米", "秒"],
  "forbidden_ids": [],
  "expected_no_answer": false,
  "annotation_source": "human"
}
```

禁止：

- 空结果自动通过；
- 只检查响应 JSON 中出现单位字符；
- 只检查第一条以外没有误命中。

必须验证 top-1、top-3 和 forbidden unit。

## 10. Routing 数据格式

```json
{
  "id": "ROUTE-001",
  "query": "",
  "expected_mode": "structured|graph|hybrid",
  "expected_tool": "execute_query|graph_traverse|search|ask_with_query",
  "required_argument_keys": [],
  "forbidden_tool": "",
  "expected_task_outcome": "non_empty|no_answer|validation_error|graph_result|structured_result"
}
```

## 11. Answer Citation 数据格式

```json
{
  "id": "ANS-001",
  "question": "",
  "expected_answer_facts": [
    {
      "fact_id": "F1",
      "statement": "",
      "supporting_knowledge_ids": [],
      "supporting_block_ids": []
    }
  ],
  "forbidden_claims": [],
  "minimum_sources": 1,
  "annotation_source": "human"
}
```

## 12. 样本数量门槛

最低要求：

```text
Retrieval：60 条
No-answer：30 条
Numeric Units：25 条
Routing：40 条
Answer + Citation：25 条
```

样本可以部分重叠，但每个指标使用自己的严格分母。

## 13. 人工标注流程

新增：

```text
docs/testing/production-pilot-eval-annotation-guide.md
```

流程：

1. 从真实库检索候选；
2. 打开候选正文；
3. 人工确认相关性；
4. 标记 expected / acceptable / forbidden；
5. 记录 corpus snapshot；
6. 第二次复核；
7. 对争议样本标记 `excluded_pending_review=true`，不得计分。

### Phase 1 门槛

- 删除或排除 PAD 自动满分样本；
- 所有参与 Recall 的样本都有非空人工 ground truth；
- 每项指标有独立数据集；
- 数据格式校验测试通过；
- 数据集 commit 独立。

---

# Phase 2：重写评估脚本，修复统计失真

## 14. 禁止默认满分

重写：

```text
scripts/final_closure_mcp_harness.py
```

或新建：

```text
scripts/production_pilot_eval.py
```

禁止以下逻辑：

```python
expected_ids 为空 -> response_ok 即满分
citation_complete = True
citation_correct = True
unit_ok = True
```

## 15. Retrieval 指标

仅在 `expected_ids` 非空的样本上计算：

```text
Recall@1
Recall@5
MRR@10
nDCG@10
Precision@5
Forbidden Hit Rate@5
```

定义：

```text
Recall@5 = top5 是否命中 expected/acceptable
MRR = 第一个 expected/acceptable 的倒数排名
nDCG = 人工分级相关度计算
Forbidden Hit Rate = top5 中出现 forbidden_id 的样本比例
```

不适用样本必须：

```text
excluded_from_metric=true
```

不得计为 1。

## 16. No-answer 指标

计算：

```text
No-answer Accuracy
False-answer Rate
False-positive Retrieval Rate
False-negative Refusal Rate
```

要求同时检查：

- search 是否 `no_match=true`；
- ask 是否 `answer_mode=no_answer`；
- answer 是否为空；
- sources 是否为空；
- reason 是否符合预期类别。

## 17. Citation 指标

仅对真实生成回答的 Answer 数据集计算。

至少计算：

```text
Answer completion rate
Citation completeness
Citation correctness
Unsupported claim rate
Source precision
Source recall
```

定义：

- Citation completeness：每个回答事实是否至少有一个支持来源；
- Citation correctness：引用来源是否真正支持对应事实；
- Unsupported claim：回答中无人工支持证据的事实比例；
- 不允许 no-answer 样本贡献 Citation 满分。

## 18. 数字单位指标

计算：

```text
Top1 unit accuracy
Top3 expected document recall
Forbidden unit confusion rate
Forbidden document hit rate
Numeric no-answer accuracy
```

空结果规则：

- 应命中但空结果：失败；
- 应 no-answer 且空结果：通过；
- 不得统一默认通过。

## 19. Routing 指标

拆分为：

```text
Mode Accuracy
Recommended Tool Accuracy
Argument Contract Accuracy
Protocol Execution Rate
Task Completion Rate
Timeout-free Completion Rate
```

定义：

- 返回稳定错误只算 Protocol Execution；
- timeout 不能算 Task Completion；
- 空结果是否成功取决于 `expected_task_outcome`；
- route mode 错误不能因下游可调用而算路由正确。

## 20. Transport 一致性

比较：

```text
mode
recommended_tool
no-answer decision
top-5 ordered IDs
task outcome
citation IDs
error code
```

不能只用集合存在交集视为一致。

建议计算：

```text
Exact mode agreement
Exact tool agreement
Top5 Jaccard
Rank correlation
No-answer agreement
Outcome agreement
```

### Phase 2 门槛

- 所有不适用样本从分母剔除；
- 每个指标输出 numerator / denominator；
- 指标代码有单元测试；
- 构造空 expected、空结果、错误结果、timeout 场景，确保不会自动满分；
- 旧评估脚本结果标记为 deprecated。

---

# Phase 3：正式向量索引链路修复

## 21. 当前问题

正式库在部分 MCP 启动方式下出现：

```text
Vector index is EMPTY
```

需要确认以下路径是否一致：

```text
项目根 SHINEHE_HOME
临时 SHINEHE_HOME + 正式 DB 绝对路径
GUI 启动
stdio MCP
streamable-http MCP
CLI
```

## 22. 根因检查

检查：

- 向量索引实际存储目录；
- DB 与 vector store ID 对齐；
- embedding table / sqlite-vec / Chroma 路径；
- config 相对路径解析；
- `SHINEHE_HOME` 对路径的影响；
- migration 后索引加载；
- block ID 与 knowledge ID 映射；
- embedding model 维度；
- index schema/version；
- 读取权限；
- lazy init 是否静默创建了空索引。

## 23. 修复要求

必须实现统一解析函数，例如：

```python
resolve_vector_storage_path(
    config_path,
    shinehe_home,
    storage_data_dir,
    vector_backend
)
```

stdio、HTTP、GUI、CLI 必须共用。

如果 DB 有 blocks，但 vector_count=0，启动时必须：

```text
明确 unhealthy
或
明确降级为 FTS
```

不得静默宣称 semantic search 可用。

## 24. 健康检查

`kb_health_check` 或 capabilities 返回：

```json
{
  "vector": {
    "enabled": true,
    "backend": "",
    "storage_path": "",
    "block_count": 1000,
    "vector_count": 1000,
    "coverage": 1.0,
    "model": "",
    "dimension": 1024,
    "healthy": true,
    "degraded_reason": null
  }
}
```

## 25. 正式向量对照测试

对人工 Retrieval 数据集分别执行：

```text
FTS only
Vector only
Hybrid
Hybrid + reranker
```

输出独立指标，不得只输出最终 blend。

### Phase 3 门槛

- 正式 vector_count > 0；
- vector coverage >= 0.95；
- stdio 与 HTTP 读取同一索引；
- Vector-only 能返回非空合理结果；
- 不再出现未解释的空索引告警；
- 路径对照测试全部通过；
- 正式 DB 不被写入。

---

# Phase 4：真实 Provider timeout 与进程隔离

## 26. 当前问题

非协作同步 SDK timeout 后仍可能：

```json
{
  "cancelled": false,
  "background_work_may_continue": true
}
```

这符合诚实语义，但不满足“真正终止底层调用”的最终强制门槛。

## 27. Provider 分类

列出所有外部调用：

```text
LLM
Embedding
Reranker
URL fetch
OCR（如有）
其他插件 Provider
```

对每个标记：

```text
async cancellable
sync cooperative
sync non-cooperative
```

保存：

```text
docs/architecture/provider-cancellation-matrix.md
```

## 28. Async Provider

必须支持：

```text
connect timeout
read timeout
write timeout
pool timeout
total deadline
retry budget
task cancellation
connection cleanup
```

共享单一 monotonic deadline。

## 29. 非协作同步 Provider

必须使用可终止进程隔离：

```text
spawn worker process
传入最小必要参数
timeout 后 terminate
join
确认退出码
清理临时文件
释放 IPC
限制并发 worker
```

禁止把正式数据库连接对象、API Key 明文或不可序列化 Container 直接传入子进程。

## 30. 熔断与资源保护

增加：

```text
max_provider_workers
max_abandoned_workers = 0
circuit_breaker_failure_threshold
circuit_breaker_cooldown
```

目标：

```text
background_work_may_continue 永远为 false
```

## 31. 测试

覆盖：

- 非协作永久 sleep；
- 永不返回 socket；
- 子进程崩溃；
- Provider 返回超大响应；
- 连续 50 次 timeout；
- timeout 后第三个请求；
- 进程数回到基线；
- 临时文件清理；
- stdio/http 均验证。

### Phase 4 门槛

- 所有生产 Provider 均可取消或可终止；
- timeout 后无存活 worker；
- `background_work_may_continue=false`；
- 连续 50 次进程/线程/连接无增长；
- 服务继续接受 ping/search/ask。

---

# Phase 5：真实 Provider 回答与引用验收

## 32. 测试范围

使用 Answer Citation 数据集，通过真实 Provider 调用：

```text
stdio ask
HTTP ask
stdio ask_with_query
HTTP ask_with_query
```

每条至少执行一次，关键样本执行三次评估稳定性。

## 33. 回答验收

检查：

- 是否回答问题；
- 是否拒绝库外推断；
- 是否包含 unsupported claim；
- 是否引用正确文档；
- source/block IDs 是否真实存在；
- citation 是否能定位到支持文本；
- 两个 Transport 是否一致；
- 重复执行是否出现事实漂移。

## 34. 自动 + 人工混合评估

自动评估：

```text
answer non-empty
source IDs valid
fact coverage
citation ID match
forbidden claim keyword
```

人工复核：

```text
事实正确性
引用支持性
是否过度推断
表达是否误导
```

人工结论记录：

```json
{
  "case_id": "",
  "pass": true,
  "supported_facts": [],
  "unsupported_facts": [],
  "citation_errors": [],
  "reviewer_notes": ""
}
```

## 35. 门槛

```text
Answer completion rate >= 0.90
Citation completeness >= 0.95
Citation correctness >= 0.95
Unsupported claim rate <= 0.05
Source ID validity = 1.00
No-answer leakage <= 0.05
```

---

# Phase 6：真实 Provider 并发抽样

## 36. Search 链路

使用正式向量和正式 Provider 配置，真实 MCP 测试：

```text
并发 1 / 5 / 10 / 20
每档至少 30 次
```

分别记录：

```text
FTS
Vector
Hybrid
Reranker
```

## 37. Ask 链路

真实 Provider 并发：

```text
并发 1 / 3 / 5
每档至少 5 次
```

不得用 invalid LLM 替代。

记录：

```text
success
task_completed
timeout
no_answer
provider_error
P50
P95
P99
token usage
cost estimate
citation validity
```

## 38. 成功定义

MCP 返回 envelope 不是业务成功。

Ask 成功必须：

```text
非预期 timeout
非 provider error
满足预期 answer/no-answer
sources 合法
```

### Phase 6 门槛

- 10 并发 search 成功率 >= 99%；
- 5 并发真实 ask 无进程崩溃；
- 真实 ask task completion >= 90%；
- timeout 后资源恢复；
- 无持续连接增长；
- 成本记录完整。

---

# Phase 7：路由语义收口

## 39. 典型错误修复

重点复测：

```text
文档引用了哪些页面 -> graph
上下游依赖关系 -> graph
与企微有什么关联 -> graph
广西电信企微未来应该怎么发展 -> hybrid
总结主要问题 -> hybrid
列出所有 md 文档 -> structured file_type
今天公司营收是多少 -> no-answer/current info
```

## 40. 路由判定

`route_query` 必须返回：

```text
mode
recommended_tool
recommended_arguments
recommended_flow
routing_source
fallback_used
confidence
reason_codes
```

## 41. 执行结果

每个 Routing 样本依次：

1. route；
2. 校验 mode；
3. 校验 tool；
4. 原样执行 arguments；
5. 检查 task outcome；
6. 检查是否 timeout；
7. 检查结果类型。

### Phase 7 门槛

```text
Mode Accuracy >= 0.95
Tool Accuracy >= 0.95
Argument Contract Accuracy = 1.00
Protocol Execution Rate = 1.00
Task Completion Rate >= 0.90
Timeout-free Completion Rate >= 0.90
```

禁止把 timeout、空结果和 validation error 一律计为成功。

---

# Phase 8：最终真实 MCP 综合验收

## 42. 运行矩阵

必须完成：

| 环境 | Transport | 数据 | Provider |
|---|---|---|---|
| 临时测试环境 | stdio | 临时库 | controlled |
| 临时测试环境 | HTTP | 临时库 | controlled |
| 正式只读环境 | stdio | 正式库 | real |
| 正式只读环境 | HTTP | 正式库 | real |

## 43. 工具覆盖

实际调用：

```text
initialize
tools/list
kb_capabilities
kb_health_check
search
search_fulltext
route_query
execute_query
graph_traverse
ask
ask_with_query
read
tags
ping
```

## 44. 最终长稳抽样

不需要再次重复完整两小时受控 invalid Provider 长稳。

本轮执行：

```text
正式向量 + 真实 Provider HTTP：30 分钟
正式向量 + 真实 Provider stdio：15 分钟
```

每 3 分钟调用：

```text
search
route_query
ask
kb_health_check
```

记录：

```text
RSS
threads
processes
connections
vector coverage
provider errors
timeouts
task completion
citation validity
```

### Phase 8 门槛

- 正式向量链路持续健康；
- 无索引路径漂移；
- 真实 Provider 无持续失败；
- timeout 后 worker 全部终止；
- MCP 会话可恢复；
- 正式 DB 未变化。

---

# Phase 9：工程门禁与 CI

## 45. 测试

运行：

```powershell
ruff check src tests scripts
mypy src
pytest tests -q
```

新增测试至少覆盖：

```text
tests/eval/test_metric_denominators.py
tests/eval/test_no_default_full_score.py
tests/eval/test_citation_metric_scope.py
tests/eval/test_numeric_empty_result_fails.py
tests/eval/test_routing_task_completion.py
tests/stability/test_formal_vector_path_resolution.py
tests/stability/test_provider_process_termination.py
tests/stability/test_real_provider_timeout_recovery.py
```

## 46. CI

CI 必须执行：

- dataset schema validation；
- metric correctness tests；
- unit tests；
- vector path smoke；
- controlled process termination test；
- ruff；
- mypy。

真实付费 Provider 测试可用手动 workflow：

```text
.github/workflows/real-provider-validation.yml
```

要求：

- workflow_dispatch；
- secrets 安全；
- 输出脱敏 artifact；
- 不在 PR 自动触发产生费用。

---

# Phase 10：最终报告与版本结论

## 47. 产物

```text
artifacts/production-pilot-final-validation/
  baseline.json
  dataset-validation.json
  retrieval-stdio.jsonl
  retrieval-http.jsonl
  no-answer-stdio.jsonl
  no-answer-http.jsonl
  numeric-units.jsonl
  routing.jsonl
  answers-citations.jsonl
  metrics.json
  metric-denominators.json
  vector-path-matrix.json
  real-provider-concurrency.json
  provider-timeout-process.json
  formal-mcp-soak.json
  failures.jsonl
```

## 48. 报告

生成：

```text
docs/reports/mcp-production-pilot-final-validation-YYYY-MM-DD.md
```

报告必须明确区分：

```text
历史无效指标
新可信指标
人工 ground truth 数量
排除样本数量
真实 vector 指标
真实 FTS 指标
真实 hybrid 指标
真实 reranker 指标
真实 Provider 指标
controlled Provider 指标
协议成功
业务任务成功
```

## 49. 最终门槛

### 数据集

- 所有参与检索指标的样本都有人工 ground truth；
- 无 PAD 自动满分；
- 无 `hit_or_empty` 进入 Recall；
- corpus snapshot 已记录。

### Retrieval

```text
Recall@5 >= 0.90
MRR@10 >= 0.85
nDCG@10 >= 0.85
Precision@5 >= 0.70
Forbidden Hit Rate@5 <= 0.05
```

### No-answer

```text
No-answer Accuracy >= 0.90
False-answer Rate <= 0.05
False-positive Retrieval Rate <= 0.10
```

### Numeric

```text
Top1 unit accuracy >= 0.95
Top3 expected document recall >= 0.95
Forbidden unit confusion <= 0.05
```

### Citation

```text
Answer completion >= 0.90
Citation completeness >= 0.95
Citation correctness >= 0.95
Unsupported claim rate <= 0.05
Source ID validity = 1.00
```

### Routing

```text
Mode Accuracy >= 0.95
Tool Accuracy >= 0.95
Argument Contract = 1.00
Task Completion >= 0.90
Timeout-free Completion >= 0.90
```

### Vector

```text
vector coverage >= 0.95
stdio/http vector path identical
Vector-only evaluation completed
Hybrid evaluation completed
Reranker evaluation completed
无 Vector index is EMPTY
```

### Timeout

```text
所有生产 Provider 可取消或可终止
background_work_may_continue=false
50 次 timeout 无存活 worker
进程/线程/连接回到基线
```

### Real Provider

```text
真实 Provider ask 并发完成
5 并发无崩溃
task completion >= 0.90
真实回答引用验收通过
```

### Engineering

```text
ruff 0 error
mypy 0 error
pytest 0 failed
CI 通过
正式 DB 未变化
```

---

# 50. Agent 执行顺序

必须严格按顺序：

```text
Phase 0
Phase 1
Phase 2
Phase 3
Phase 4
Phase 5
Phase 6
Phase 7
Phase 8
Phase 9
Phase 10
```

Gate：

- Phase 1 未完成，不得计算新指标；
- Phase 2 未通过，不得发布任何准确率；
- Phase 3 未通过，不得声称 semantic/hybrid 已验证；
- Phase 4 未通过，不得声称真正取消；
- Phase 5 未通过，不得声称 Citation 达标；
- Phase 6 未通过，不得声称真实 Provider 可生产；
- 任一强制指标失败，最终只能输出“未达到生产试点门槛”。

---

# 51. 禁止事项

禁止：

- 修改阈值只为让指标通过；
- 删除失败样本；
- 把 expected IDs 留空后计满分；
- 用 LIKE 自动生成最终 ground truth；
- 用当前系统结果作为标准答案；
- Citation 不适用样本计 1；
- 数字单位空结果计通过；
- timeout envelope 计 task completed；
- validation error 计路由语义正确；
- controlled invalid LLM 冒充 real provider；
- FTS 结果冒充 vector 验收；
- 将 `NOT TESTED` 写成 PASS；
- 修改正式数据库；
- 泄露 Provider secrets。

---

# 52. 每阶段提交建议

```text
chore(validation): freeze production-pilot baseline
test(eval): add human-grounded production pilot datasets
fix(eval): correct metric scopes and denominators
fix(vector): unify formal vector index path resolution
fix(provider): terminate non-cooperative calls in worker processes
test(rag): add real answer and citation validation
test(provider): add real provider concurrency sampling
fix(router): close semantic routing accuracy gaps
test(mcp): run formal real-provider MCP validation
ci(validation): add metric and provider validation gates
docs(validation): publish final production-pilot report
```

---

# 53. Agent 最终回复格式

最终只输出：

```text
1. 基线 Commit SHA
2. 最终 Commit SHA
3. 分支名
4. 提交列表
5. 人工 Ground Truth 样本数
6. 排除样本数及原因
7. 新 Retrieval 指标及分母
8. No-answer 指标及分母
9. 数字单位指标及分母
10. Citation 指标及分母
11. Routing 六项指标
12. FTS-only 指标
13. Vector-only 指标
14. Hybrid 指标
15. Hybrid+Reranker 指标
16. 正式向量 coverage 和路径
17. 非协作 Provider 终止结果
18. 真实 Provider 并发结果
19. 真实回答质量抽样
20. stdio 实际 MCP 结果
21. HTTP 实际 MCP 结果
22. 正式环境短长稳结果
23. Ruff/mypy/pytest
24. CI 状态
25. 正式数据库是否变化
26. 未解决问题
27. NOT TESTED
28. 报告路径
29. 是否达到生产试点门槛
```

最终结论只能是以下二选一：

```text
达到生产试点门槛
```

或：

```text
未达到生产试点门槛
```
