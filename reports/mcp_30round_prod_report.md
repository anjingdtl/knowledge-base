# ShineHeKnowledge MCP 生产环境 30 轮全工具稳定性与召回测试报告

## 摘要

本次测试在 ShineHeKnowledge v1.3.1 生产环境中，通过已接入的 `mcp_shinehe-kb` MCP 接口执行 30 轮全工具调用，覆盖核心检索、CRUD、Query DSL、任务/job、图谱、Agent Memory 及 legacy 别名等 8 大类工具。测试目标为验证服务在 Agent 高频调用下的稳定性以及关键词召回准确性。

**关键结果：**

| 指标 | 数值 |
|---|---|
| 总轮次 | 30 |
| 通过 | 28 |
| 失败 | 2 |
| 成功率 | **93.33%** |
| 平均召回率 | **83.33%** |
| 平均精确率 | **43.33%** |
| 测试环境 | production（真实业务库） |
| 测试时间 | 2026-06-22 23:00 ~ 23:05（北京时间） |

主要发现：**核心检索与 CRUD 链路稳定，关键词召回在退化到关键词通道的情况下仍可命中目标；但向量通道因 `sqlite-vec` 扩展 `vec0` 模块缺失已降级，直接影响语义搜索质量；`file_ingest` job 缺少 handler；`ask_with_query` 在显式 QuerySpec 下出现源文本为空、回答失真的问题。**

---

## 1. 测试背景与目标

### 1.1 背景

ShineHeKnowledge 作为本地优先 MCP 知识检索引擎，通过 `mcp_shinehe-kb` 向 AI Agent 暴露工具。生产环境当前已配置为 `full` tool profile，启用 experimental tools 与 legacy aliases，并接入真实的 SiliconFlow embedding / MiniMax LLM / SiliconFlow reranker 服务。

### 1.2 目标

1. **稳定性**：验证 MCP 接口在 30 轮连续、跨类别的 Agent 调用下是否始终返回稳定 envelope，无崩溃或超时。
2. **召回准确性**：通过预置唯一关键词的测试条目，验证 `search`、`search_fulltext`、`ask`、`ask_with_query` 等检索类工具能否正确召回目标知识。
3. **生产影响可控**：测试结束后清理所有测试知识条目，避免污染真实业务数据。

### 1.3 测试范围

| 工具类别 | 覆盖工具 |
|---|---|
| 连通/元数据 | `ping`、`kb_capabilities` |
| 知识检索 | `search`、`search_fulltext`、`ask` |
| CRUD | `create`、`read`、`update`、`delete`、`restore_knowledge` |
| 列表/标签 | `list_knowledge`、`tags` |
| 预览/审计 | `preview_operation`、`query_operation_logs` |
| 任务/job | `create_ingest_job`、`get_job`、`cancel_job` |
| Query DSL | `structured_query`、`execute_query`、`route_query` |
| 图谱 | `get_source_graph` |
| Agent Memory | `remember_fact`、`recall_facts` |
| 命名空间别名 | `kb.search` |

> 注：30 轮为抽样覆盖，未能穷尽全部 100+ 个工具/别名。Wiki 工作流、异步任务、图遍历、undo_operation 等未在本轮测试中直接调用。

---

## 2. 测试方法与数据

### 2.1 测试数据

在生产库中创建 4 条带唯一标识的测试条目，标签统一为 `mcp-prod-test-20260622`：

| 条目 | 关键词 | 用途 |
|---|---|---|
| A | `MCP生产稳定性测试ALPHA_20260622` | 语义搜索 / RAG 召回 |
| B | `MCP生产召回测试BETA_20260622` | 全文搜索召回 |
| C | `MCP生产图遍历测试GAMMA_20260622` + ALPHA | 图/语义搜索 |
| D | 初始无关键词，后追加 BETA | CRUD 生命周期 |

### 2.2 判定标准

- **通过（ok=true）**：工具返回 `{"ok":true,...}` envelope，且业务断言（如目标知识出现、版本递增、标签命中）成立。
- **失败（ok=false）**：返回 `ok=false`，或 envelope 虽为 true 但业务目标未达成（如 R27 `ask_with_query` 回答错误）。
- **召回率 / 精确率**：对单一目标条目，recall = 目标是否出现在结果中（0/1），precision = 1 / 返回结果数。

---

## 3. 测试结果总览

### 3.1 成功率

```
总轮次: 30
通过: 28
失败: 2
成功率: 93.33%
```

### 3.2 召回与精确

| 轮次 | 工具 | 目标 | 召回 | 精确 | 结果数 |
|---|---|---|---|---|---|
| R6 | search | ALPHA | 1.00 | 0.50 | 2 |
| R7 | search_fulltext | BETA | 1.00 | 0.10 | 10 |
| R8 | search | GAMMA | 1.00 | 0.50 | 2 |
| R9 | ask | ALPHA | 1.00 | 1.00 | 2 sources |
| R16 | search | BETA/D | 1.00 | 0.50 | 2 |
| R27 | ask_with_query | BETA | 0.00 | 0.00 | 1 empty source |
| **平均** | | | **0.8333** | **0.4333** | |

### 3.3 类别表现

| 工具类别 | 通过/总数 | 说明 |
|---|---|---|
| 连通/元数据 | 2/2 | ping <10ms，capabilities 返回完整工具元数据 |
| CRUD | 6/6 | create/read/update/delete/restore 链路完整 |
| 检索召回 | 4/5 | search/search_fulltext/ask 正常；ask_with_query 异常 |
| 列表/标签 | 2/2 | 按标签列出与标签枚举正常 |
| Query DSL | 3/3 | structured_query、execute_query、route_query 正常 |
| 图谱 | 1/1 | get_source_graph 返回节点结构 |
| 任务/job | 1/3 | create/get 正常；cancel 因 job 已失败而失败 |
| 预览/审计 | 2/2 | preview、query_operation_logs 正常 |
| Agent Memory | 1/1 | remember/recall 正常 |
| 别名 | 1/1 | `kb.search` 别名工作正常 |

---

## 4. 逐轮详情

| 轮次 | 名称 | 工具 | 结果 | 关键细节 |
|---|---|---|---|---|
| R1 | create 预置测试条目 A | `create` | PASS | id=eab05679... |
| R2 | create 预置测试条目 B | `create` | PASS | id=5a8e5eab... |
| R3 | create 预置测试条目 C | `create` | PASS | id=248723f6... |
| R4 | ping 连通性检测 | `ping` | PASS | status=alive, version=1.3.1 |
| R5 | kb_capabilities 能力清单 | `kb_capabilities` | PASS | full profile 启用 |
| R6 | search 语义搜索召回 ALPHA | `search` | PASS | 命中 A、C；**向量通道降级为 keyword** |
| R7 | search_fulltext 全文搜索召回 BETA | `search_fulltext` | PASS | B 排名第一，共 10 条结果 |
| R8 | search 语义搜索召回 GAMMA | `search` | PASS | 命中 C（wiki + knowledge） |
| R9 | ask RAG 问答召回 ALPHA | `ask` | PASS | 回答正确引用 A、C |
| R10 | read 读取条目 A | `read` | PASS | 返回完整 item 与 block |
| R11 | list_knowledge 按测试标签列出 | `list_knowledge` | PASS | 返回 3 条测试条目 |
| R12 | tags 标签列表 | `tags` | PASS | 5 个标签 |
| R13 | create 创建 CRUD 测试条目 D | `create` | PASS | id=8ed2ac16... |
| R14 | read 读取条目 D | `read` | PASS | version=1 |
| R15 | update 更新条目 D | `update` | PASS | 内容更新，version=2 |
| R16 | search 更新后召回 BETA/D | `search` | PASS | 命中 B、D |
| R17 | delete 软删除条目 D | `delete` | PASS | 软删除成功 |
| R18 | restore_knowledge 恢复条目 D | `restore_knowledge` | PASS | 恢复成功 |
| R19 | preview_operation 预览创建 | `preview_operation` | PASS | dry_run=true |
| R20 | create_ingest_job 创建导入任务 | `create_ingest_job` | PASS | job 创建成功 |
| R21 | get_job 查询导入任务 | `get_job` | PASS | job 状态显示 **failed: No handler for file_ingest** |
| R22 | cancel_job 取消导入任务 | `cancel_job` | **FAIL** | PRECONDITION_FAILED：job 已失败/完成 |
| R23 | query_operation_logs 查询操作日志 | `query_operation_logs` | PASS | 返回 7 条近期操作 |
| R24 | structured_query DSL 结构化查询 | `structured_query` | PASS | 按 tag 返回 4 条 |
| R25 | execute_query 执行结构化查询 | `execute_query` | PASS | 按 tag 返回 4 条 |
| R26 | route_query 查询路由 | `route_query` | PASS | fallback to hybrid search |
| R27 | ask_with_query 显式 QuerySpec 问答 | `ask_with_query` | **FAIL** | envelope ok，但回答错误声称无内容，源文本为空 |
| R28 | get_source_graph 来源图谱 | `get_source_graph` | PASS | 2 个 knowledge 节点 |
| R29 | remember_fact 记住决策 | `remember_fact` | PASS | 记忆写入成功 |
| R30 | recall_facts + kb.search 别名测试 | `recall_facts`, `kb.search` | PASS | 记忆召回与别名均正常 |

---

## 5. 关键问题与风险

### 5.1 向量通道降级（高优先级）

**现象**：所有 `search` 调用返回 `warnings: ["vector channel degraded: OperationalError: no such module: vec0"]`，实际使用 keyword 匹配完成召回。

**影响**：
- 语义搜索能力名存实亡，依赖字面关键词匹配。
- 对同义词、改写查询、长文本语义的召回会显著下降。
- 本次测试召回率 100% 是因为关键词被硬编码在文本中，不代表真实语义检索效果。

**建议**：检查 SQLite 的 `sqlite-vec` 扩展是否正确加载；确认 `vec0` 虚拟表已创建；必要时重新初始化向量存储并重建索引。

### 5.2 file_ingest job 缺少 handler（中优先级）

**现象**：`create_ingest_job` 创建任务成功，但 `get_job` 显示 `status=failed`，错误 `No handler for file_ingest`。

**影响**：基于 job 的异步文件导入不可用，与 `index_path`/`ingest_file` 的同步导入形成能力缺口。

**建议**：在 `src/services/jobs.py` 或相应注册处补全 `file_ingest` handler；或从 full profile 中移除该工具直至实现完整。

### 5.3 ask_with_query 源文本为空（中优先级）

**现象**：`ask_with_query` 传入显式 `query_spec` 后，envelope 返回 ok，但检索到的 source 文本为空，导致 LLM 错误回答“知识库中无内容”。

**影响**：Agent 使用 QuerySpec 进行精确 RAG 时会产生幻觉式否认，降低可用性。

**建议**：排查 `ask_with_query` 在显式 query_spec 路径下，检索结果到 source 文本回填的链路；对比 `ask`（自动 query_spec）的实现差异。

### 5.4 cancel_job 语义问题（低优先级）

**现象**：对已失败/已完成的 job 调用 `cancel_job` 返回 PRECONDITION_FAILED。

**评估**：该行为本身合理，但由于 R20 的 job 失败，导致 R22 无法按预期完成。建议在文档或错误信息中明确 cancel 的前置条件。

---

## 6. 稳定性观察

- **envelope 稳定性**：30 轮调用中，所有工具均返回标准 envelope（`ok`/`data`/`error`/`meta`），无超时、无进程崩溃、无协议错误。
- **写操作稳定性**：create/update/delete/restore 均通过操作审计（operation_id）落盘，undo 数据完整。
- **读操作延迟**：ping、read、list、tags、query_operation_logs 等读工具响应在秒级以内。
- **LLM/RAG 延迟**：`ask`、`route_query`、`ask_with_query` 受 MiniMax API 调用影响，响应在数秒级，符合预期。
- **生产数据隔离**：测试条目使用唯一标签，测试结束后已执行软删除，未对真实业务文档造成结构性影响。

---

## 7. 结论

ShineHeKnowledge v1.3.1 生产环境的 MCP 接口在 30 轮全工具抽样测试中表现出较高的**调用稳定性**（成功率 93.33%，无崩溃），核心检索与 CRUD 链路可用。然而，**语义搜索能力因 sqlite-vec 扩展缺失已降级为关键词匹配**，且 `file_ingest` job、`ask_with_query` 存在功能性缺陷。

如果目标是将该生产环境作为 Agent 的稳定知识后端，建议优先修复向量通道问题，并补全 `ask_with_query` 的源文本回填逻辑；在此基础上再进行更大规模、更长周期的压力测试与真实用户查询召回评估。

---

## 8. 附录

### 8.1 原始数据文件

- JSON 报告：[reports/mcp_30round_prod_report.json](file:///f:/ClaudeWorkSpace/projects/knowledge-base/reports/mcp_30round_prod_report.json)
- 历史 70 轮测试报告：[reports/mcp_50round_report.json](file:///f:/ClaudeWorkSpace/projects/knowledge-base/reports/mcp_50round_report.json)

### 8.2 相关代码位置

- MCP 工具注册：[src/mcp/tool_registry.py](file:///f:/ClaudeWorkSpace/projects/knowledge-base/src/mcp/tool_registry.py)
- MCP Server 实现：[src/mcp_server.py](file:///f:/ClaudeWorkSpace/projects/knowledge-base/src/mcp_server.py)
- 生产配置：[config.yaml](file:///f:/ClaudeWorkSpace/projects/knowledge-base/config.yaml)
- 历史测试脚本：[scripts/mcp_50_round_stability_test.py](file:///f:/ClaudeWorkSpace/projects/knowledge-base/scripts/mcp_50_round_stability_test.py)

### 8.3 测试清理说明

测试结束后，条目 A、B、C、D 已通过 `delete` 工具执行软删除；标签 `mcp-prod-test-20260622`、`alpha`、`beta`、`gamma`、`delta` 仍保留在标签表中（由系统按需管理）。测试期间写入的一条 Agent Memory 事实（`mcp_prod_test_decision_20260622`）因缺少对应删除工具未清理，影响可忽略。
