# ShineHeKnowledge 融合收束开发规格说明（Spec）

> 文档状态：Proposed  
> 适用仓库：`anjingdtl/knowledge-base`  
> 当前基线：`master` / `v1.6.0`  
> 建议目标版本：`v1.7.0`  
> 主要执行者：开发 Agent  
> 核心目标：在不牺牲项目聚焦度的前提下，将 Raw Retrieval 与 Wiki V2 融合为统一的 Agent 知识服务：原始文档是证据底座，经过验证的 Canonical Wiki 是默认知识增强层；由自动化“Wiki 维护中心”持续执行监测、失效保护、校验、重建编排和审阅分发，语义修改与发布仍受人工门禁控制。

---

## 0. 最高优先级决策

本 Spec 只允许围绕以下四条核心决策实施。

1. **原始文档及其 Block 是最终证据来源。**
2. **经过验证、状态有效且可追溯的 Wiki V2 Claim 默认参与检索和回答。**
3. **低风险、单调且可逆的保护性维护可以自动执行，包括标记 stale、将无有效证据的 Claim 降级为 unsupported、将受影响页面转入 review，以及把不合格知识立即移出 Serving。**
4. **会改变知识语义的操作必须受控：自动化可以生成 Draft、差异和修订建议，但 Claim 合并、冲突裁决、正式修正、发布、删除和迁移切换不得绕过人工审阅。**
5. **Wiki 不可用、无有效 Claim 或可信度不足时，系统必须自动降级为原始文档检索，不得阻断回答。**
6. **维护中心是现有 Raw Index、Canonical Repository、Projection、Outbox、Validator、Rebuild 和 Operation Log 的统一控制面，不得成为第二套事实库。**

项目不再被定义为两个彼此隔离的产品，也不再采用“Retrieval 或 Wiki-First 二选一”的默认叙事。

统一产品定位为：

> **ShineHeKnowledge 是一个本地优先、面向 AI Agent 的可验证知识检索引擎。它以原始文档为证据底座，以 Canonical Wiki V2 为受控知识增强层，通过 MCP 返回可追溯、可解释、可评测的答案。**

---

## 1. Agent 执行契约

本节优先级高于其他章节。

### 1.1 执行原则

开发 Agent 必须：

1. 先建立测试和行为基线，再修改默认流程；
2. 采用兼容式收束，不进行大爆炸式重写；
3. 保留现有 Wiki V2、Canonical Store、Claim、Graph、GUI、API、任务系统和迁移能力；
4. 将“读取已验证知识”“保护性自动维护”“语义 Authoring”“正式发布”四种权限严格分离；
5. 确保所有 Wiki 结论最终可追溯到原始 `knowledge_id`、`block_id` 和位置；
6. 确保 Wiki 异常不会导致基础检索失败；
7. 每个阶段独立可合并、可回滚；
8. 所有行为变化必须配套测试、文档和迁移说明；
9. 不得降低现有 Raw Retrieval 指标和引用完整性；
10. 不得为了获得更好的评测结果删除困难样本或降低质量阈值；
11. 不得自行新增与本 Spec 无关的产品能力；
12. 遇到重大架构冲突时停止当前阶段并生成阻塞报告，不得继续堆叠修改。

### 1.2 禁止事项

开发期间禁止：

- 删除 Wiki V2 或将其降级为完全独立的旁路功能；
- 让 Wiki 页面或 Claim 脱离原始证据成为新的事实源；
- 默认开启 Wiki 自动发布；
- 默认允许 Agent 修改、删除或发布知识；
- 允许维护自动化绕过 Repository 事务、Serving Gate、Review Gate、Operation Log 或备份机制；
- 默认暴露 Wiki 管理、迁移、回滚等写工具；
- 新增外部向量数据库或图数据库；
- 新增 SaaS、多租户、RBAC、协作编辑；
- 重写整个 `src/services`；
- 将所有查询强制走 Wiki；
- 建立一套与 Canonical Repository 平行的“维护中心数据库”保存另一份 Claim 真相；
- Wiki 失败时返回空结果而不尝试 Raw Retrieval；
- 把 `draft`、`stale`、`unsupported` 或 `retracted` Claim 当作可靠主结论；
- 将 fake-embedding 结果宣传为真实模型效果；
- 在同一提交中混入无关格式化、命名重构或前端改版。

### 1.3 每阶段交付格式

每完成一个阶段，Agent 必须输出：

1. 修改文件列表；
2. 行为变化摘要；
3. 兼容性说明；
4. 测试命令与结果；
5. Retrieval / Wiki / Hybrid 指标变化；
6. 未解决风险；
7. 回滚方式；
8. 对应提交 SHA。

---

## 2. 背景与问题定义

项目已经具备两类有价值的能力。

### 2.1 原始证据检索能力

- 本地文件解析和增量索引；
- SQLite、FTS5、sqlite-vec；
- 向量与关键词并行检索；
- RRF 融合；
- Query Rewrite；
- Rerank；
- Parent-Child 上下文扩展；
- PDF、DOCX、Excel、Markdown 等位置引用；
- MCP Search / Ask / Read；
- 文件监听、诊断和 Retrieval Eval。

这些能力擅长：

- 精确查找原文；
- 定位页码、标题、Sheet 和段落；
- 回答新鲜或细节性问题；
- 为答案提供可核验引用。

### 2.2 Wiki V2 规范知识能力

- Canonical Page；
- Claim；
- Evidence；
- supports / contradicts / refines 等关系；
- Claim 状态；
- Review Gate；
- 来源变更后的失效传播；
- stale / unsupported / retracted；
- Projection parity；
- 迁移、验证和用户反馈；
- 知识演进评测。

这些能力擅长：

- 沉淀稳定结论；
- 合并跨文档知识；
- 识别冲突和限定条件；
- 降低重复查询时的答案波动；
- 追踪知识的更新和失效；
- 为复杂问题提供结构化知识上下文。

### 2.3 当前问题

当前项目的主要问题不是 Wiki V2 本身，而是产品和运行边界不清晰：

- Wiki 读取、Wiki 写入、Wiki 发布和实验工具混在同一开关中；
- 默认初始化容易让新用户误以为必须使用完整 Wiki 工作流；
- README 主叙事在“检索引擎”和“知识编译系统”之间摆动；
- Raw Retrieval 与 Wiki Eval 相对割裂，尚未证明融合后的最终答案更准确；
- MCP 工具过多时可能增加 Agent 选择错误；
- Wiki 内容即使已生成，也缺少统一的 Serving Eligibility Gate；
- Wiki 不可用时的降级行为没有成为明确产品契约。

本轮收束不再采用“关闭 Wiki 以保持聚焦”的方式，而是采用：

> **统一产品、三层架构、两种运行档位、一个自动降级链路。**

---

## 3. 统一三层架构与维护控制面

## 3.1 Evidence Layer：原始证据层

该层是系统的最终 Source of Truth。

包含：

- 原始文件；
- Knowledge Item；
- Block；
- 文件路径；
- 页码 / Sheet / Slide / 标题路径 / 行号；
- 文件 Hash；
- Block Hash；
- Embedding；
- FTS5；
- Raw Retrieval；
- Citation。

规则：

1. 原始文件不得被 Wiki 自动修改；
2. 所有 Claim 必须至少关联一个有效 Evidence；
3. Evidence 必须指向可读取的 `knowledge_id` 和 `block_id`；
4. 文件更新后必须重新验证相关 Evidence；
5. 文件删除后相关 Claim 必须进入保守失效流程；
6. 最终答案必须能够回到该层核验。

## 3.2 Canonical Knowledge Layer：规范知识层

该层由 Wiki V2 提供，是受控的派生知识。

包含：

- Canonical Page；
- Claim；
- Evidence 引用；
- Claim Relations；
- Entity / Concept；
- Contradiction；
- Review Item；
- Claim Status；
- Revision；
- Stale Propagation；
- Projection。

规则：

1. Wiki 不是独立事实源；
2. Claim 的可信度由状态、证据、时效和校验共同决定；
3. 只有通过 Serving Eligibility Gate 的 Claim 才能进入默认回答；
4. Claim 与原始 Evidence 不一致时，以原始 Evidence 为准；
5. 对冲突 Claim 不允许静默裁决；
6. Wiki 写入与 Wiki 读取必须拥有不同权限和配置。

## 3.3 Agent Serving Layer：统一服务层

该层通过 MCP 向 Agent 暴露统一能力。

默认工具面继续围绕：

- `ping`
- `kb_capabilities`
- `search`
- `ask`
- `read`
- `list_knowledge`
- `get_job`
- `list_jobs`

索引写工具只在写策略允许时出现。

Agent 不需要调用独立的“Wiki Search”和“Raw Search”完成普通问答。统一 `search` 和 `ask` 内部负责：

1. Query Routing；
2. Verified Wiki Retrieval；
3. Raw Block Retrieval；
4. Evidence Alignment；
5. Conflict / Freshness Check；
6. Unified Rerank；
7. Citation Packaging；
8. Answer Generation。

`read` 必须支持读取：

- 原始文档；
- Block；
- Canonical Page；
- Claim；
- Claim 对应 Evidence。

不建议仅为默认 Agent 新增大量独立 Wiki 工具。

---

## 3.4 Maintenance Control Plane：维护控制面

维护中心不是第四个知识数据层，而是横跨 Evidence、Canonical Knowledge 和 Agent Serving 的控制面。

它负责：

- 监听 Raw Source 新增、更新和删除事件；
- 合并和去重短时间内的重复事件；
- 生成影响分析与 Dry Run 计划；
- 调用现有 Block Hash、Dependency、Rebuild、Validator 和 Projection 能力；
- 自动执行低风险保护动作；
- 为高风险语义变更生成 Review Item；
- 管理维护任务、重试、取消、失败队列和审计；
- 展示 Wiki 健康度、Serving 风险、Projection Drift 和处理积压；
- 将确认后的变更经 Canonical Repository 事务提交；
- 在任何失败情况下优先保护 Raw Retrieval 和现有 Canonical 数据。

维护中心必须复用：

```text
Raw Index / File Watcher
        ↓
Source Event / Outbox
        ↓
Maintenance Policy Engine
        ↓
Impact Plan / Rebuild / Validation
        ↓
Review Queue
        ↓
Canonical Repository Transaction
        ↓
Projection / Serving Gate / Audit
```

维护中心不得：

- 直接修改 Raw Source；
- 绕过 `WikiRepository.transaction()` 写 Claim 或 Page；
- 绕过 Outbox 和 Projection；
- 把自动生成的 Draft 直接升级为 Active；
- 自动解决高风险冲突；
- 自动发布语义变化；
- 因维护任务失败阻断 Raw Search。


## 4. 运行档位设计

项目提供三个运行档位。它们是同一产品的运行策略，不是三个产品。

## 4.1 `verified`：默认档

适合绝大多数用户。

行为：

- Raw Retrieval 始终开启；
- 读取已发布、有效且有证据的 Wiki Claim；
- Raw 与 Wiki 并行或按路由组合；
- Wiki 无命中时使用 Raw；
- Wiki 异常时自动降级 Raw；
- 维护中心自动监测 Source、Evidence、Claim、Page 和 Projection 健康状态；
- 允许自动执行保护性状态迁移：Evidence stale、Claim unsupported、Page review、Serving 排除；
- 不自动抽取并激活新 Claim；
- 不自动合并或修正 Claim 语义；
- 不自动发布；
- 不向 Agent 暴露 Wiki 写工具；
- MCP 写策略默认关闭。

该档位的核心语义是：

> **使用已验证知识，但不允许 Agent 自主维护知识。**

## 4.2 `authoring`：显式维护档

适合需要构建和维护 Canonical Wiki 的用户。

在 `verified` 基础上增加完整维护中心和受控 Authoring：

- Source Summary；
- Entity / Concept 编译；
- Claim 抽取；
- Claim Matcher；
- Merge Engine；
- Contradiction Review；
- Feedback；
- Rebuild；
- Migration；
- Validation；
- Publish Workflow；
- 自动生成 Draft、重建建议和差异，但仍由 Review Gate 决定是否正式发布。

安全要求：

- `auto_publish=false`；
- 高风险关系必须进入 Review；
- 写操作受 `write_policy` 控制；
- HTTP 写默认关闭；
- 所有写操作有审计记录和回滚能力。

该档位的核心语义是：

> **允许受控维护知识，但不允许无审阅自动成为可靠知识。**

## 4.3 `evidence_only`：降级和调试档

仅使用原始文档检索。

用途：

- 新安装且尚无 Canonical 数据；
- Wiki 数据损坏；
- 维护中心故障隔离；
- 性能对比；
- 回归分析；
- 用户主动禁用 Wiki；
- Hybrid Eval 的 Raw-only 对照组。

该档位不是默认产品定位，而是：

- 安全降级；
- 兼容入口；
- 调试和评测模式。

---

## 5. 配置契约

## 5.1 新的顶层语义

建议使用：

```yaml
knowledge_workflow:
  mode: verified   # verified | authoring | evidence_only
```

兼容旧值：

| 旧值 | 新语义 | 行为 |
|---|---|---|
| `wiki_first` | `authoring` | 保持现有 Wiki 维护能力 |
| `legacy` | `evidence_only` | 保持旧原始检索路径，并输出弃用提示 |
| 未配置 | `verified` | 新默认 |
| 非法值 | 启动失败或 Doctor 明确报错 | 不静默猜测 |

不得自动改写用户配置文件。

## 5.2 默认 `verified` 配置

```yaml
knowledge_workflow:
  mode: verified

wiki:
  enabled: true
  read_enabled: true
  authoring_enabled: false
  auto_compile: false
  auto_link: true
  auto_publish: false
  lint_contradictions: true

  serving:
    enabled: true
    allowed_claim_statuses:
      - active
    require_block_evidence: true
    exclude_stale: true
    exclude_unsupported: true
    exclude_retracted: true
    require_validation_passed: true
    unresolved_policy: disclose
    contradiction_policy: disclose
    on_failure: raw_fallback
    max_claims_per_query: 8
    max_evidence_per_claim: 3

mcp:
  tool_profile: core
  experimental_tools_enabled: false
  enable_legacy_aliases: false
  write_policy: disabled
  allow_http_write: false

rag:
  search_mode: hybrid_verified
  enable_query_rewriting: true
  enable_rerank: false
  parent_child:
    enabled: true
  chunk_size: 1200
  chunk_overlap: 180
  top_k: 8
  score_threshold: 0.35
  rrf_weight_keyword_zh: 0.7
  rrf_weight_keyword_en: 0.5

  verified_knowledge:
    enabled: true
    raw_candidate_multiplier: 3
    wiki_candidate_multiplier: 2
    raw_weight: 0.60
    wiki_weight: 0.40
    evidence_alignment_enabled: true
    stale_fallback_to_raw: true
    empty_wiki_fallback_to_raw: true
```

权重只是初始建议，最终值必须由 Hybrid Eval 决定。

## 5.3 `authoring` 配置覆盖

```yaml
knowledge_workflow:
  mode: authoring

wiki:
  enabled: true
  read_enabled: true
  authoring_enabled: true
  auto_compile: true
  auto_publish: false

mcp:
  tool_profile: extended
  experimental_tools_enabled: true
  write_policy: local_confirm
  allow_http_write: false
```

说明：

- `experimental_tools_enabled=true` 只适用于明确选择 Authoring 的新配置；
- 已有用户显式配置优先；
- 即便 Authoring 开启，自动发布仍保持关闭；
- `local_confirm` 不能被 HTTP 模式绕过。

## 5.4 `evidence_only` 配置覆盖

```yaml
knowledge_workflow:
  mode: evidence_only

wiki:
  read_enabled: false
  authoring_enabled: false

rag:
  search_mode: blend
  verified_knowledge:
    enabled: false
```

## 5.5 `canonical_v2.mode` 的边界

现有：

```yaml
wiki:
  canonical_v2:
    mode: off | shadow | canary | primary
```

继续用于控制 Canonical 写入迁移阶段，不应再承担“回答时是否读取 Wiki”的职责。

必须明确分离：

- `wiki.serving.*`：控制读和回答；
- `wiki.canonical_v2.mode`：控制写入路径和迁移阶段；
- `wiki.authoring_enabled`：控制是否允许维护；
- `mcp.write_policy`：控制 Agent 写权限。

---

## 5.6 维护中心配置

维护中心配置与 MCP 写策略分离。

- `mcp.write_policy` 控制 Agent 能否通过 MCP 发起写操作；
- `maintenance.*` 控制系统内部维护任务；
- 系统维护写入仍必须经过 Repository 事务、审计和风险策略；
- `verified` 模式允许保护性自动维护，但不允许语义 Authoring；
- `authoring` 模式允许自动生成 Draft 和修复建议，但默认禁止自动发布。

建议默认配置：

```yaml
maintenance:
  enabled: true
  center_enabled: true

  # observe | supervised | managed
  # verified 默认 supervised；authoring 可显式选择 managed。
  automation_level: supervised

  policy:
    # R0：只读观测与诊断
    allow_observation: true

    # R1：单调、可逆、保护 Serving 的状态迁移
    auto_protective_actions: true
    auto_mark_evidence_stale: true
    auto_downgrade_unsupported_claims: true
    auto_move_affected_pages_to_review: true
    auto_exclude_ineligible_claims_from_serving: true

    # R2：不改知识语义的结构修复
    auto_retry_failed_jobs: true
    auto_recover_outbox: true
    auto_rebuild_projection_on_safe_drift: false
    auto_reindex_missing_blocks: false

    # R3：语义 Draft，只在 authoring 模式生成
    auto_generate_claim_drafts: false
    auto_generate_page_drafts: false
    auto_generate_correction_suggestions: true

    # R4：正式语义变更和高风险动作，默认一律人工确认
    auto_merge_claims: false
    auto_resolve_conflicts: false
    auto_publish: false
    auto_retract: false
    auto_delete: false
    auto_migrate_primary: false

  scheduler:
    source_events_enabled: true
    debounce_ms: 500
    validation_cron: "0 3 * * *"
    projection_check_cron: "30 3 * * *"
    weekly_quality_cron: "0 4 * * 1"
    monthly_full_audit_cron: "0 5 1 * *"

  jobs:
    max_workers: 2
    max_attempts: 3
    retry_backoff_seconds: [10, 60, 300]
    lease_timeout_seconds: 300
    retain_completed_days: 30
    retain_failed_days: 90
    dead_letter_enabled: true

  review:
    require_human_for_risk_at_or_above: R3
    max_bulk_action_items: 50
    require_note_for_reject: true
    require_note_for_conflict_resolution: true
    publish_requires_validation_passed: true
    publish_requires_projection_parity: true

  health:
    snapshot_enabled: true
    history_days: 90
    high_priority_claim_query_threshold: 20
    alert_on_serving_stale_claim: true
    alert_on_projection_drift: true
    alert_on_failed_protective_action: true
```

### 自动化等级

| 等级 | 用途 | 自动行为 |
|---|---|---|
| `observe` | 审计、调试 | 只生成诊断、计划和 Review Item，不写 Canonical 状态 |
| `supervised` | 默认 | 自动执行 R0/R1；R2 仅重试和安全恢复；R3/R4 进入审阅 |
| `managed` | 高级 Authoring | 自动执行 R0/R1/允许的 R2，并可生成 R3 Draft；R4 始终人工确认 |

`managed` 不等于无人值守发布。任何配置等级下，`auto_publish` 默认都必须为 `false`。


## 6. Wiki Serving Eligibility Gate

这是融合成功的核心门禁。

任何 Claim 在进入默认 `search`、`ask` 或生成上下文前，必须通过统一的 Serving Eligibility Gate。

## 6.1 默认允许条件

一个 Claim 只有同时满足以下条件，才能作为可靠主结论：

```text
status == active
AND stale == false
AND evidence_count >= 1
AND all_required_evidence_resolvable == true
AND validation_passed == true
AND review_required == false
AND retracted == false
```

## 6.2 状态处理

| 状态或条件 | Serving 行为 |
|---|---|
| `active` 且门禁通过 | 可作为主结论 |
| `draft` | 不作为主结论；可在 Authoring UI 中展示 |
| `unresolved` | 只在问题相关时披露歧义，不自动选边 |
| `contradicted` | 展示冲突双方及各自证据 |
| `stale` | 禁止作为可靠主结论，回退 Raw |
| `unsupported` | 排除 |
| `retracted` | 排除 |
| Evidence 无法解析 | 排除并记录诊断 |
| 校验失败 | 排除并进入 Review |
| Evidence 已更新但 Claim 未重建 | 降级为 stale |

## 6.3 证据一致性

Serving Gate 除状态检查外，还必须验证：

1. Evidence 指向的 Block 存在；
2. Block 所属 Knowledge 未软删除或硬删除；
3. Evidence 的 Hash / excerpt hash 与当前 Block 一致，或已通过重建确认；
4. Claim 的关键断言未超出 Evidence；
5. 时间、地区、型号、单位、否定极性和强度限定未被遗漏；
6. 多 Evidence Claim 不得只保留支持结论而丢失反例。

第一阶段可使用确定性校验和已有 Matcher 能力，后续再加入可选语义一致性校验。不得让每次普通查询都强制调用昂贵 LLM。

## 6.4 门禁输出

被过滤的 Claim 必须产生可观测原因代码，例如：

- `claim_status_not_allowed`
- `claim_stale`
- `claim_unsupported`
- `claim_retracted`
- `missing_evidence`
- `evidence_block_missing`
- `evidence_hash_mismatch`
- `validation_failed`
- `review_required`
- `scope_mismatch`
- `unit_incompatible`
- `polarity_mismatch`
- `intensity_mismatch`

这些原因应进入 Trace 和 Eval 报告，不必默认暴露给最终用户。

---

## 7. 统一查询与回答管线

## 7.1 总体流程

```text
User Query
  ↓
Query Analysis / Routing
  ↓
┌──────────────────────────┬──────────────────────────┐
│ Verified Wiki Retrieval  │ Raw Block Retrieval      │
│ Claim / Page / Relations │ Vector / FTS5 / RRF      │
└──────────────────────────┴──────────────────────────┘
  ↓
Wiki Serving Eligibility Gate
  ↓
Evidence Resolution
  ↓
Candidate Normalization
  ↓
Conflict / Freshness / Scope Check
  ↓
Unified Fusion and Rerank
  ↓
Context Assembly
  ↓
Answer Generation
  ↓
Claim Citation + Raw Evidence Citation
```

## 7.2 Query Router

Router 必须是可解释和可降级的。

建议意图：

- `definition`
- `entity_summary`
- `relationship`
- `comparison`
- `exact_lookup`
- `document_location`
- `recent_or_current`
- `multi_source_synthesis`
- `unanswerable_check`

初始路由可采用规则优先，LLM 可选：

| 意图 | Wiki 权重 | Raw 权重 |
|---|---:|---:|
| 定义、概念、实体总结 | 高 | 中 |
| 关系、跨页综合 | 高 | 中 |
| 精确数值、页码、条款 | 低 | 高 |
| 文件内定位 | 低 | 高 |
| 时效敏感问题 | 低 | 高 |
| 多来源比较 | 中高 | 高 |
| 无答案判断 | 中 | 高 |

Router 失败时默认并行查询，不得阻断。

## 7.3 Verified Wiki Retrieval

只能检索：

- Canonical Store 中可读取的 Page；
- 通过 Gate 的 Claim；
- Claim 对应 Evidence；
- 有效关系；
- 必要的 source / entity / concept 上下文。

不得默认检索：

- Draft Claim；
- Review Item；
- 已失效历史版本；
- 未发布临时文件；
- Authoring staging 数据。

## 7.4 Raw Retrieval

保留现有高精度链路：

```text
Query Rewrite
→ Vector + FTS5
→ RRF
→ Rerank
→ Parent-Child
→ Diversity
→ Citation
```

不得因为 Wiki 命中而跳过 Raw Evidence Resolution。

对于稳定的 Wiki Claim，可以减少 Raw 候选数量，但至少保留其 Evidence Block。

## 7.5 Candidate Normalization

Wiki Claim 与 Raw Block 必须转换为统一候选结构。

建议：

```json
{
  "candidate_type": "claim | raw_block | wiki_page",
  "candidate_id": "...",
  "text": "...",
  "score": 0.0,
  "source_layer": "canonical | evidence",
  "knowledge_id": "...",
  "block_id": "...",
  "claim_id": "...",
  "page_id": "...",
  "status": "active",
  "freshness": "current",
  "match_channels": [],
  "score_breakdown": {},
  "evidence": [],
  "warnings": []
}
```

## 7.6 融合与重排

不得直接把 Wiki 分数与向量距离相加。

必须：

1. 各通道内部归一化；
2. 使用 RRF 或显式可配置融合；
3. Wiki Claim 得分必须受到门禁、状态和 Evidence Coverage 影响；
4. Raw Block 得分必须保留现有语义和关键词分解；
5. 相同 Claim 的 Evidence Block 不应重复挤占结果；
6. 相同文档的重复块继续受多样性约束；
7. 冲突 Claim 不应被普通排序掩盖。

建议 Wiki 可信度乘数：

```text
claim_serving_score =
retrieval_score
× status_factor
× evidence_coverage
× freshness_factor
× validation_factor
```

默认门禁不通过时直接排除，不依赖低分“自然掉出”。

## 7.7 冲突处理

当检测到相关 Claim 存在冲突时：

- `ask` 不得生成单一确定结论；
- 必须展示主要分歧；
- 必须分别引用双方 Evidence；
- 可说明哪个来源更新、范围更匹配或证据更多；
- 除非存在明确规则，不得自动宣布某一方正确；
- 输出中增加 `conflict_disclosed=true`。

## 7.8 时效处理

对于包含“当前、最新、现行、现在、截至”等意图的查询：

1. 降低 Wiki 历史总结的优先级；
2. 检查 Claim 有效期；
3. 检查 Evidence 文件更新时间；
4. 排除 stale Claim；
5. 优先返回最新有效原始文件；
6. 若无法确认最新性，明确说明。

---

## 8. 引用契约

融合后的答案必须同时区分：

- 规范知识引用；
- 原始证据引用。

## 8.1 Claim 引用

示例：

```json
{
  "claim_id": "claim_...",
  "statement": "...",
  "status": "active",
  "revision": 3,
  "page_id": "page_...",
  "validation": "passed"
}
```

## 8.2 Evidence 引用

每个被采用的 Claim 至少返回一个原始 Evidence：

```json
{
  "knowledge_id": "doc_...",
  "block_id": "block_...",
  "path": "D:/docs/...",
  "location": {
    "page": 12,
    "heading_path": ["...", "..."]
  },
  "excerpt": "...",
  "evidence_stance": "supports"
}
```

## 8.3 最终答案要求

`ask` 的主结论不能只引用 Wiki Page。

必须满足：

- Claim 可追溯；
- 原始 Evidence 可读取；
- 页面或 Block 位置完整；
- 冲突时双方都有 Evidence；
- Raw-only 回答保持现有 Citation 契约；
- Wiki 不可用时响应结构仍稳定。

## 8.4 `read` 扩展

保持单一 `read` 工具，但支持以下输入：

```json
{"block_id": "..."}
{"knowledge_id": "..."}
{"claim_id": "..."}
{"page_id": "..."}
```

读取 Claim 时返回：

- Claim；
- 状态；
- Relations；
- Evidence；
- Evidence 当前有效性；
- 对应原始片段。

---

## 9. MCP 工具面收束

## 9.1 Core 工具

保留现有 Core 概念，但修正文案：

> Core 包含稳定的检索、问答、读取、能力发现与任务查看工具；索引维护工具仅在写策略允许时注册。

不再称为“10 个只读工具”。

## 9.2 写工具动态隐藏

当：

```yaml
mcp:
  write_policy: disabled
```

必须过滤：

- `side_effect == write`
- `side_effect == destructive`

默认 `verified` 档不向 Agent 展示：

- `index_path`
- `reindex_all`
- Claim Review 写工具；
- Wiki migration；
- Wiki publish；
- CRUD；
- Undo / restore；
- Canonical rebuild 写入口。

CLI 索引命令不受 MCP 工具隐藏影响。

## 9.3 Authoring 工具

只有同时满足以下条件才注册：

```text
knowledge_workflow.mode == authoring
AND wiki.authoring_enabled == true
AND mcp.write_policy != disabled
```

工具仍按现有 profile、experimental 和 side effect 共同过滤。

## 9.4 `kb_capabilities`

必须返回：

```json
{
  "knowledge_mode": "verified",
  "raw_retrieval": true,
  "verified_wiki_read": true,
  "wiki_authoring": false,
  "wiki_serving_status": "ready | empty | degraded | unavailable",
  "fallback": "raw_retrieval",
  "tool_profile": "core",
  "write_policy": "disabled",
  "registered_tools": [],
  "hidden_by_policy": [],
  "serving_claim_statuses": ["active"],
  "citation_layers": ["claim", "raw_evidence"],
  "recommended_flow": ["search", "read"]
}
```

---

## 10. 初始化与用户体验

## 10.1 默认初始化

执行：

```bash
shinehe init --local --path D:\docs --client claude-code
```

等价于：

```bash
shinehe init --mode verified --local --path D:\docs --client claude-code
```

行为：

- 配置 Raw Retrieval；
- 开启 Verified Wiki 读取能力；
- 关闭 Wiki Authoring；
- 开启维护中心的只读监测与保护性自动维护；
- 关闭自动语义编译和自动发布；
- 关闭 MCP 写操作；
- 不要求用户维护 Wiki；
- 不强制创建面向作者的完整 Wiki 目录；
- Canonical Store 可按需、懒创建或复用现有数据；
- 无 Canonical 数据时自动表现为 Raw-only，而非报错。

输出：

```text
[OK] 知识模式: verified
[OK] 原始文档检索: enabled
[OK] 已验证 Wiki 读取: enabled
[INFO] 当前无可用 Canonical Claim，将自动使用原始文档检索
[OK] Wiki Authoring: disabled
[OK] Maintenance Center: supervised (protective actions only)
[OK] MCP 客户端已配置: claude-code
```

## 10.2 Authoring 初始化

执行：

```bash
shinehe init --mode authoring --local --path D:\knowledge
```

行为：

- 创建 `raw/`、`wiki/`、`schema/`、`artifacts/eval/`；
- 生成 Authoring `AGENTS.md`；
- 启用 Wiki 编译、Claim 工作流和维护中心；
- 允许自动生成 Draft、影响计划和修订建议；
- 自动发布保持关闭；
- 写策略默认 `local_confirm`；
- HTTP 写保持关闭。

## 10.3 Evidence-only 初始化

执行：

```bash
shinehe init --mode evidence-only --local --path D:\docs
```

行为：

- 完全不读取 Wiki；
- Wiki 维护任务暂停，仅保留 Raw Index 健康检查；
- 适合故障排查和性能对照；
- 不创建 Authoring 目录。

CLI 名称使用连字符，配置值使用下划线：

```text
CLI: evidence-only
YAML: evidence_only
```

## 10.4 Doctor

`shinehe doctor` 必须检查：

- 当前知识模式；
- Raw 索引状态；
- Canonical Store 是否存在；
- 可 Serving Claim 数；
- 被 Gate 排除的 Claim 数及原因；
- stale / unsupported / unresolved 数；
- Evidence 可解析率；
- Projection parity；
- Wiki 失败时 Raw fallback 是否可用；
- Authoring 是否安全配置；
- MCP 实际注册工具；
- 维护中心运行状态和 Automation Level；
- Pending / Running / Waiting Review / Failed / Dead Letter 任务数；
- 最近一次全量校验、Projection Check 和质量审计时间；
- 保护性自动动作失败数；
- Review Queue 的 P0 / P1 积压和最长等待时间。

---


## 11. Wiki 自动化维护中心（Maintenance Center）

维护中心应作为项目已有 GUI / Web Admin 的统一维护入口，并通过共享 Service 与 API 复用业务逻辑。

若现有 GUI 与 Web Client 的成熟度不同，实施顺序必须是：

1. 先实现无 UI 依赖的 Maintenance Domain Service；
2. 再实现稳定的 API / CLI；
3. 最后接入至少一个现有管理界面；
4. 另一界面复用同一 API，不复制维护逻辑。

## 11.1 产品目标

维护中心需要让使用者在一个位置完成：

- 查看知识库健康状态；
- 追踪来源变更；
- 查看自动影响分析；
- 管理维护任务；
- 审阅 Draft、Conflict、Stale 和 Correction；
- 对比 Claim 与原始 Evidence；
- 确认、拒绝、修正、暂缓和发布；
- 查看审计历史；
- 执行安全修复；
- 验证修复后是否恢复 Serving。

维护中心不负责编辑 Raw Source，也不应成为独立 Wiki 编辑器。

## 11.2 维护对象

维护中心至少管理以下对象：

### Source Event

```json
{
  "event_id": "evt_...",
  "knowledge_id": "doc_...",
  "event_type": "created | updated | deleted",
  "source_path": "...",
  "source_hash_before": "...",
  "source_hash_after": "...",
  "created_at": "..."
}
```

### Maintenance Job

```json
{
  "job_id": "mjob_...",
  "job_type": "impact_plan | protective_rebuild | validation | projection_repair | draft_generation | publish",
  "risk_level": "R0 | R1 | R2 | R3 | R4",
  "status": "pending | running | waiting_review | completed | failed | cancelled | dead_letter",
  "idempotency_key": "...",
  "attempt": 1,
  "source_event_ids": [],
  "affected_claim_ids": [],
  "affected_page_ids": [],
  "created_at": "...",
  "started_at": null,
  "finished_at": null
}
```

### Review Item

```json
{
  "review_id": "review_...",
  "review_type": "new_claim | correction | conflict | stale_rebuild | publish | migration | projection_drift",
  "priority": "P0 | P1 | P2 | P3",
  "risk_level": "R3",
  "before": {},
  "proposed": {},
  "evidence": [],
  "reason_codes": [],
  "status": "open | assigned | approved | rejected | deferred | superseded",
  "created_by": "system | agent | user",
  "created_at": "..."
}
```

### Health Snapshot

记录：

- Active / Draft / Disputed / Unsupported / Retracted Claim 数；
- Servable Claim 数；
- Evidence 可解析率；
- stale Evidence 数；
- Projection Drift 数；
- Published Page 引用不合格 Claim 数；
- 未处理高风险 Review 数；
- 失败任务数；
- Raw / Wiki / Hybrid 最近质量指标；
- Serving Fallback 次数。

## 11.3 自动维护主流程

```text
Raw Source Event
    ↓
事件去重与 Debounce
    ↓
Raw Index 完成并提交
    ↓
生成 Impact Plan（Dry Run）
    ↓
Policy Engine 风险分类
    ↓
┌───────────────────────────────┐
│ R0 观测：自动记录             │
│ R1 保护：自动执行             │
│ R2 结构修复：按策略执行        │
│ R3 语义 Draft：生成后待审阅    │
│ R4 发布/删除/迁移：人工确认    │
└───────────────────────────────┘
    ↓
Validator + Serving Gate
    ↓
Review Queue / 自动完成
    ↓
Repository Transaction
    ↓
Outbox + Projection
    ↓
Post Validation + Parity
    ↓
Serving 恢复或继续隔离
```

关键顺序要求：

1. Raw Index 必须先成功提交；
2. Impact Plan 必须先以 Dry Run 生成；
3. 风险策略必须在写入前决定；
4. R1 操作用于降低风险，不得扩大 Serving 范围；
5. R3 只生成 Draft，不改变 Active 结论；
6. R4 在人工确认前不得写正式状态；
7. 写入后必须执行 Validator；
8. 发布前必须验证 Projection Parity；
9. 失败时保持 Claim 不 Serving 或恢复到写入前状态；
10. 所有步骤必须可追踪到同一个 `correlation_id`。

## 11.4 风险分级

| 等级 | 定义 | 典型动作 | 默认策略 |
|---|---|---|---|
| R0 | 只读观测 | 健康快照、质量统计、影响 Dry Run | 自动 |
| R1 | 保护性、单调、可逆 | Evidence stale、Claim unsupported、Page review、Serving 排除 | 自动 |
| R2 | 不改变知识语义的结构修复 | Outbox Recover、Projection Rebuild、索引缺口修复 | 默认重试；自动修复需策略允许 |
| R3 | 生成或修改语义 Draft | 新 Claim Draft、Correction Draft、Page Draft、Merge Suggestion | 自动生成建议，人工审阅 |
| R4 | 正式知识决策或破坏性操作 | Publish、Conflict Resolution、Retract、Delete、Migration Primary | 人工确认 |

### R1 自动执行的约束

R1 只有同时满足以下条件才可自动执行：

- 操作会减少而不是扩大可 Serving 知识；
- 操作可通过历史 Revision 恢复；
- 操作不修改 Raw Source；
- 操作不改变 Claim statement；
- 操作不把 Draft 升级为 Active；
- 操作通过 Repository Transaction；
- 操作写入 Operation Log；
- 操作失败时默认保持“不 Serving”。

## 11.5 维护策略引擎

建议新增统一 `MaintenancePolicyEngine`，输入：

- 当前运行模式；
- Automation Level；
- Job Type；
- Risk Level；
- Claim / Page 当前状态；
- Evidence 状态；
- Source Event；
- 用户配置；
- 是否有人工确认 Token。

输出：

```json
{
  "decision": "auto_execute | create_review | block | dry_run_only",
  "risk_level": "R1",
  "reason_codes": [],
  "required_checks": [],
  "required_permissions": []
}
```

策略不得散落在 Scheduler、API、GUI 和 MCP 各自实现。

## 11.6 审阅队列

Review Queue 至少支持：

- 按优先级、类型、来源、Claim 状态和创建时间筛选；
- 查看修改前后差异；
- 查看 Claim 与 Evidence 原文并排对照；
- 查看单位、型号、地区、时间、否定极性和强度词差异；
- 查看受影响页面和潜在 Serving 影响；
- `confirm / reject / correct / needs_review / defer`；
- 批量处理低风险同类项；
- 高风险动作逐项确认；
- 记录审阅人、说明、时间和决策依据；
- 审阅后自动执行 Validator 和 Projection Check。

优先级建议：

| 优先级 | 条件 |
|---|---|
| P0 | 曾经可 Serving 但证据已失效；保护动作失败；错误知识可能仍在 Serving |
| P1 | 高频 Claim 冲突；Published Page 受影响；Projection Drift |
| P2 | 新 Claim Draft；普通 Correction；缺少附加证据 |
| P3 | 低频页面整理、标签、别名和非关键结构问题 |

## 11.7 任务系统

优先复用现有异步 Job 基础设施；只有现有模型无法表达维护状态时才扩展，不得创建完全平行的第二套任务框架。

任务必须具备：

- 持久化状态；
- 幂等键；
- 去重；
- Lease / Heartbeat；
- 可取消；
- 指数退避重试；
- 单任务失败不阻断其他任务；
- Dead Letter；
- Correlation ID；
- 输入和输出摘要；
- Error Category；
- 可重放；
- 保留期策略。

状态机：

```text
pending
  → running
  → completed
  → waiting_review
  → failed → pending(retry)
  → dead_letter
  → cancelled
```

不得从 `waiting_review` 自动转为 `completed`，除非 Review Item 已有明确批准记录。

## 11.8 触发与调度

### 事件驱动

- 文件新增；
- 文件修改；
- 文件删除；
- Raw Reindex；
- Claim Feedback；
- Projection Drift；
- Validator Error；
- Publish；
- Rollback。

### 周期任务

- 每日：Serving Gate 全量轻检查；
- 每日：Evidence 可解析性抽查或增量检查；
- 每日：Projection Parity；
- 每周：高频 Claim 质量抽样；
- 每周：Draft / Disputed / Unsupported 积压报告；
- 每月：全量 Canonical Validation；
- 每月：Raw / Wiki / Hybrid 对照评测；
- 发布前：完整质量门禁。

所有时间必须可配置，不得在业务代码中硬编码。

## 11.9 维护中心界面

建议导航：

1. **Overview**
   - 总体健康分；
   - P0 / P1 风险；
   - Serving 状态；
   - 最近自动维护；
   - Review 积压；
   - 失败任务。

2. **Review Queue**
   - Claim / Page 差异；
   - Evidence 对照；
   - 审阅动作；
   - 批量低风险处理。

3. **Jobs**
   - Pending / Running / Failed / Dead Letter；
   - 重试、取消、查看日志；
   - Correlation Trace。

4. **Sources**
   - 来源版本；
   - 最近变更；
   - 影响范围；
   - Rebuild Plan。

5. **Claims**
   - 状态、证据、健康度、使用频率；
   - Claim 历史和 Revision；
   - Serving Eligibility。

6. **Pages**
   - Published / Review / Draft；
   - 引用 Claim；
   - 受影响范围。

7. **Health & Quality**
   - Validator；
   - Projection；
   - Raw / Wiki / Hybrid Eval；
   - 趋势图。

8. **Audit**
   - 自动动作；
   - 人工动作；
   - 配置变更；
   - 回滚记录。

界面不得提供绕过 Gate 的“强制发布”快捷入口。必要的紧急操作必须进入高级确认流程并写审计。

## 11.10 CLI 与 API

建议 CLI：

```bash
shinehe maintenance status
shinehe maintenance health
shinehe maintenance jobs list
shinehe maintenance jobs retry <job_id>
shinehe maintenance jobs cancel <job_id>
shinehe maintenance review list
shinehe maintenance review show <review_id>
shinehe maintenance review approve <review_id>
shinehe maintenance review reject <review_id> --note "..."
shinehe maintenance run validation
shinehe maintenance run projection-check
shinehe maintenance run full-audit
shinehe maintenance plan source <knowledge_id> --event update
```

要求：

- 所有写命令支持 `--dry-run`；
- 高风险动作要求交互确认或明确 `--confirm`；
- CLI、API 和 UI 必须调用相同 Service；
- API 返回稳定 Job / Review / Health 契约；
- 默认 MCP Core 不暴露这些写能力；
- `kb_capabilities` 只返回维护状态摘要。

## 11.11 权限与审计

逻辑角色：

| 角色 | 权限 |
|---|---|
| Reader | 查看 Health、Job、Review、Claim 和 Evidence |
| Reviewer | Confirm、Reject、Correct、Needs Review、批准 Publish |
| Maintainer | Rebuild、Projection Repair、Migration、Rollback、配置维护 |

即使单人部署，也必须保留角色语义，避免 Agent 默认获得 Maintainer 权限。

所有维护写入必须记录：

- operator；
- source；
- correlation_id；
- job_id；
- review_id；
- before / after；
- reason；
- timestamp；
- config snapshot；
- affected objects；
- rollback reference。

## 11.12 健康指标与告警

硬门禁指标：

```text
Stale Claim Serving Rate = 0
Unsupported Claim Serving Rate = 0
Retracted Claim Serving Rate = 0
Published Page 引用 Draft Claim = 0
Active Claim 无有效 Supports Evidence = 0
Projection Drift = 0（发布前）
Raw Fallback Success Rate = 1.00
```

运营指标：

- Evidence Resolvability；
- Review Queue Aging；
- P0 / P1 平均处理时间；
- Job Success Rate；
- Protective Action Success Rate；
- Dead Letter 数；
- Claim Conflict Rate；
- Draft → Active 转化率；
- 用户反馈关闭率；
- Hybrid Gain over Raw；
- Serving Fallback 频率。

告警不得只显示“健康分下降”，必须给出对象、原因代码和建议动作。

## 11.13 故障与降级

维护中心故障时：

- Raw Search 必须继续；
- Verified Wiki 只使用上一次已通过 Serving Gate 的快照或实时安全读取；
- 无法确认有效性的 Claim 必须排除；
- Scheduler 暂停不得自动扩大 Serving；
- P0 保护动作失败时应立即让相关 Claim 非 Serving；
- UI 不可用不影响 CLI / API；
- Projection 失败不覆盖 Canonical；
- Canonical 写失败必须事务回滚；
- Job Store 损坏不得自动重新执行高风险历史任务。

## 11.14 自动化验收原则

自动维护只有满足以下条件才能视为成功：

1. 自动化减少人工重复劳动；
2. 自动化不会扩大未经审阅的知识 Serving 范围；
3. 来源变化后，受影响 Claim 能及时退出 Serving；
4. 保护动作失败时系统采取保守降级；
5. 所有正式知识变化都可追溯到 Review 或明确策略；
6. 所有动作可审计、可重放、可回滚；
7. 维护中心关闭后，Raw Retrieval 和现有查询仍可用。


## 12. 核心模块改造清单

Agent 必须先核对实际代码，再实施。

## 12.1 `src/services/project_setup.py`

修改：

- 支持 `verified`、`authoring`、`evidence_only`；
- 默认 `verified`；
- `wiki_first` 兼容映射到 `authoring`；
- `legacy` 兼容映射到 `evidence_only`；
- 拆分 Verified Wiki Read 与 Authoring 配置；
- 默认不创建 Authoring 目录；
- 已有显式配置优先；
- 不静默覆盖旧配置。

## 12.2 `src/cli.py`

修改：

- `init --mode verified|authoring|evidence-only`；
- 帮助文本说明三档差异；
- 仅 Authoring 调用 `write_wiki_first_layout()`；
- 输出 Raw、Verified Wiki、Authoring 三项状态；
- 保持旧参数兼容；
- 现有 `shinehe wiki ...` 命令继续保留，但在非 Authoring 模式写操作前给出明确拒绝或引导。

## 12.3 `src/services/wiki_repository.py`

增加或整理只读 Serving API：

- `list_servable_claims(...)`
- `get_servable_claim(...)`
- `resolve_claim_evidence(...)`
- `get_claim_serving_diagnostics(...)`

要求：

- 默认不返回 Draft / Stale / Unsupported / Retracted；
- 不读取 staging；
- 不产生写副作用；
- 支持依赖注入和测试。

## 12.4 新增或整理 `WikiServingGate`

建议路径：

```text
src/services/wiki_serving_gate.py
```

职责：

- Claim 状态门禁；
- Evidence 存在性；
- Evidence Hash / freshness；
- Validation；
- Review Required；
- Scope / Unit / Polarity / Intensity 诊断；
- Reason Code；
- Serving Result。

建议接口：

```python
class WikiServingGate:
    def evaluate(self, claim: Claim) -> ServingDecision:
        ...
```

不得把该逻辑散落在 MCP、SearchService 和 API 三处。

## 12.5 `src/services/search_service.py`

改造为统一检索编排：

- 支持 `evidence_only`；
- 支持 `verified`；
- 调用 Raw Retrieval；
- 调用 Verified Wiki Retrieval；
- 执行 Gate；
- Evidence Resolution；
- Candidate Normalization；
- Fusion；
- Conflict / Freshness；
- 保留现有超时和降级；
- Wiki 失败时 Raw fallback；
- 输出统一 Trace。

不要在本阶段重写现有 Raw Retrieval 算法。

## 12.6 `src/services/rag_pipeline.py`

如当前主路径使用该模块：

- 增加 `verified_wiki_retrieval` Stage；
- 增加 `evidence_alignment` Stage；
- 增加 `conflict_check` Stage；
- Stage 必须可关闭；
- Wiki Stage 超时不能阻断 Raw；
- Trace 记录每阶段候选数量、过滤原因和耗时。

如 SearchService 才是实际统一入口，应避免形成两套不同融合逻辑。必须选择一个编排源，另一个复用它。

## 12.7 `src/services/citation_builder.py`

支持：

- Claim Citation；
- Evidence Citation；
- Claim → Evidence 链；
- Raw-only Citation；
- Conflict Citation；
- Claim 状态和 Revision；
- Evidence Stance。

引用结构必须向后兼容已有字段。

## 12.8 `src/mcp/tool_registry.py`

修改：

- `select_tools()` 接受 write policy；
- 同时考虑 mode、profile、experimental、side effect；
- `full` 和 `legacy` 不得绕过写策略；
- 默认隐藏不可执行工具；
- 加入契约测试。

## 12.9 `src/mcp/tool_profiles.py`

修改文案：

- Core 不称为纯只读；
- Verified Wiki 读取是 Core 内部能力，不需要暴露大量新工具；
- Authoring / Admin 工具说明写清副作用；
- 保持工具集合兼容，除非有充分测试证明需要调整。

## 12.10 `src/mcp_server.py`

修改：

- 启动时输出 mode、Raw、Wiki Serving、Authoring、fallback；
- 将 mode 和 write policy 传入工具选择；
- 增强 `kb_capabilities`；
- `search` / `ask` 使用统一编排；
- `read` 支持 Claim 和 Evidence；
- Wiki 初始化失败时降级，不得导致 Server 启动失败。

## 12.11 `src/core/container.py`

修改原则：

- Raw 核心服务正常 eager 或现有初始化；
- Wiki Serving 服务允许懒加载；
- Authoring 服务只在 Authoring 模式初始化；
- Canonical Migration / Rebuild 不在默认查询启动路径执行；
- Wiki 初始化异常可被隔离；
- 避免同一 Repository / Projection 注入错配。

## 12.12 `config.example.yaml`

改为：

- 默认 `verified`；
- Wiki Read 开启；
- Authoring 关闭；
- Auto Publish 关闭；
- Experimental 关闭；
- Write Policy 禁止；
- 单独列出 Authoring 覆盖示例；
- 单独列出 Evidence-only 示例；
- 解释 Canonical 写模式与 Serving 模式的区别。

## 12.13 README 与文档

README 第一屏应表达：

```text
Local documents
→ Raw Evidence Index
→ Verified Canonical Knowledge
→ MCP Search / Ask / Read
→ Traceable Answer
```

必须说明：

- Raw 是证据底座；
- Wiki V2 提升一致性、冲突识别和跨文档综合；
- 默认只读取已验证 Claim；
- 默认不会自动修改知识；
- Wiki 无法使用时自动 Raw fallback；
- Authoring 是高级能力。

不得将 Wiki 描述为默认自动生成并自动发布的第二事实库。

---


## 12.14 新增 `MaintenancePolicyEngine`

建议路径：

```text
src/services/maintenance_policy.py
```

职责：

- 风险分类；
- Automation Level 判断；
- Mode / Permission / Policy 合并；
- 输出 auto / review / block / dry-run；
- 统一原因代码；
- 不执行实际写操作。

## 12.15 新增 `WikiMaintenanceService`

建议路径：

```text
src/services/wiki_maintenance_service.py
```

职责：

- 接收 Source Event；
- 生成和执行 Impact Plan；
- 调用现有 `WikiRebuildService`、`WikiValidator`、`WikiFeedbackService`；
- 创建 Maintenance Job 和 Review Item；
- 统一 Correlation ID；
- 执行 Post Validation；
- 输出结构化维护结果。

不得复制现有 Rebuild、Feedback 和 Validation 逻辑。

## 12.16 任务存储与调度

建议优先扩展现有 Job 模型和调度器。

如必须新增模块，可使用：

```text
src/services/maintenance_job_store.py
src/services/maintenance_scheduler.py
```

要求：

- 持久化；
- 幂等；
- 重试；
- Dead Letter；
- 状态机；
- 任务 Lease；
- 单任务失败隔离；
- 可观测性。

## 12.17 Review Queue Service

建议路径：

```text
src/services/wiki_review_queue.py
```

职责：

- Review Item 生命周期；
- 优先级；
- 分配；
- Approve / Reject / Correct / Defer；
- 调用 Feedback / Repository；
- 审阅记录；
- 审阅后校验；
- 防止重复批准。

## 12.18 Maintenance API / CLI

建议：

```text
src/api/routes/maintenance.py
src/cli.py
```

API 和 CLI 只做输入校验、权限和输出格式，不实现维护业务逻辑。

## 12.19 Maintenance Center UI

优先复用现有前端和设计系统。

建议模块：

```text
client/src/pages/MaintenanceCenter/
client/src/services/maintenance.ts
```

具体路径以仓库当前前端结构为准。

第一阶段必须完成：

- Overview；
- Review Queue；
- Jobs；
- Health；
- Audit Detail。

后续再增加 Sources / Claims / Pages 的深度管理视图。

## 12.20 Operation Log 与 Health Snapshot

复用现有 Operation Log，并扩展维护动作类型。

Health Snapshot 可存入现有 SQLite 数据库的独立 read model，但不得复制 Claim / Page 正文成为第二事实源。


## 13. 统一响应契约

## 13.1 Search Result

```json
{
  "title": "FTTR 安装要求",
  "text": "...",
  "score": 0.91,
  "candidate_type": "claim",
  "source_layer": "canonical",
  "claim": {
    "claim_id": "claim_123",
    "status": "active",
    "revision": 4
  },
  "evidence": [
    {
      "knowledge_id": "doc_1",
      "block_id": "block_8",
      "path": "D:/docs/manual.pdf",
      "location": {"page": 12},
      "stance": "supports"
    }
  ],
  "match_channels": [
    "wiki_claim",
    "semantic",
    "keyword"
  ],
  "score_breakdown": {},
  "warnings": []
}
```

Raw-only 结果保持：

```json
{
  "candidate_type": "raw_block",
  "source_layer": "evidence"
}
```

## 13.2 Ask Result

建议增加：

```json
{
  "answer": "...",
  "answer_mode": "hybrid_verified | raw_only | conflict_disclosure",
  "claims_used": [],
  "raw_evidence_used": [],
  "conflicts": [],
  "fallbacks": [],
  "warnings": [],
  "trace_id": "..."
}
```

## 13.3 降级透明度

Wiki 无数据或异常时：

```json
{
  "answer_mode": "raw_only",
  "fallbacks": [
    {
      "from": "verified_wiki",
      "to": "raw_retrieval",
      "reason": "wiki_store_unavailable"
    }
  ]
}
```

普通最终文案不必显示内部堆栈，但开发 Trace 必须完整。

---

## 14. 评测体系

融合是否成立，不能只看 Wiki 命中率。

必须建立三路对照：

1. `Raw Only`
2. `Wiki Only`
3. `Hybrid Verified`

## 14.1 核心问题

评测必须回答：

- Hybrid 是否比 Raw 更准确；
- Wiki 是否降低重复问答波动；
- Wiki 是否正确处理跨文档综合；
- Wiki 是否保留数值、单位、地区、型号和强度限定；
- stale Claim 是否被使用；
- unsupported Claim 是否进入答案；
- 冲突是否被披露；
- 最终引用是否能回到原始 Evidence；
- Wiki 失败时 Raw fallback 是否稳定。

## 14.2 数据集

最低 150 条，建议：

| 类型 | 最低数量 |
|---|---:|
| 单文档事实 | 25 |
| 中文专有名词 / 缩写 | 15 |
| 跨文档综合 | 25 |
| 概念定义 / 实体总结 | 15 |
| 精确数值 / 单位 / 型号 | 15 |
| 地区 / 时间 / 条件限定 | 10 |
| 冲突来源 | 15 |
| 文件更新和过期 | 10 |
| 无答案 | 15 |
| PDF / DOCX / Excel 定位 | 20 |
| **总计** | **165** |

可优先使用通信行业数据：

- FTTR；
- PON；
- OLT / ONU；
- 5G；
- 网络维护；
- 业务规则；
- 营业厅服务；
- 视频双录；
- 套餐与资费；
- 故障工单。

## 14.3 指标

### Raw Retrieval 指标

- Recall@5；
- MRR；
- nDCG@10；
- No-Answer Accuracy；
- Citation Location Completeness。

### Wiki Serving 指标

- Servable Claim Precision；
- Evidence Resolvability；
- Unsupported Claim Serving Rate；
- Stale Claim Serving Rate；
- Claim-Evidence Alignment；
- Conflict Detection Recall；
- Scope Preservation；
- Unit Preservation；
- Polarity Preservation；
- Intensity Preservation。

### End-to-End 指标

- Answer Correctness；
- Citation Correctness；
- Evidence Coverage；
- Conditional Detail Preservation；
- Contradiction Disclosure Accuracy；
- Answer Stability；
- Hybrid Gain over Raw；
- P50 / P95；
- Raw Fallback Success Rate。

## 14.4 建议门槛

```text
Raw Recall@5 >= 0.85
Raw MRR >= 0.75
Raw nDCG@10 >= 0.78
No-Answer Accuracy >= 0.80
Citation Location Completeness = 1.00

Hybrid Answer Correctness >= Raw Answer Correctness
Hybrid Citation Correctness >= 0.95
Unsupported Claim Serving Rate = 0
Stale Claim Serving Rate = 0
Evidence Resolvability >= 0.99
Conflict Detection Recall >= 0.90
Raw Fallback Success Rate = 1.00
```

允许 Hybrid 延迟增加，但必须记录，并建议：

```text
Hybrid P95 <= Raw P95 × 1.75
```

若无法达到，必须提供性能分析，不得关闭正确性门禁换取速度。

## 14.5 CI 分层

### 每次 PR 强制

- 单元测试；
- 确定性 fake-embedding；
- Gate 测试；
- Raw / Wiki / Hybrid 小型 Golden Set；
- Citation Contract；
- Fallback；
- Python 兼容测试。

### 定时或发布前

- 真实 Embedding；
- 本地 Reranker；
- 大数据集 Hybrid Eval；
- 性能；
- 内存；
- 索引规模；
- Wiki Evolution。

---

## 15. 测试要求

## 15.1 Serving Gate

必须覆盖：

- Active + valid Evidence → 允许；
- Draft → 排除；
- Stale → 排除并 Raw fallback；
- Unsupported → 排除；
- Retracted → 排除；
- Missing Block → 排除；
- Deleted Knowledge → 排除；
- Hash mismatch → 排除或 stale；
- Validation failed → 排除；
- Review required → 排除；
- Unit mismatch；
- Model mismatch；
- Region mismatch；
- Polarity mismatch；
- Intensity mismatch。

## 15.2 查询管线

必须覆盖：

- Wiki + Raw 均命中；
- 只有 Wiki 命中；
- 只有 Raw 命中；
- Wiki 空；
- Wiki 超时；
- Wiki Repository 异常；
- Projection drift；
- Claim 有效但 Evidence 读取失败；
- Raw Vector 失败但 FTS 可用；
- Rerank 失败；
- Router 失败；
- 冲突 Claim；
- 最新性问题；
- 无答案问题。

## 15.3 Authoring 边界

必须覆盖：

- Verified 模式无法自动写 Claim；
- Verified 模式不注册 Authoring 工具；
- Authoring 模式写操作仍需 policy；
- Auto Publish 默认关闭；
- HTTP 写默认拒绝；
- Review Gate 不可绕过；
- Migration 不在查询启动时自动运行；
- Authoring 故障不影响 Raw Search。

## 15.4 兼容

必须覆盖：

- `wiki_first` 旧配置；
- `legacy` 旧配置；
- 无 mode 配置；
- 旧 MCP profile；
- 旧 Citation 消费方；
- 现有 GUI；
- 现有 API；
- Windows Service；
- Docker MCP；
- `run_mcp.py` 旧入口。


## 15.5 维护中心

必须覆盖：

### Policy Engine

- Verified + R1 → auto_execute；
- Verified + R3 → create_review；
- Authoring + R3 → 生成 Draft 后 waiting_review；
- 任意模式 + R4 → 人工确认；
- Evidence-only → 不运行 Wiki 维护写任务；
- 配置不能绕过硬门禁。

### Event / Job

- 同一 Source 连续更新去重；
- Delete 优先于 Update；
- 幂等键阻止重复执行；
- Job Lease 超时可恢复；
- 重试达到上限进入 Dead Letter；
- 单任务失败不阻断其他任务；
- Cancel 在事务前后语义正确；
- waiting_review 不会自动完成。

### Protective Automation

- Block 更新自动标记对应 Evidence stale；
- 无剩余 Supports 的 Claim 自动变 unsupported；
- 受影响 Published Page 自动转 review；
- Claim 立即退出 Serving；
- 保护动作失败时 Claim 保持非 Serving；
- R1 不修改 statement；
- 所有 R1 操作有 Audit Log。

### Review Queue

- Review Item 去重；
- Before / Proposed / Evidence 完整；
- Approve 后运行 Validator；
- Reject 需要 Note；
- Correct 后状态为 Draft；
- Publish 前检查 Validation 和 Projection；
- 重复批准不产生二次写入；
- 高风险批量操作被阻止。

### Maintenance Center API / CLI / UI

- 状态、任务、审阅、健康契约稳定；
- CLI 写命令支持 Dry Run；
- 权限不足拒绝；
- UI 操作调用共享 Service；
- Maintenance UI 故障不影响 MCP Search；
- P0 告警包含对象与原因代码。

### 周期审计

- Daily Validation 可重复执行；
- Projection Check 可重复执行；
- Monthly Audit 不修改语义；
- 定时任务时间可配置；
- 多实例下同一任务仅一个执行者。


---

## 16. 分阶段实施计划

## Phase 0：冻结基线

任务：

- 全量测试；
- Ruff；
- mypy；
- 前端；
- Docker；
- Raw Retrieval Eval；
- Wiki Eval；
- Knowledge Evolution Eval；
- 保存当前工具列表；
- 保存当前初始化配置；
- 记录现有 Wiki 可 Serving Claim 数；
- 记录当前查询延迟。

输出：

```text
docs/superpowers/reviews/verified-hybrid-baseline.md
```

不得修改生产行为。

---

## Phase 1：模式与配置语义

目标：

- 引入 `verified / authoring / evidence_only`；
- 兼容旧值；
- 分离 Wiki Read 与 Authoring；
- 默认 Verified。

任务：

- ProjectSetup；
- CLI；
- Config Example；
- Doctor 基础状态；
- 初始化测试；
- 迁移文档骨架。

验收：

- 默认初始化不开启写入；
- 已有 `wiki_first` 用户不被切成只读；
- 新用户无 Wiki 数据也能正常使用 Raw。

---

## Phase 2：Wiki Serving Gate

目标：

- 建立唯一 Claim Serving 入口。

任务：

- 新增 `WikiServingGate`；
- Repository 只读 Serving API；
- Reason Code；
- Evidence Resolution；
- Freshness / Hash；
- 单元测试；
- 诊断统计。

验收：

- 不合格 Claim 无法进入 Search / Ask；
- Stale 和 Unsupported Serving Rate 为 0；
- Gate 不调用不必要的 LLM。

---

## Phase 3：统一检索编排

目标：

- Raw 与 Verified Wiki 在同一 SearchService 中融合。

任务：

- Query Router；
- Wiki Retrieval；
- Raw Retrieval 复用；
- Candidate Normalization；
- Fusion；
- Timeout；
- Raw Fallback；
- Trace；
- 集成测试。

验收：

- Wiki 异常不影响 Raw；
- Evidence-only 与 Verified 输出契约稳定；
- Wiki Claim 必须带原始 Evidence。

---

## Phase 4：回答、冲突和引用

目标：

- `ask` 真正使用规范知识，同时保留证据链。

任务：

- Context Assembly；
- Conflict Disclosure；
- Freshness；
- Claim + Evidence Citation；
- `read` 扩展；
- No-Answer；
- 回答模式标记。

验收：

- 不出现只引用 Wiki 页面而无 Evidence 的主结论；
- 冲突问题不静默选边；
- 最新性问题不使用 stale Claim。

---


## Phase 5：自动化维护中心

目标：

- 将现有 Rebuild、Validator、Feedback、Projection、Operation Log 和 Job 能力编排成正式维护闭环；
- 自动执行低风险保护；
- 将语义变更送入审阅；
- 提供 Health、Jobs、Review 和 Audit 控制面。

任务：

- Maintenance Policy Engine；
- Maintenance Service；
- Job Store / Scheduler；
- Risk Level；
- Review Queue；
- Health Snapshot；
- Protective Automation；
- CLI / API；
- 至少一个 Maintenance Center UI；
- Audit 和 Correlation Trace；
- 故障降级；
- 单元和集成测试。

验收：

- Source 更新后自动生成 Impact Plan；
- R1 自动完成并立即保护 Serving；
- R3 自动生成 Draft 但不发布；
- R4 无人工确认不能执行；
- P0 保护失败时相关 Claim 自动非 Serving；
- Review Item 可从 Evidence 对照完成审阅；
- Job 可重试、取消、进入 Dead Letter；
- 维护中心不可用不影响 Raw Search；
- 所有维护写入有 Operation Log；
- 关闭 Maintenance Center 后查询功能仍正常。

---

## Phase 6：MCP 与 Authoring 安全边界

目标：

- 默认工具面稳定；
- Authoring 显式、安全、可审计。

任务：

- Tool Registry 过滤；
- `kb_capabilities`；
- Authoring 工具条件；
- 写策略；
- HTTP 限制；
- 启动日志；
- GUI / API 状态一致；
- Maintenance Center 权限与 MCP 写策略边界一致；
- 内部保护性维护不被误判为 Agent 写权限。

验收：

- Verified 默认无写工具；
- Authoring 仍必须通过 policy；
- Legacy 不绕过安全策略。

---

## Phase 7：Hybrid Eval

目标：

- 证明 Wiki 融合确实提高最终答案质量。

任务：

- 150+ 数据集；
- Raw / Wiki / Hybrid 对照；
- 通信行业样本；
- 冲突、时效、条件限定；
- 真实模型可选评测；
- 性能；
- 报告。

验收：

- Hybrid 正确率不低于 Raw；
- Unsupported / Stale Serving Rate 为 0；
- Citation Correctness 达标；
- 失败样本有分类分析。

---

## Phase 8：文档与发布

任务：

- README；
- README_zh；
- 架构文档；
- Serving Gate 文档；
- Authoring 文档；
- Migration；
- Release Notes；
- PROGRESS；
- 版本一致性检查；
- 安装和客户端验证。

最终评审：

```text
docs/superpowers/reviews/verified-hybrid-final-review.md
```

---

## 17. 文档结构

建议：

```text
docs/
├── getting-started/
│   ├── verified-mode.md
│   ├── authoring-mode.md
│   ├── evidence-only-mode.md
│   └── client-setup.md
├── architecture/
│   ├── evidence-layer.md
│   ├── canonical-knowledge-layer.md
│   ├── agent-serving-layer.md
│   └── hybrid-query-pipeline.md
├── wiki/
│   ├── serving-gate.md
│   ├── claim-status.md
│   ├── evidence-traceability.md
│   └── authoring-safety.md
├── maintenance/
│   ├── maintenance-center.md
│   ├── automation-policy.md
│   ├── review-queue.md
│   ├── jobs-and-retries.md
│   ├── health-and-alerts.md
│   └── operations-runbook.md
├── mcp/
│   ├── tools.md
│   ├── agent-usage.md
│   └── safety.md
├── evaluation/
│   ├── raw-retrieval.md
│   ├── wiki-serving.md
│   └── hybrid-knowledge.md
└── migration/
    └── v1.6-to-v1.7-verified-hybrid.md
```

---

## 18. Definition of Done

### 产品

- [ ] 项目只有一个统一定位；
- [ ] Raw 是明确的最终证据底座；
- [ ] Verified Wiki 默认参与读取增强；
- [ ] Authoring 默认关闭；
- [ ] Wiki 失败自动 Raw fallback；
- [ ] 默认 Agent 不需要理解两套搜索工具；
- [ ] README 主叙事不再在“纯检索”和“全自动 Wiki”之间摆动；
- [ ] 维护中心成为唯一维护控制面，不新增第二套事实库；
- [ ] Verified 默认启用保护性自动维护，但不启用语义自动发布。

### 正确性

- [ ] 所有主结论可追溯到原始 Block；
- [ ] Stale Claim Serving Rate = 0；
- [ ] Unsupported Claim Serving Rate = 0；
- [ ] Retracted Claim 不进入结果；
- [ ] 冲突被披露；
- [ ] 最新性问题优先检查原始新文件；
- [ ] Wiki 不可用时 Raw 仍成功；
- [ ] Hybrid 正确率不低于 Raw；
- [ ] Source 变化后受影响 Claim 能自动退出 Serving；
- [ ] 保护动作失败时系统保持保守状态；
- [ ] Review Queue 中的变更均可回到原始 Evidence。

### 安全

- [ ] Verified 默认不注册写工具；
- [ ] Authoring 需要显式模式；
- [ ] Auto Publish 默认关闭；
- [ ] HTTP 写默认关闭；
- [ ] Review Gate 无法绕过；
- [ ] 写操作有审计和回滚；
- [ ] Legacy Profile 不绕过 write policy；
- [ ] 内部维护策略不绕过 Review Gate；
- [ ] R3 / R4 不会因 Automation Level 提升而自动发布；
- [ ] 所有维护写操作有 Audit、Correlation ID 和 Rollback Reference。

### 工程

- [ ] Python 3.10 / 3.11 / 3.12 通过；
- [ ] Ruff 通过；
- [ ] mypy 通过；
- [ ] 前端构建通过；
- [ ] Docker 构建通过；
- [ ] Raw Eval 通过；
- [ ] Wiki Eval 通过；
- [ ] Hybrid Eval 通过；
- [ ] Citation Contract 通过；
- [ ] Fallback 测试通过；
- [ ] Windows 入口通过。


### 维护中心

- [ ] Source Event 可触发幂等维护任务；
- [ ] R0 / R1 / R2 / R3 / R4 风险策略有测试；
- [ ] R1 可自动执行且不改变知识语义；
- [ ] R3 只生成 Draft；
- [ ] R4 必须人工确认；
- [ ] Review Queue 支持差异和 Evidence 对照；
- [ ] Job 支持持久化、重试、取消和 Dead Letter；
- [ ] Health Snapshot 和历史趋势可查看；
- [ ] P0 / P1 风险有明确告警；
- [ ] Maintenance Center UI、CLI 和 API 复用同一 Service；
- [ ] 维护中心故障不影响 Raw Retrieval；
- [ ] 关闭自动化后系统仍可人工维护。

### 文档

- [ ] README 和源码版本一致；
- [ ] 中英文 README 一致；
- [ ] 三层架构有文档；
- [ ] 三个运行档位有文档；
- [ ] Migration 完整；
- [ ] Eval 明确区分 fake 与真实模型；
- [ ] PROGRESS 更新；
- [ ] Release Notes 完整；
- [ ] Maintenance Operations Runbook 完整；
- [ ] 自动化风险等级、默认策略和人工边界有文档。

---

## 19. 风险与缓解

### 风险 1：Wiki 错误反而降低答案准确率

缓解：

- Serving Gate；
- 只允许 Active + Valid Evidence；
- Hybrid 对照评测；
- Raw Evidence 必须随 Claim 返回；
- Hybrid 不得低于 Raw；
- 可随时切换 Evidence-only。

### 风险 2：查询延迟增加

缓解：

- Wiki 和 Raw 并行；
- Gate 使用确定性检查；
- 缓存 Servable Claim；
- 限制 Wiki 候选数；
- 超时 Raw fallback；
- 记录 P50 / P95；
- 路由精确问题时降低 Wiki 查询规模。

### 风险 3：旧 Wiki-First 用户行为改变

缓解：

- `wiki_first` 映射 Authoring；
- 不自动修改配置；
- 旧用户保留写能力和目录；
- 增加迁移提示；
- 发布前用真实旧配置回归。

### 风险 4：Canonical Store 损坏影响默认使用

缓解：

- Wiki 懒加载；
- Repository 异常隔离；
- Projection parity 诊断；
- Raw fallback；
- Doctor 修复建议；
- 不在普通查询时自动迁移。

### 风险 5：工具面再次膨胀

缓解：

- 默认仍使用统一 `search / ask / read`；
- Claim 读取并入 `read`；
- Authoring 工具仅显式模式注册；
- `kb_capabilities` 提供能力发现；
- 不为每个 Wiki 内部动作新增 Core 工具。

---


### 风险 6：自动维护产生错误写入

缓解：

- 只有 R1 保护动作默认自动执行；
- R1 不修改 statement、不升级 Active、不扩大 Serving；
- R2 自动修复默认受限；
- R3 只生成 Draft；
- R4 强制人工确认；
- 所有写入走 Repository Transaction；
- 每个任务有 Idempotency Key；
- Post Validation 和 Projection Parity；
- Audit 和 Rollback Reference；
- 保护动作失败时默认排除相关 Claim。

## 20. 回滚策略

每阶段必须可独立回滚。

### 配置语义回滚

保留兼容开关：

```yaml
knowledge_workflow:
  mode: evidence_only
```

可立即关闭 Wiki Serving，不影响 Raw。

### Hybrid 回滚

建议临时开关：

```yaml
rag:
  verified_knowledge:
    enabled: false
```

关闭后恢复现有 Raw Retrieval。

### 工具过滤回滚

临时兼容：

```yaml
mcp:
  hide_disabled_write_tools: false
```

仅用于短期兼容，后续版本应移除。


### 维护中心回滚

支持逐级关闭：

```yaml
maintenance:
  center_enabled: false
```

关闭 UI 和调度，但不影响 Raw Retrieval。

```yaml
maintenance:
  automation_level: observe
```

只生成诊断和计划，不执行维护写入。

```yaml
maintenance:
  policy:
    auto_protective_actions: false
```

关闭 R1 自动写入，改为 Review Queue；Serving Gate 仍必须实时排除不合格 Claim。

回滚要求：

- 不删除现有 Job、Review 和 Audit 历史；
- 已执行的保护动作通过 Revision 恢复；
- 不自动恢复已被标记 stale 的 Evidence，除非重新验证；
- 不重放 R3 / R4 历史任务；
- 回滚配置本身写入 Operation Log。

### 数据安全

本轮不允许破坏性数据库迁移。

涉及配置或 Canonical 数据的操作必须：

- Dry Run；
- 备份；
- 原子写；
- 可回滚；
- 保留未知字段；
- 不在普通查询启动时自动执行。

---

## 21. Agent 建议提交序列

建议分支：

```bash
git checkout -b feat/verified-wiki-hybrid
```

建议提交：

1. `test: capture verified hybrid baseline`
2. `feat(config): introduce verified authoring and evidence-only modes`
3. `feat(wiki): add canonical claim serving gate`
4. `feat(search): fuse verified claims with raw retrieval`
5. `feat(answer): add evidence-backed claim citations and conflict disclosure`
6. `feat(maintenance): add policy engine and protective automation`
7. `feat(maintenance): add durable jobs and review queue`
8. `feat(maintenance-ui): add maintenance center overview and reviews`
9. `feat(mcp): enforce authoring and write-policy boundaries`
10. `test(eval): add raw wiki hybrid ablation suite`
11. `docs: align product positioning maintenance and migration guidance`
12. `ci: add hybrid maintenance and compatibility gates`
13. `chore(release): prepare verified hybrid release`

每个提交必须通过相关测试，不得把所有变化压成一个提交。

---

## 22. Agent 停止条件

出现以下任一情况，Agent 必须停止当前阶段：

- Hybrid 正确率明显低于 Raw；
- Wiki Claim 无法稳定追溯到 Block；
- Stale 或 Unsupported Claim 进入正式回答；
- 旧 `wiki_first` 配置无法运行；
- Wiki 异常导致 MCP 无法启动；
- 需要破坏性数据库迁移；
- 需要删除大量现有 Wiki V2 代码；
- 需要新增未在 Spec 定义的重大产品能力；
- 全量测试出现无法解释的系统性失败；
- 维护自动化把 Draft 升级为 Active 或绕过 Publish Review；
- 同一 Source Event 产生无法去重的重复维护写入；
- 维护中心需要复制 Canonical Claim / Page 作为第二事实源；
- P0 保护动作失败后相关 Claim 仍可 Serving。

生成：

```text
docs/superpowers/handoffs/verified-hybrid-blocker-<date>.md
```

内容包括：

- 问题；
- 复现；
- 影响；
- 已尝试方案；
- 指标变化；
- 推荐决策。

---

## 23. 最终验收场景

### 场景 A：新用户，没有 Wiki 数据

```bash
shinehe init --local --path ./docs --client claude-code
shinehe index ./docs
shinehe mcp --transport stdio
```

预期：

- 模式为 Verified；
- Wiki Read 已启用但状态为 Empty；
- 自动 Raw-only；
- 不报错；
- 不暴露 Authoring 工具；
- 引用完整。

### 场景 B：已有已验证 Wiki

同样使用 Verified。

预期：

- 定义和综合问题优先使用有效 Claim；
- Claim 附带 Raw Evidence；
- 精确位置问题优先 Raw；
- Hybrid 答案质量不低于 Raw；
- 不发生语义 Authoring 或自动发布；
- 允许维护中心执行保护性状态迁移并完整审计。

### 场景 C：Wiki 数据损坏

预期：

- Wiki 状态为 Degraded / Unavailable；
- 自动 Raw fallback；
- `kb_capabilities` 和 Doctor 可诊断；
- MCP 仍可搜索和读取。

### 场景 D：Authoring 用户

```bash
shinehe init --mode authoring --local --path ./knowledge
```

预期：

- 创建完整 Wiki 目录；
- Claim 工作流可用；
- Auto Publish 关闭；
- 写操作需确认；
- 已发布 Claim 可进入 Verified Serving；
- Draft 不进入普通回答。

### 场景 E：冲突知识

预期：

- 检索到冲突 Claim；
- 回答披露分歧；
- 双方 Evidence 可读取；
- 不静默选择一方；
- 冲突可进入 Authoring Review。

### 场景 F：来源更新

预期：

- 相关 Evidence Hash 变化；
- Claim 标记 stale 或进入 rebuild；
- stale Claim 不再 Serving；
- 回答回退最新 Raw；
- 重建并审阅后重新进入 Serving。

### 场景 G：旧配置

`knowledge_workflow.mode=wiki_first`

预期：

- 映射为 Authoring；
- 不需手工迁移即可运行；
- 提示新名称；
- 原有 Wiki 维护能力不丢失。

---


### 场景 H：维护中心自动闭环

操作：

1. 修改一个被 Active Claim 引用的 Raw 文件；
2. 等待 Source Event 和 Debounce；
3. 打开 Maintenance Center。

预期：

- Raw Index 先完成；
- 自动生成 Impact Plan；
- 变化 Block 对应 Evidence 被标记 stale；
- 无其他 Supports 的 Claim 自动变 unsupported；
- 相关 Page 自动转 review；
- Claim 立即退出 Serving；
- Review Queue 生成修订项；
- 界面显示 Before / Proposed / Evidence Diff；
- Reviewer 批准修订后运行 Validator；
- Projection Parity 通过后才能发布；
- 全流程拥有 Job、Review、Audit 和 Correlation Trace；
- 任一步失败均不影响 Raw Search。

## 24. 最终交付物

开发 Agent 必须提交：

1. 代码改动；
2. Serving Gate；
3. 统一检索编排；
4. Claim + Evidence 引用；
5. Maintenance Policy Engine；
6. Wiki Maintenance Service；
7. Durable Maintenance Jobs；
8. Review Queue；
9. Maintenance Center UI；
10. Maintenance CLI / API；
11. Health Snapshot 与告警；
12. MCP 安全边界；
13. 单元测试；
14. 集成测试；
15. Raw / Wiki / Hybrid Eval；
16. Maintenance Automation Eval；
17. CI 改动；
18. README / README_zh；
19. 三层架构与维护控制面文档；
20. 三档运行模式文档；
21. Maintenance Operations Runbook；
22. 迁移文档；
23. Release Notes；
24. PROGRESS 更新；
25. 基线报告；
26. 最终验收报告。

最终报告：

```text
docs/superpowers/reviews/verified-hybrid-final-review.md
```

必须回答：

- Wiki 是否真正提升了最终答案准确性；
- 哪类问题获得提升；
- 哪类问题仍应优先 Raw；
- 是否出现 Unsupported / Stale Claim；
- 引用是否全部可追溯；
- Wiki 故障是否能稳定降级；
- Authoring 是否被默认隔离；
- 保护性自动维护是否及时且安全；
- R3 / R4 是否始终经过审阅；
- Review Queue 和 Job 系统是否可操作、可审计、可回滚；
- 维护中心故障是否影响查询；
- 旧用户是否兼容；
- 是否建议发布。

---

## 25. 最终决策摘要

本 Spec 的最终设计不是“Retrieval 与 Wiki V2 分家”，而是：

```text
Raw Evidence
    ↓
Verified Canonical Knowledge
    ↓
Unified MCP Serving

Maintenance Control Plane 横向负责：监测、保护、校验、审阅、修复与审计
```

产品默认行为是：

```text
Raw Retrieval 始终可用
+ 已验证 Wiki 默认增强
+ 保护性维护自动执行
+ 语义变更进入审阅
+ 自动发布默认禁止
+ Wiki / 维护中心故障自动降级
+ 所有结论回到原始证据
```

所有开发任务都必须服务于以下目标：

> **让 Wiki V2 提升答案的一致性、准确性、冲突处理和知识演进能力，并通过自动化维护中心持续消除 stale、unsupported、projection drift 和审阅积压，同时不牺牲 Raw Retrieval 的可靠性、可追溯性和默认易用性。**
