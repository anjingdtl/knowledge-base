# ShineHeKB 三项基础问题修复报告

日期：2026-07-16

分支：`fix/production-pilot-foundation-three`

基线源码 SHA：`c43fe37d058555031707cf354c4b76a71e3496e1`（v1.10.4）

Phase 4 已验证实现 SHA：`96e42911ff8ca9e3ec1a54dc2445db25985bb534`

报告提交：在上述已验证实现之后，仅增加本报告，不改变实现或验证结果。

## 1. 结论

三项中的 Routing Harness 与正式 Provider 隔离接线已完成实现、定向测试和相关回归。Ground Truth 的候选、人工审核、冻结发布机制也已完成，但 196 条现有候选尚未由两名真实人员逐条复核，因此没有任何样本可以合法标为 `human_reviewed` 或进入 `frozen/`。

本轮没有冒充审核员、没有自动接受候选、没有修改 Ground Truth 迎合搜索结果，也没有执行最终生产试点质量验收。

**最终判定：三个基础问题尚未全部修复，不能进入全量验收。**

## 2. 基线与提交

| Phase | Commit | 内容 |
|---|---|---|
| 0 | `fd1b62e30e584bf59f839b07ffcdb8147eb17fd6` | 冻结基线、DB 指纹与三个问题复现证据 |
| 1 | `09e1bb7c746067d8f8a78ad74661a4495b878250` | 候选/审核/冻结三层与人工审核门禁 |
| 2 | `851146f2757c0cd2a985b9a32966c2c5ad50dd0f` | 参数原样执行、统一结果分类、多步 flow |
| 3 | `dd8e6112ea3a3a0488c3a00852fd9af4e81fe11d` | 正式 LLM/Embedding/Reranker 可终止隔离接线 |
| 4 | `96e42911ff8ca9e3ec1a54dc2445db25985bb534` | 综合回归、静态检查、DB 完整性与机器汇总 |

Phase 0 的 `git pull --ff-only` 因远端 TLS 握手失败未成功；当时本地 `master` 与 `origin/master` 均指向同一基线 SHA。用户提供的未跟踪 Spec 保持未修改、未纳入提交。

## 3. 三个根因

1. `build_production_pilot_datasets.py` 以标题/正文关键词、正则和规则挑选文档，却直接写 `annotation_source=human`；无审核员、时间、正文证据、二审或裁决记录。
2. Routing Harness 对 search/ask/ask_with_query 重新构造参数，覆盖 Agent 推荐；空结果分支两侧都返回 `non_empty`；timeout、validation、transport 与任务完成混为一体；Graph 无参数直接跳过。
3. `run_in_terminable_process` 已存在，但正式 Ask 外层默认 thread，LLM、Embedding 和 Reranker 的同步 SDK/HTTP 调用都绕过可终止隔离层。

## 4. 主要修改文件

- Ground Truth：`scripts/build_production_pilot_datasets.py`、`scripts/review_production_pilot_ground_truth.py`、`scripts/freeze_production_pilot_datasets.py`、`tests/eval/datasets/{candidates,reviewed,frozen}/` 及 5 个 schema/门禁测试。
- Routing：`scripts/production_pilot_mcp_harness.py`、`evals/production_pilot_metrics.py`、6 个 Routing 定向测试与 `routing-smoke.jsonl`。
- Provider：`src/services/provider_runtime.py`、`deadline.py`、`llm.py`、`embedding.py`、API/本地 Reranker、Ask timeout envelope、health 诊断、8 个定向测试及真实 smoke 脚本。
- 架构清单：`docs/architecture/provider-runtime-isolation-map.md`。
- Phase 4：`regression-summary.json`、`formal-db-integrity.json`、`failures.jsonl`。

## 5. Ground Truth 审核机制

数据角色已拆分为：

```text
规则候选 candidates/ -> 人工审核 reviewed/ -> 门禁冻结 frozen/
```

候选构建器只输出 `rule_assisted_candidate` / `pending` 与 `candidate_*` 建议字段，不再输出正式 expected 字段或 `human`。正式离线评估和 MCP Harness 均只读取 `frozen/`。

人工审核 CLI 会显示 query、标题、正文摘要，可打开完整正文；每个候选文档必须由人明确选择 expected/acceptable/forbidden/irrelevant 并填写理由。Answer Citation 还会展示 block，要求输入真实 block ID 与原文 quote，并校验 quote 确实存在于该 block。二审人与一审人必须不同；分歧进入 `needs_adjudication`，裁决人也必须独立。

冻结器逐条校验：

- `annotation_source=human_reviewed`；
- 状态 approved，一审/二审身份和 ISO 8601 时间完整且人员不同；
- retrieval expected 非空；
- 所有标注 ID 均有 title/body 审核证据与理由；
- Answer fact 有对应 knowledge、block、quote，且 quote 位于冻结 DB 的对应 block；
- corpus snapshot 一致；
- 争议已裁决；
- 正式 DB 以 SQLite `mode=ro` 打开。

### 实际数据状态

| Dataset | Candidate | Reviewed | Frozen |
|---|---:|---:|---:|
| Retrieval | 63 | 0 | 0 |
| No-answer | 32 | 0 | 0 |
| Numeric units | 28 | 0 | 0 |
| Routing | 45 | 0 | 0 |
| Answer citations | 28 | 0 | 0 |
| **合计** | **196** | **0** | **0** |

- 双人审核完成比例：`0/196 = 0%`。
- 争议样本数量：0（尚未开始真实审核，不代表无争议）。
- Answer supporting block/quote 覆盖率：`0/28 = 0%` 的候选完成审核；frozen 中无可计算样本。
- 冻结摘要已对 196 条逐条记录 `review_record_missing`。

这是真实阻塞项。Agent 不能替代两名真实审核员，因此 Phase 1 的数据验收门未通过。

## 6. Routing Harness 修复

执行器现在使用 `deepcopy(recommended_arguments)` 原样调用推荐工具，保存：

- `recommended_arguments_raw`；
- `executed_arguments`；
- `arguments_exact_match`；
- required/forbidden/type/mutation argument contract；
- raw route/exec/flow responses；
- protocol、route contract、timeout、task outcome 与 task completed。

`classify_task_outcome` 明确区分 non_empty、empty、no_answer、graph_result、structured_result、validation/provider/MCP/transport error、timeout、cancelled 与 unknown。空 search/ask/graph/rows 不再算 non_empty；timeout、empty 和各类错误均不算 task completed。

多步 Graph flow 支持仅按 Agent 明示的 `arguments_from_previous` 从上一步结果提取 start IDs，Harness 不自行猜测或覆盖参数。

新增指标：Raw Argument Preservation Rate、Empty-result Honesty Rate、Timeout Classification Accuracy、Recommended Flow Execution Rate。

### Routing 证据

- 10 条真实 stdio MCP 候选 smoke：参数原样执行 `10/10 = 100%`。
- 真实 smoke 结果：7 validation_error、2 non_empty、1 empty；task completed 仅 2/10。
- 空结果诚实分类：真实 smoke 1/1；单元测试另覆盖空 search、ask、graph、rows，5 passed。
- Timeout 分类：5 种正式 timeout 信号均为 timeout 且 task completed=false，5 passed。
- Agent 参数与原 query 不同、ask_with_query.search_query：2 passed，未被重写。
- Graph 推荐 flow：search -> graph_traverse 的单元执行通过；真实 10 条 smoke 未得到一个可成功执行的 Graph flow，因此真实 Graph flow 仍列为 NOT TESTED。
- 真实 smoke 中的 validation error 被保留为失败，未通过重构推荐参数掩盖。

本轮结果不是最终 Routing 质量通过结论。

## 7. 正式 Provider 接线

正式非流式 LLM、Embedding、API Reranker 和本地 CrossEncoder Reranker 均通过最小可序列化 `ProviderRequest` 调用 `run_provider_operation(..., isolation_mode="process")`。LLM fallback Reranker 通过中心 LLM 服务间接获得相同隔离。LLM streaming 显式标为 `thread_cooperative` 并使用请求 timeout。

子进程通过 `spawn` 创建；请求中不含 API Key、Container、DB/SQLite 连接、HTTP session、logger handler 或模型实例。子进程按 `secret_env_key` 从环境/keyring/服务安全存储读取密钥，错误返回会脱敏。timeout 执行 terminate+join，必要时 kill+join，随后关闭 queue 并 join feeder thread。

`kb_health_check` 新增无秘密的 `provider_isolation`：active/max/abandoned workers、circuit 状态、最近 timeout operation、PID 和退出码。

完整调用点、同步性质和选择理由见 `docs/architecture/provider-runtime-isolation-map.md`。

## 8. Provider 终止与真实 smoke

- 正常 LLM：3/3，非空响应。
- 人工 10ms LLM timeout：2/2，`worker_terminated=true`、`background_work_may_continue=false`。
- 正常 Embedding：3/3，均返回 1024 维向量。
- 正常 API Reranker：3/3，均返回真实 rerank score。
- 首轮 30 秒正常 LLM smoke 为 2/3，另 1 次真实终止 timeout；提高正常验证预算到 60 秒后为 3/3。该 timeout 没有被计为成功。
- 50 次 timeout：回归通过，abandoned worker 未增长；随后正常请求恢复。
- PID：timeout 后记录的 worker PID 已不存在。
- Worker limit：超限请求被拒绝，后台不继续。
- Secret：定向日志测试通过，所有 commit 的 gitleaks 通过。

## 9. 定向测试

| Test | Result |
|---|---:|
| Ground Truth review metadata | 3 passed |
| Frozen dataset only | 2 passed |
| Routing arguments preservation | 2 passed |
| Routing empty honesty | 5 passed |
| Routing timeout classification | 5 passed |
| `tests/providers/` | 3 passed |
| Ask explicit isolation | 2 passed |
| Provider worker PID terminated | 1 passed |

所有定向 pytest 退出码为 0。

## 10. 相关回归与静态检查

| Command | Result |
|---|---:|
| `pytest tests/eval/ -q` | 49 passed |
| `pytest tests/stability/ -q` | 138 passed, 1 warning |
| `pytest tests/test_mcp_contract.py -q` | 44 passed, 1 skipped, 1 warning |
| `pytest tests/test_public_search_contract.py -q` | 9 passed, 1 warning |
| `pytest tests/test_public_ask_contract.py -q` | 6 passed, 1 warning |
| Routing related extra | 10 passed, 1 warning |
| `ruff check src tests scripts evals` | passed |
| `mypy src` | success, 272 source files |

pytest 成功退出后重复出现 Windows 临时 `pytest-current` 符号链接清理 PermissionError；不影响测试退出码，已记录为环境警告。

## 11. 正式 DB 完整性

| Field | Before | After |
|---|---|---|
| Size | 346,054,656 bytes | 346,054,656 bytes |
| SHA256 | `dee013a91eeae27b0224dbe3c756b2d815b1d11803efc0925f53d621fa0e2c01` | `dee013a91eeae27b0224dbe3c756b2d815b1d11803efc0925f53d621fa0e2c01` |

正式 DB 未变化。

## 12. 未解决问题

1. 196 条候选需要两名真实、独立的审核员完成标题/正文/事实证据复核；争议需第三人裁决。
2. 在完成审核并重新运行 strict freeze 前，`frozen/` 合法地保持为空，正式验收不可运行。
3. 真实 stdio smoke 暴露了当前 Agent 推荐的多条 execute_query 参数与 MCP schema 不匹配；Harness 已诚实记录，未在本轮修改 Ground Truth 或重构参数来掩盖。

## 13. NOT TESTED

- 最终生产试点全量指标与门槛：未测试，禁止在 frozen GT 为空时执行/宣称。
- 真实双人审核与真实裁决工作流：未测试，需要真实人员输入。
- 真实 MCP Graph 推荐 flow 成功执行：未测试；仅有确定性单元测试。
- OCR Provider：未测试；仓库中没有实现或启用的生产 OCR Provider 路径。
- 全仓所有测试：未运行；已运行 Spec 指定的定向与相关回归集合。

## 14. 证据索引

- `artifacts/foundation-three-fixes/baseline.json`
- `repro-ground-truth.json`
- `repro-routing-harness.jsonl`
- `repro-provider-isolation.json`
- `dataset-freeze-summary.json`
- `routing-smoke.jsonl`
- `provider-wiring-smoke.jsonl`
- `regression-summary.json`
- `formal-db-integrity.json`
- `failures.jsonl`
- Phase 1/2/3 red/green 原始摘要

## 15. 是否可以进入独立全量验收

否。Routing 与 Provider 两项满足本轮技术修复门槛，Ground Truth 流程机制满足门禁设计，但实际双人复核与冻结发布为 0%，故三项基础问题尚未全部修复。

```text
三个基础问题尚未全部修复，不能进入全量验收
```
