# ShineHeKB 三项基础问题修复 Spec

**项目目录：** `D:\ClaudeCodeWorkSpace\projects\knowledge-base`  
**建议分支：** `fix/production-pilot-foundation-three`  
**前置版本：** v1.10.4  
**目标：** 在不提前进行最终生产试点验收的前提下，修复当前评估可信度和运行链路中的三个基础问题。

---

# 1. 本轮只允许处理的三个问题

本轮严格限定为以下三项：

1. 将当前规则自动生成的“人工 Ground Truth”改造为真正可审计的人工复核数据；
2. 修复 Routing Harness 覆盖 Agent 推荐参数、空结果恒判 `non_empty` 等问题；
3. 将可终止进程隔离真正接入正式 Provider 调用路径，而不只停留在测试辅助函数。

本轮不负责：

- 最终生产试点结论；
- 全量指标重跑；
- 为通过 Precision/nDCG/Numeric 而调整阈值；
- 大规模检索排序优化；
- 修改最终门槛；
- 发布新的“达到生产试点门槛”结论。

修复完成后只能输出：

```text
三个基础问题已完成修复，等待独立全量验收
```

不能输出：

```text
达到生产试点门槛
```

---

# 2. 强制执行原则

## 2.1 修复与验收分离

本轮可以执行：

- 单元测试；
- 定向集成测试；
- 小规模 MCP smoke；
- 数据 schema 校验；
- Provider timeout 定向测试。

本轮不得把这些定向结果冒充最终全量验收。

## 2.2 测试驱动

每个问题必须遵循：

```text
先新增失败测试
确认失败
修改生产代码或数据流程
运行定向测试
运行相关回归
独立 commit
```

## 2.3 正式数据库保护

- 正式 `data\kb.db` 只读；
- 人工标注不得修改正式 DB；
- Provider 隔离测试不得对正式库写入；
- 所有临时测试使用独立 `SHINEHE_HOME`；
- 记录修复前后正式 DB SHA256。

## 2.4 不得修改门槛掩盖失败

禁止：

- 修改数据集使现有代码更容易通过；
- 删除难例；
- 把规则自动匹配直接标记为 human；
- 在 Routing Harness 中重新构造推荐参数；
- 把 timeout、validation error、空结果一律算成功；
- 仅测试进程隔离工具函数而不接入正式 Provider。

---

# Phase 0：基线冻结

## 3. 创建分支

```powershell
cd D:\ClaudeCodeWorkSpace\projects\knowledge-base
git checkout master
git pull
git checkout -b fix/production-pilot-foundation-three
```

## 4. 记录基线

生成：

```text
artifacts/foundation-three-fixes/baseline.json
```

至少记录：

```text
baseline_commit_sha
version
branch
python_version
formal_db_path
formal_db_size
formal_db_sha256
retrieval_dataset_count
no_answer_dataset_count
numeric_dataset_count
routing_dataset_count
answer_citation_dataset_count
current_annotation_source_counts
current_provider_timeout_path
current_routing_harness_sha
```

## 5. 旧问题复现

必须保存三个问题的失败证据：

```text
artifacts/foundation-three-fixes/repro-ground-truth.json
artifacts/foundation-three-fixes/repro-routing-harness.jsonl
artifacts/foundation-three-fixes/repro-provider-isolation.json
```

### Phase 0 门槛

- 没有修改生产代码；
- 已冻结正式 DB SHA；
- 三个问题都能明确复现；
- 基线独立 commit。

建议 commit：

```text
chore(validation): freeze three-foundation-fix baseline
```

---

# Phase 1：真正人工 Ground Truth 审核机制

# 6. 当前问题

当前数据由 `scripts/build_production_pilot_datasets.py` 根据：

- `title_any`
- `content_any`
- `content_must`
- 正则匹配

自动挑选 expected / acceptable IDs，然后直接写入：

```json
{
  "annotation_source": "human"
}
```

该流程属于规则辅助弱标注，不是可审计的人工 Ground Truth。

---

# 7. 数据角色拆分

必须把数据分成三层：

```text
候选生成层
人工审核层
冻结发布层
```

建议目录：

```text
tests/eval/datasets/candidates/
tests/eval/datasets/reviewed/
tests/eval/datasets/frozen/
```

对应文件：

```text
candidates/production_pilot_retrieval.candidates.jsonl
candidates/production_pilot_numeric_units.candidates.jsonl
candidates/production_pilot_answer_citations.candidates.jsonl

reviewed/production_pilot_retrieval.reviewed.jsonl
reviewed/production_pilot_numeric_units.reviewed.jsonl
reviewed/production_pilot_answer_citations.reviewed.jsonl

frozen/production_pilot_retrieval.jsonl
frozen/production_pilot_no_answer.jsonl
frozen/production_pilot_numeric_units.jsonl
frozen/production_pilot_routing.jsonl
frozen/production_pilot_answer_citations.jsonl
```

正式验收只能读取 `frozen/`。

---

# 8. 候选生成规则

`build_production_pilot_datasets.py` 只能生成候选，不得直接生成正式 Ground Truth。

候选数据字段：

```json
{
  "id": "RET-001",
  "query": "企微运营管理办法",
  "candidate_expected_ids": [],
  "candidate_acceptable_ids": [],
  "candidate_forbidden_ids": [],
  "generated_by": "scripts/build_production_pilot_datasets.py",
  "generation_rule": {},
  "corpus_snapshot_sha": "",
  "annotation_source": "rule_assisted_candidate",
  "human_review_status": "pending"
}
```

禁止候选文件出现：

```json
{
  "annotation_source": "human"
}
```

---

# 9. 人工审核字段

每个进入 reviewed 的样本必须包含：

```json
{
  "id": "RET-001",
  "query": "",
  "expected_ids": [],
  "acceptable_ids": [],
  "forbidden_ids": [],
  "review": {
    "status": "approved|rejected|needs_adjudication",
    "primary_reviewer": "",
    "primary_reviewed_at": "",
    "secondary_reviewer": "",
    "secondary_reviewed_at": "",
    "adjudicator": "",
    "adjudicated_at": "",
    "decision_notes": "",
    "evidence_checked": [
      {
        "knowledge_id": "",
        "title": "",
        "decision": "expected|acceptable|forbidden|irrelevant",
        "reason": "",
        "checked_title": true,
        "checked_body": true
      }
    ]
  },
  "annotation_source": "human_reviewed",
  "corpus_snapshot_sha": ""
}
```

要求：

- `expected_ids` 必须至少 1 个；
- 每个 expected / acceptable / forbidden ID 都有审核记录；
- 审核必须检查标题和正文；
- reviewer 不得为空；
- reviewed_at 必须为 ISO 8601；
- 二次审核至少覆盖全部样本；
- 有争议则进入 adjudication；
- `needs_adjudication` 不得进入 frozen。

---

# 10. 人工审核工具

新增一个不会自动决定结果的审核辅助脚本：

```text
scripts/review_production_pilot_ground_truth.py
```

功能：

```text
按样本显示 query
显示候选文档 title
显示正文摘要
支持打开完整正文
允许人工选择 expected / acceptable / forbidden / irrelevant
记录 reviewer 和时间
保存 reviewed JSONL
不得自动把候选全部接受
```

可以是 CLI，不要求 GUI。

建议命令：

```powershell
python scripts/review_production_pilot_ground_truth.py `
  --dataset retrieval `
  --reviewer reviewer_a
```

二次复核：

```powershell
python scripts/review_production_pilot_ground_truth.py `
  --dataset retrieval `
  --reviewer reviewer_b `
  --second-review
```

---

# 11. 冻结工具

新增：

```text
scripts/freeze_production_pilot_datasets.py
```

只有满足以下条件的样本才能进入 frozen：

```text
review.status == approved
primary_reviewer 非空
secondary_reviewer 非空
expected_ids 非空（retrieval）
所有标注 ID 都有 evidence_checked
corpus_snapshot_sha 与当前冻结快照一致
无 needs_adjudication
```

输出：

```text
artifacts/foundation-three-fixes/dataset-freeze-summary.json
```

至少记录：

```text
candidate_count
reviewed_count
approved_count
rejected_count
adjudicated_count
frozen_count
reviewer_counts
missing_review_fields
corpus_snapshot_sha
```

---

# 12. No-answer 与 Routing 数据

No-answer 和 Routing 也必须增加审核元数据。

可保留程序生成初稿，但冻结前必须具备：

```json
{
  "annotation_source": "human_reviewed",
  "review": {
    "status": "approved",
    "primary_reviewer": "",
    "secondary_reviewer": ""
  }
}
```

Routing 的 expected mode/tool/outcome 必须由人工确认，不能只依据当前路由器行为。

---

# 13. Answer Citation 数据审核

每个事实必须检查真实正文，不允许只从标题推断。

字段：

```json
{
  "expected_answer_facts": [
    {
      "fact_id": "F1",
      "statement": "",
      "supporting_knowledge_ids": [],
      "supporting_block_ids": [],
      "supporting_quotes": [
        {
          "knowledge_id": "",
          "block_id": "",
          "quote": "",
          "reason": ""
        }
      ]
    }
  ]
}
```

要求：

- 每个 fact 至少一个 supporting block；
- supporting quote 必须来自对应 block；
- 不得只写泛化事实，如“管理办法管理某事项”；
- 至少包含一定比例的条款、条件、流程、数字和例外情况问题。

---

# 14. 数据 schema 测试

新增：

```text
tests/eval/test_ground_truth_review_metadata.py
tests/eval/test_frozen_dataset_only.py
tests/eval/test_no_rule_assisted_marked_human.py
tests/eval/test_answer_fact_has_block_quote.py
tests/eval/test_ground_truth_corpus_snapshot.py
```

必须验证：

- candidate 不能标记 human；
- frozen 全部 human_reviewed；
- reviewer 字段完整；
- retrieval expected 非空；
- answer fact 有 block 和 quote；
- needs_adjudication 不进入 frozen；
- 正式 harness 只读 frozen。

### Phase 1 门槛

- 三层数据结构完成；
- 构建脚本不再直接写正式 GT；
- 人工审核 CLI 可用；
- 冻结脚本可用；
- schema 测试通过；
- 至少完成所有现有样本的双人复核或明确标记未完成；
- 未完成复核时不得进入 Phase 4 最终收尾。

建议 commit：

```text
fix(eval): require audited human review before dataset freeze
```

---

# Phase 2：Routing Harness 修复

# 15. 当前问题

现有 Harness 存在：

1. 对 search / ask / ask_with_query 覆盖 `recommended_arguments`；
2. 没有真正原样执行 Agent 推荐参数；
3. 空结果判断中存在：

```python
task_outcome = "non_empty" if condition else "non_empty"
```

4. timeout 识别不完整；
5. graph 无参数时直接跳过，没有明确区分 route contract failure；
6. validation error、transport success、task success 边界不清。

---

# 16. 原样执行要求

Routing Harness 必须执行：

```python
exec_args = deepcopy(recommended_arguments)
await call_tool(client, recommended_tool, exec_args)
```

禁止针对工具重新拼接：

```python
{"query": original_query}
{"question": original_query}
```

只有在检查 recommended arguments 之前，任何参数都不得覆盖。

记录：

```text
recommended_arguments_raw
executed_arguments
arguments_exact_match
```

要求：

```text
executed_arguments == recommended_arguments_raw
```

---

# 17. 参数契约校验

执行前必须校验：

```text
required_argument_keys
forbidden_argument_keys
argument_types
no_unexpected_mutation
```

结果字段：

```json
{
  "argument_contract": {
    "required_keys_present": true,
    "types_valid": true,
    "raw_equals_executed": true,
    "missing_keys": [],
    "unexpected_mutations": []
  }
}
```

---

# 18. 任务结果统一分类

新增统一函数：

```python
classify_task_outcome(tool_name, response_payload) -> str
```

允许值：

```text
non_empty
empty
no_answer
graph_result
structured_result
validation_error
provider_error
mcp_error
transport_error
timeout
cancelled
unknown
```

禁止默认返回 `non_empty`。

---

# 19. non_empty 判定

只有满足以下条件才可为 `non_empty`：

- search：结果列表长度 > 0；
- ask：answer 非空且 answer_mode 不是 no_answer/timeout/error；
- ask_with_query：同 ask；
- graph：nodes 或 edges 非空；
- structured：rows/items/data 列表非空；
- read：返回正文非空。

空数组、空 answer、空 data 必须为：

```text
empty
```

或符合拒答时：

```text
no_answer
```

---

# 20. Timeout 判定

同时检查：

```text
MCP transport timeout
payload.error_code
payload.route.mode
payload.answer_mode
payload.meta.timeout
warnings
```

若任何正式 timeout 标志出现：

```text
timed_out = true
task_outcome = timeout
task_completed = false
```

---

# 21. Graph 路由执行

Graph 推荐必须包含可执行的：

```text
start_ids
或
start_type + start selector
```

若 Graph 意图无法从自然语言获得 start_id，则 route 应返回：

```text
recommended_flow
```

例如先 search 定位节点，再 graph_traverse。

Harness 必须支持多步推荐流：

```json
{
  "recommended_flow": [
    {"tool": "search", "arguments": {}},
    {"tool": "graph_traverse", "arguments_from_previous": {}}
  ]
}
```

禁止因为 Graph 缺少 start_ids 就直接把业务任务当 validation error 而不评估推荐流。

---

# 22. Routing 结果字段

每条必须保存：

```json
{
  "id": "",
  "query": "",
  "expected_mode": "",
  "expected_tool": "",
  "got_mode": "",
  "got_tool": "",
  "recommended_arguments_raw": {},
  "executed_arguments": {},
  "arguments_exact_match": true,
  "protocol_ok": true,
  "route_contract_ok": true,
  "timed_out": false,
  "task_outcome": "",
  "expected_task_outcome": "",
  "task_completed": false,
  "route_elapsed_ms": 0,
  "exec_elapsed_ms": 0,
  "raw_route_response": {},
  "raw_exec_response": {}
}
```

---

# 23. Routing 指标

保留：

```text
Mode Accuracy
Recommended Tool Accuracy
Argument Contract Accuracy
Protocol Execution Rate
Task Completion Rate
Timeout-free Completion Rate
```

新增：

```text
Raw Argument Preservation Rate
Empty-result Honesty Rate
Timeout Classification Accuracy
Recommended Flow Execution Rate
```

---

# 24. Routing 单元测试

新增：

```text
tests/eval/test_routing_harness_preserves_arguments.py
tests/eval/test_routing_empty_is_not_non_empty.py
tests/eval/test_routing_timeout_is_not_complete.py
tests/eval/test_routing_validation_error_classification.py
tests/eval/test_routing_graph_flow_execution.py
tests/eval/test_routing_raw_response_saved.py
```

必须构造：

1. search 返回空数组；
2. ask 返回空 answer；
3. ask timeout；
4. graph 返回空 nodes；
5. structured 返回空 rows；
6. validation error；
7. Agent 推荐参数与原 query 不同；
8. ask_with_query 含 `search_query`；
9. multi-step graph flow；
10. transport error。

### Phase 2 门槛

- Harness 不再覆盖 recommended arguments；
- 删除恒 `non_empty` bug；
- 任务结果分类统一；
- 所有定向测试通过；
- 小规模 10 条 MCP smoke 结果可人工检查；
- 本 Phase 不宣称 Routing 最终通过。

建议 commit：

```text
fix(eval): preserve routing arguments and classify real task outcomes
```

---

# Phase 3：正式 Provider 进程隔离接线

# 25. 当前问题

项目已实现：

```text
run_in_terminable_process
run_with_deadline(..., isolate="process")
```

但正式 Ask 仍默认使用 thread isolate。

因此当前只能证明“隔离工具可用”，不能证明正式 Provider 已真正接线。

---

# 26. Provider 调用清单

先建立：

```text
docs/architecture/provider-runtime-isolation-map.md
```

列出所有真实调用点：

```text
LLM generation
Embedding
Reranker
OCR
URL fetch
其他同步 SDK
```

每项记录：

```text
module
function
provider
sync/async
cooperative/non-cooperative
network timeout support
selected isolation mode
reason
production call path
test coverage
```

---

# 27. 隔离策略

## 27.1 不应一刀切把整个 RAG Container 放入子进程

禁止直接将以下对象传入 spawn：

```text
AppContainer
Database connection
SQLite connection
keyring object
HTTP session
logger handler
不可序列化模型实例
```

## 27.2 推荐隔离粒度

对非协作同步 Provider：

```text
父进程完成检索和参数准备
构造最小可序列化 ProviderRequest
子进程加载必要配置
子进程执行单次 Provider 调用
子进程只返回 ProviderResponse
父进程继续组装结果
```

---

# 28. Provider Request / Response

新增可序列化结构，例如：

```python
@dataclass
class ProviderRequest:
    provider_type: str
    base_url: str
    model: str
    payload: dict
    timeout_seconds: float
    secret_env_key: str
```

禁止把 API Key 写入 artifact 或日志。

子进程应从环境变量读取密钥，或使用安全的最小传递机制。

ProviderResponse：

```python
@dataclass
class ProviderResponse:
    ok: bool
    data: dict | list | str | None
    error_type: str | None
    error_message: str | None
    elapsed_ms: int
```

---

# 29. 正式调用点接线

至少检查并接入：

```text
LLM ask generation
Embedding request
Reranker request
OCR（若生产启用）
```

每个调用点必须显式声明：

```python
isolation_mode = "process" | "async" | "thread_cooperative"
```

禁止依赖默认值。

---

# 30. Ask 路径

正式 Ask 不得继续模糊使用：

```python
run_with_deadline(runner, timeout)
```

应改为显式策略：

```python
run_provider_operation(
    operation="llm_generate",
    request=...,
    isolation_mode=resolved_mode,
    timeout=...
)
```

Ask 的最终 timeout envelope 必须来自真实底层状态：

```json
{
  "cancelled": true,
  "background_work_may_continue": false,
  "worker_terminated": true,
  "provider_operation": "llm_generate"
}
```

---

# 31. Embedding 与 Reranker

对 HTTP 客户端已具备严格 connect/read/total timeout 的，可标为 cooperative。

但必须测试：

- 连接建立后永不返回；
- read 卡死；
- DNS/连接失败；
- 服务返回超大响应；
- 请求取消后连接释放。

如果客户端不能可靠取消，改为 process。

---

# 32. 子进程安全

必须：

- 使用 `spawn`；
- 不继承正式 DB write connection；
- 不输出 secret；
- 不把完整 prompt 写入默认日志；
- 限制 worker 数量；
- timeout 后 terminate + join；
- 必要时 kill；
- 关闭 queue/pipe；
- 清理临时文件；
- 记录子进程 PID 和退出码但不记录密钥。

---

# 33. Provider 运行状态

增加运行时诊断：

```json
{
  "provider_isolation": {
    "active_workers": 0,
    "max_workers": 8,
    "abandoned_workers": 0,
    "circuit_open": false,
    "last_timeout_operation": "",
    "last_worker_exit_code": 0
  }
}
```

可加入 `kb_health_check`，但不得暴露秘密。

---

# 34. Provider 测试

新增：

```text
tests/providers/test_llm_process_isolation_wiring.py
tests/providers/test_embedding_timeout_cleanup.py
tests/providers/test_reranker_timeout_cleanup.py
tests/stability/test_ask_uses_explicit_isolation_mode.py
tests/stability/test_provider_worker_pid_terminated.py
tests/stability/test_provider_timeout_envelope_truthful.py
tests/stability/test_provider_secret_not_logged.py
tests/stability/test_provider_worker_limit.py
```

必须覆盖：

1. LLM 永久阻塞；
2. Embedding 永久阻塞；
3. Reranker 永久阻塞；
4. 子进程异常退出；
5. 连续 50 次 timeout；
6. timeout 后正常请求；
7. worker 数量回落；
8. `background_work_may_continue=false`；
9. 正式 Ask 确实调用隔离层；
10. 日志无密钥。

---

# 35. 小规模真实 Provider 验证

成本受控执行：

```text
LLM 真实正常请求 3 次
LLM 人工短 timeout 2 次
Embedding 正常 3 次
Reranker 正常 3 次（若启用）
```

只验证接线和终止，不做全量质量评估。

保存：

```text
artifacts/foundation-three-fixes/provider-wiring-smoke.jsonl
```

### Phase 3 门槛

- 正式 Provider 调用点显式接线；
- 非协作调用使用 process；
- Ask 不再依赖默认 thread；
- timeout 后 worker PID 消失；
- 50 次 timeout 无资源增长；
- 正常请求可恢复；
- secrets 未泄漏；
- 定向 smoke 通过。

建议 commit：

```text
fix(provider): wire terminable isolation into production provider paths
```

---

# Phase 4：三项修复综合验证

# 36. 定向测试

运行：

```powershell
pytest tests/eval/test_ground_truth_review_metadata.py -q
pytest tests/eval/test_frozen_dataset_only.py -q
pytest tests/eval/test_routing_harness_preserves_arguments.py -q
pytest tests/eval/test_routing_empty_is_not_non_empty.py -q
pytest tests/eval/test_routing_timeout_is_not_complete.py -q
pytest tests/providers/ -q
pytest tests/stability/test_ask_uses_explicit_isolation_mode.py -q
pytest tests/stability/test_provider_worker_pid_terminated.py -q
```

## 37. 相关回归

运行：

```powershell
pytest tests/eval/ -q
pytest tests/stability/ -q
pytest tests/test_mcp_contract.py -q
pytest tests/test_public_search_contract.py -q
pytest tests/test_public_ask_contract.py -q
ruff check src tests scripts evals
mypy src
```

## 38. 正式 DB 校验

记录：

```text
formal_db_sha256_before
formal_db_sha256_after
formal_db_size_before
formal_db_size_after
```

必须完全一致。

## 39. 输出产物

```text
artifacts/foundation-three-fixes/
  baseline.json
  repro-ground-truth.json
  repro-routing-harness.jsonl
  repro-provider-isolation.json
  dataset-freeze-summary.json
  routing-smoke.jsonl
  provider-wiring-smoke.jsonl
  regression-summary.json
  formal-db-integrity.json
  failures.jsonl
```

---

# 40. 最终报告

生成：

```text
docs/reports/production-pilot-foundation-three-fixes-YYYY-MM-DD.md
```

必须包含：

1. 基线和最终 SHA；
2. 三个问题根因；
3. 修改文件；
4. Ground Truth 审核机制；
5. 实际双人审核完成比例；
6. Routing Harness 修复；
7. 原样参数执行证据；
8. 空结果/timeout 分类测试；
9. Provider 正式接线图；
10. timeout worker PID 终止证据；
11. 定向测试；
12. 相关回归；
13. 正式 DB 完整性；
14. 未解决问题；
15. NOT TESTED；
16. 是否可以进入独立全量验收。

---

# 41. 最终判定

只有以下全部满足才允许输出：

```text
三个基础问题已完成修复，可以进入独立全量验收
```

条件：

- frozen 数据全部 `human_reviewed`；
- 双人审核字段完整；
- Answer fact 有 supporting block 和 quote；
- Routing 参数 100% 原样执行；
- 空结果不再判 non_empty；
- timeout 不再判完成；
- 正式 Ask/Embedding/Reranker 已显式接入隔离策略；
- 非协作调用 timeout 后 worker 真正消失；
- 正式 DB 未变化；
- 定向及相关回归通过。

任一项失败必须输出：

```text
三个基础问题尚未全部修复，不能进入全量验收
```

---

# 42. Agent 最终回复格式

```text
1. 基线 Commit SHA
2. 最终 Commit SHA
3. 分支名
4. 提交列表
5. Ground Truth candidate/reviewed/frozen 数量
6. 双人审核完成比例
7. 争议样本数量
8. Answer supporting block/quote 覆盖率
9. Routing 参数原样执行率
10. 空结果诚实分类结果
11. Timeout 分类结果
12. Graph 推荐流执行结果
13. 正式 Provider 接线清单
14. 进程隔离实际调用点
15. 50 次 timeout 资源结果
16. 正常请求恢复结果
17. secrets 泄漏检查
18. 定向 pytest
19. 相关回归
20. Ruff/mypy
21. 正式 DB 是否变化
22. 未解决问题
23. NOT TESTED
24. 报告路径
25. 是否可以进入独立全量验收
```
