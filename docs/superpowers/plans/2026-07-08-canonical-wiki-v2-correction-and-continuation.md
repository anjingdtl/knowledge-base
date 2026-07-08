# Canonical Wiki V2 纠偏与续建执行方案

> **目标仓库：** `anjingdtl/knowledge-base`  
> **执行对象：** Claude Code、Codex、Cursor Agent、Cline 等编码 Agent  
> **方案性质：** Wiki V2 中期纠偏，不撤销现有成果，不停止 Wiki V2 建设  
> **执行原则：** 先完成纠偏门禁，再进入主工作流切换、失效传播、迁移与反馈闭环  
> **建议分支：** 从当前最新稳定提交创建或继续使用独立 Wiki V2 功能分支  
> **禁止事项：** 未通过本方案规定的门禁前，不得开始 Canonical V2 主路径全面切换

---

## 1. 执行指令

继续建设 Canonical Wiki V2，但立即暂停以下工作：

- 不得把 Canonical V2 设置为默认写入路径；
- 不得让现有用户项目自动启用 V2；
- 不得开始大规模来源失效传播；
- 不得删除或弱化 legacy Wiki 回退路径；
- 不得继续增加新的 Claim action、页面类型或自动发布功能；
- 不得借 Wiki V2 顺手重构 GUI、RBAC、向量数据库、认证和全部 RAG 架构。

当前已经完成的 Canonical 模型、Schema、Repository、SQLite Projection、Claim Extractor、Claim Matcher、Merge Engine 和相关测试继续保留。

下一步不是推倒重来，而是在现有 Phase 3 与 Phase 4 之间插入一个强制的：

```text
Phase 3.5：Correction Gate
```

只有 Phase 3.5 全部通过，才能恢复 Phase 4—6 建设。

---

## 2. 本轮纠偏的核心判断

当前 Wiki V2 总体方向正确：

```text
Raw Source
    ↓
Evidence-backed Claim
    ↓
Canonical Markdown Wiki
    ↓
SQLite Projection
    ↓
MCP / API / GUI Retrieval
```

需要纠正的不是总架构，而是实施顺序和风险控制。

当前主要风险包括：

1. Claim Merge 已进入复杂语义判断，但专项评测体系尚未成为强制门禁；
2. `supports`、`refines`、`contradicts`、`supersedes` 等动作一旦误判，会污染 Canonical Store；
3. 文件、Claim、Page Registry、Outbox 和 Projection 涉及多对象写入，必须验证崩溃恢复和半写防护；
4. 现有项目仍存在不同检索入口，Wiki V2 不应继续复制新的读取与 fallback 逻辑；
5. 新服务仍可能通过全局 Config、Database 单例或 active container 获取依赖；
6. 从“实验代码可运行”到“主路径可切换”之间缺少 shadow、canary 和回滚阶段；
7. 当前测试数量很多，但测试数量不能替代 Claim 语义准确率。

本轮纠偏必须优先提高：

- 错误自动合并的发现能力；
- 写入一致性；
- 故障恢复能力；
- 新旧路径可比性；
- 可回滚性；
- 语义评测的精确率。

---

## 3. 全局铁律

后续所有任务必须遵守以下规则。

### 3.1 Raw Source 永远是最终证据源

Canonical Wiki 是整理后的权威知识层，但不得把生成内容当作原始事实。

任何 Claim 必须能够追溯到：

```text
knowledge_id
→ source_revision
→ block_id（能够定位时必须存在）
→ location
→ excerpt_hash
```

没有有效 Evidence 的自动生成事实不得进入 `active` 状态。

### 3.2 自动化应采用保守策略

Claim Matcher 的默认结果必须是：

```text
unresolved
```

只有在证据和作用域充分明确时，才允许自动输出：

- `supports`
- `refines`
- `contradicts`
- `supersedes`

原则：

> 漏合并可以进入人工审阅；错误合并会污染整个知识层。

因此，Wiki V2 的优化目标首先是自动动作的高精确率，而不是最大召回率。

### 3.3 Canonical 写入只有一个入口

所有 Canonical Page、Claim、Registry、Redirect、Staging、Outbox 写入必须通过：

```python
WikiRepository
```

业务服务不得直接：

- `write_text`
- `write_markdown`
- 修改 `pages.json`
- 修改 `redirects.json`
- 写 Claim YAML
- 调用旧 `insert_wiki_page`
- 直接写 V2 Projection 表

允许的例外只能存在于：

- migration adapter；
- legacy compatibility adapter；
- projection worker；
- 明确记录在架构守卫 allowlist 中的过渡代码。

每完成一个过渡任务，必须缩小 allowlist，不得扩大 allowlist 来绕过测试。

### 3.4 Projection 不是第二个知识系统

SQLite Projection 必须满足：

- 可以全部删除后从 Canonical Store 重建；
- 不产生 Canonical 中不存在的事实；
- 不允许独立修改 Claim 状态；
- 不允许独立修改 Page 内容；
- Projection 失败不回滚已经成功的 Canonical 写入；
- Projection drift 必须可检测和修复。

### 3.5 不允许在新模块中继续扩大全局依赖

以下新服务必须支持构造函数依赖注入：

- `WikiRepository`
- `WikiProjection`
- `WikiClaimExtractor`
- `WikiClaimMatcher`
- `WikiMergeEngine`
- `WikiDependencyService`
- `WikiRebuildService`
- `WikiFeedbackService`
- `WikiValidator`
- Wiki 查询适配器

除最外层 compatibility adapter 外，新模块不得直接依赖：

```python
Config.get(...)
Database
get_active_container()
```

测试必须能注入：

- fake repository；
- fake projection；
- fake embedding；
- fake LLM；
- deterministic clock；
- deterministic ID generator。

---

# 4. Phase 3.5：纠偏门禁

## C0：现状审计与冻结

### 目标

在继续修改代码前，准确记录当前 Wiki V2 已完成范围和公共契约。

### 任务

- [ ] 创建：

```text
docs/superpowers/reviews/2026-07-08-canonical-wiki-v2-current-state.md
```

- [ ] 记录当前已有模块、公共类、配置、表、Schema 和调用关系；
- [ ] 标记 Phase 0、1、2、3 中各 Task 的实际状态：
  - completed
  - partially completed
  - not started
  - deviated
- [ ] 列出所有 Canonical 直接写调用；
- [ ] 列出所有 Projection 直接写调用；
- [ ] 列出所有读取 Wiki 的服务和入口；
- [ ] 列出所有全局 Config、Database 单例、active container 使用点；
- [ ] 列出当前 feature flag 和 legacy fallback；
- [ ] 记录当前完整测试、ruff、mypy、retrieval eval、wiki eval 结果；
- [ ] 不根据旧计划中的 checkbox 推断完成状态，必须检查真实代码和测试。

### 输出格式

审计文档至少包含：

```text
1. 当前模块图
2. 写路径图
3. 读路径图
4. 数据对象图
5. 配置与门控
6. 已知直接写 allowlist
7. 当前测试与指标
8. 与原 Spec 的偏差
9. Phase 4 前阻断项
```

### 验收

- 审计结果可以从真实代码复核；
- 不存在“计划写了已完成，但代码未实现”的任务；
- 不存在未记录的 Canonical 写入口。

### Commit

```text
docs(wiki-v2): audit current canonical implementation
```

---

## C1：冻结 Claim 语义契约

### 目标

在继续扩展 Matcher 和 Merge Engine 前，固定各种 merge action 的严格定义。

### 新增文档

```text
docs/architecture/wiki-v2-claim-merge-contract.md
```

### 必须定义的决策矩阵

#### `duplicate`

只有同时满足以下条件才能判定：

- Claim 语义等价；
- 作用域等价；
- 时间范围等价；
- Evidence 唯一键也已经存在。

重复 Evidence 不得再次写入。

#### `supports`

满足：

- 新旧 Claim 语义等价；
- 作用域和时间范围兼容；
- 新 Evidence 与已有 Evidence 不同；
- 新来源确实提供支持性证据。

结果：

- 不创建新 Claim；
- 为现有 Claim 增加 Evidence；
- 不改变已有 Evidence stance。

#### `refines`

满足：

- subject 和核心 predicate 一致；
- 新 Claim 不否定旧 Claim；
- 新 Claim 增加限定条件、精度、范围、单位或上下文；
- 旧 Claim 在新 Claim 的限定范围内仍然成立。

以下情况不得自动判定为 `refines`：

- 数值直接不同；
- 产品型号不同；
- 地区不同；
- 时间范围不同但未明确关联；
- 单位无法安全转换；
- 否定词不同。

#### `contradicts`

只有同时满足以下条件才能自动判定：

- subject 相同；
- predicate 相同或明确互斥；
- 作用域重叠；
- 有效时间重叠；
- object、极性、数值或关系不能同时成立；
- 新证据确实表达反驳，而不是不同场景的补充。

不同版本、不同时间、不同区域、不同产品型号和不同适用条件，默认不得判定为矛盾。

#### `supersedes`

必须存在明确的替代信号，例如：

- 新标准明确废止旧标准；
- 新版本声明替代旧版本；
- 有明确生效时间；
- 来源明确写明“替代”“废止”“自某日起执行”。

不得仅因为以下原因自动 supersede：

- 新来源发布时间更晚；
- 新数字更大；
- LLM 认为新说法更合理；
- 两条事实相似但措辞不同。

#### `new`

现有 Claim 中没有足够接近且作用域兼容的对象。

#### `unresolved`

出现以下任一情况必须进入 unresolved：

- 缺失时间或作用域；
- 单位不清；
- subject_refs 无法可靠识别；
- 多个候选 Claim 分数接近；
- 可能是 contradict，也可能是不同场景；
- Evidence 质量不足；
- LLM 输出与规则层不一致；
- 低于自动动作阈值。

### Reason Code

`ClaimMatchDecision.reasons` 不得仅返回自然语言，必须增加稳定 reason code，例如：

```text
EXACT_NORMALIZED_MATCH
SAME_SEMANTICS_NEW_EVIDENCE
SCOPE_MISMATCH
TIME_RANGE_MISMATCH
NUMERIC_CONFLICT
UNIT_INCOMPATIBLE
EXPLICIT_REPLACEMENT
AMBIGUOUS_CANDIDATES
INSUFFICIENT_EVIDENCE
LOW_CONFIDENCE
```

### 验收

- 所有 action 都有明确正例和反例；
- Matcher 与 Merge Engine 共用同一份枚举和契约；
- 不允许不同模块各自实现 normalize 或 action 语义；
- 当前测试全部迁移到统一 reason code。

### Commit

```text
docs(wiki-v2): freeze claim merge semantics
```

---

## C2：建立 Claim 专项黄金评测集

### 目标

将 Claim 语义准确率变成 Phase 4 的强制门禁。

### 新增目录

```text
evals/wiki_v2/
├── claim_extraction.jsonl
├── claim_matching.jsonl
├── claim_merge.jsonl
├── source_update.jsonl
├── source_delete.jsonl
└── README.md
```

### 数据集要求

不得只使用简单英文或无歧义例子。

必须覆盖中文知识库高风险场景：

- 同一产品不同型号；
- 同一指标不同版本；
- 同一速度不同上下行；
- Mbps 与 Gbps；
- 理论值与实测值；
- 全国规则与省级规则；
- 当前政策与历史政策；
- 产品宣传与技术规范；
- “最高可达”与“保证达到”；
- 含“可能”“建议”“必须”“禁止”的不同强度陈述；
- 同一 Claim 来自多个来源；
- 一个来源同时包含支持和限制条件；
- 来源更新但相关 block 未变化；
- 来源删除后仍有其他有效 Evidence；
- 同义表达；
- 否定表达；
- OCR 或解析产生的轻微噪声；
- 表格中的数值和正文中的数值；
- 相同文本但不同作用域；
- 不同文本但语义等价。

### 确定性测试

PR 和普通 CI 使用：

- 固定结构化 LLM 响应；
- fake embedding；
- 固定 clock；
- 固定 UUID；
- 固定候选顺序。

### 真实模型评测

增加非阻断的定时评测：

```text
evals/run_wiki_v2_semantic_eval.py
```

真实模型评测应输出：

- extraction precision；
- extraction recall；
- evidence validity；
- action confusion matrix；
- supports precision；
- refines precision；
- contradicts precision；
- supersedes precision；
- unresolved rate；
- false merge rate；
- LLM 调用次数；
- 每文档处理时间。

### Phase 4 最低门槛

| 指标 | 最低要求 |
|---|---:|
| Evidence 包含有效 knowledge_id | 100% |
| 可定位 block 时 block_id 完整率 | 100% |
| Evidence source_revision 完整率 | 100% |
| `supports` precision | ≥ 95% |
| `refines` precision | ≥ 95% |
| `contradicts` precision | ≥ 98% |
| `supersedes` precision | ≥ 98% |
| duplicate Evidence 写入次数 | 0 |
| 错误自动合并率 | ≤ 1% |
| 无法确定时进入 unresolved | 100% |
| Published Page 引用非法 Claim | 0 |

对 `contradicts` 和 `supersedes`，宁可降低召回，也不得降低精确率。

### Commit

```text
test(wiki-v2): add claim semantic golden evaluation
```

---

## C3：多对象原子性与崩溃恢复

### 目标

证明 Canonical 写入不会在中途中断后留下半页、半 Claim 或错误引用。

### 必须定义的事务边界

一次 Merge 可能同时修改：

- 旧 Claim；
- 新 Claim；
- Evidence；
- Page claim_ids；
- Page 正文；
- Page Registry；
- Redirect；
- Operation Log；
- Projection Outbox。

这些对象不能简单依赖“逐个文件原子写”。

必须采用：

```text
prepare staging
→ validate all staged objects
→ write transaction manifest
→ publish canonical objects
→ write commit marker
→ append outbox
→ cleanup staging
```

### 恢复规则

启动或下一次写入前必须扫描未完成事务：

- 无 commit marker：回滚或重新执行；
- Canonical 已发布但 outbox 未写：补写 outbox；
- Registry 与 Page 不一致：根据 transaction manifest 修复；
- Claim 已写但 Page 未引用：回滚 Claim 或完成 Page 发布；
- Page 引用了不存在 Claim：阻止发布并恢复；
- supersede 只修改了旧 Claim：必须回滚，不能留下无替代对象的 `superseded` Claim。

### 故障注入测试

至少在以下节点模拟异常：

1. 写完第一个 Claim 后；
2. 写完全部 Claim、未写 Page 前；
3. 写完 Page、未写 Registry 前；
4. 写完 Registry、未写 commit marker 前；
5. 写完 Canonical、未写 Outbox 前；
6. Outbox 写入一半；
7. Projection 消费中断；
8. 重复消费同一个事件；
9. Windows 文件被短暂占用；
10. revision 冲突；
11. 同一 Claim 并发更新；
12. supersede 两对象校验其中一个失败。

### 验收

- 任意故障注入后，系统可自动恢复到事务前或事务后的一致状态；
- 不存在 Page 引用不存在 Claim；
- 不存在旧 Claim 已 superseded 而新 Claim 未创建；
- Outbox 重放不会产生重复行；
- 全量 Projection 重建后 parity 为 100%。

### Commit

```text
test(wiki-v2): harden canonical transaction recovery
```

---

## C4：统一 Wiki 读取端口

### 目标

不要求在本阶段重构全部 RAG，但禁止 Wiki V2 再形成第三套读取逻辑。

### 新增统一端口

建议新增：

```python
class WikiQueryService:
    def search_pages(self, query: str, limit: int = 10) -> list[WikiCandidate]: ...
    def get_page(self, page_id: str) -> WikiPage | None: ...
    def get_claim(self, claim_id: str) -> Claim | None: ...
    def health(self) -> WikiReadHealth: ...
```

内部读取顺序：

```text
Healthy V2 Projection
    ↓ failure
Canonical Filesystem
    ↓ unavailable
Legacy SQLite Compatibility Read
```

### 约束

以下模块不得各自重新实现 projection fallback：

- `SearchService`
- `RagPipeline`
- `WikiPageLocator`
- MCP handlers
- API routes
- GUI workers

它们只能调用统一 Wiki 读取端口。

本任务不要求立即统一整个 `SearchService` 和 `RagPipeline`，但必须做到：

- 两个入口使用同一个 Wiki 查询服务；
- 相同 query 和配置下，Wiki 候选集合一致；
- page_id、revision、status、claim_ids 和 warning 语义一致；
- fallback 顺序一致；
- projection drift warning 一致。

### 契约测试

增加测试：

```text
MCP search
MCP ask
SearchService
RagPipeline
WikiPageLocator
```

对同一 fixture 的 Wiki 查询应返回相同的：

- page_id；
- title；
- status；
- claim_ids；
- source_ids；
- canonical revision；
- fallback warning。

### Commit

```text
refactor(wiki-v2): centralize wiki query fallback
```

---

## C5：完善配置状态机

### 目标

避免只使用一个 `enabled: true/false` 就完成高风险切换。

### 建议配置

```yaml
wiki:
  canonical_v2:
    mode: "off"  # off | shadow | canary | primary
    projection_fallback_to_filesystem: true
    compatibility_read_legacy_sqlite: true
    compatibility_write_legacy_sqlite: false
```

### 模式定义

#### `off`

- 不执行 V2 主流程；
- legacy 行为完全不变；
- 允许显式运行 validate 和 migration dry-run。

#### `shadow`

- V2 Claim 抽取、匹配、合并和 Projection 在隔离区域运行；
- 不影响用户可见的 Canonical Page；
- 不参与正式检索；
- 输出与 legacy 结果的差异报告；
- 用于真实数据验证语义准确率。

#### `canary`

- 只有显式指定的知识目录、knowledge_id 或操作使用 V2；
- Canonical V2 为这些对象的主写路径；
- 查询优先 V2，允许 legacy read fallback；
- 所有自动 publish 关闭；
- 所有 contradicts、supersedes 进入 review。

#### `primary`

- Canonical V2 成为主写路径；
- Projection 成为主查询层；
- legacy 只读回退继续保留至少一个 minor 版本；
- 仍然不得自动裁决高风险冲突。

### 验收

- 每个模式都有独立集成测试；
- `off` 模式行为与当前 legacy 基线完全一致；
- `shadow` 模式不得修改正式 Wiki；
- `canary` 模式只影响 allowlist 中的对象；
- 模式切换不要求修改数据库结构；
- 从 canary 退回 off 后，legacy 仍能工作。

### Commit

```text
feat(wiki-v2): add staged canonical cutover modes
```

---

## C6：收紧新服务依赖边界

### 目标

防止 Wiki V2 服务继续依赖隐式全局状态。

### 任务

检查以下模块：

```text
wiki_repository.py
wiki_projection.py
wiki_claim_extractor.py
wiki_claim_matcher.py
wiki_merge_engine.py
wiki_page_locator.py
wiki_query_service.py
```

对每个模块：

- [ ] 构造函数显式接收依赖；
- [ ] 不在方法内部创建生产服务；
- [ ] 不直接读取全局 Config；
- [ ] 不直接获取 active container；
- [ ] 不直接使用 Database 单例；
- [ ] clock 和 ID generator 可注入；
- [ ] 文件路径通过配置对象传入；
- [ ] 所有副作用可以在测试中替换。

允许 compatibility adapter 在最外层解析全局配置，然后注入服务。

### 架构守卫

新增 AST 或 import boundary 测试，禁止上述模块导入：

```text
src.core.container.get_active_container
src.utils.config.Config
src.services.db.Database
```

若确有必要，必须在代码注释和 allowlist 中说明原因及移除阶段。

### Commit

```text
refactor(wiki-v2): enforce explicit service dependencies
```

---

# 5. Phase 3.5 总验收门禁

只有全部满足，Agent 才能进入 Phase 4：

- [ ] 当前状态审计完成；
- [ ] Claim action 语义契约完成；
- [ ] Claim 黄金评测集完成；
- [ ] 专项评测达到最低门槛；
- [ ] 多对象事务故障注入全部通过；
- [ ] Projection 可重复消费、可全量重建；
- [ ] Page/Claim/Registry parity 为 100%；
- [ ] 统一 Wiki 读取端口完成；
- [ ] `off/shadow/canary/primary` 状态机完成；
- [ ] `off` 模式 legacy 行为零变化；
- [ ] 新 Wiki V2 服务依赖边界通过；
- [ ] Ruff 0 error；
- [ ] mypy 0 error；
- [ ] 全量 pytest 通过；
- [ ] retrieval eval 不低于纠偏前基线；
- [ ] wiki eval 不低于纠偏前基线；
- [ ] 所有高风险自动动作都有 review fallback；
- [ ] 更新 Spec、Plan、PROGRESS 或当前状态文档。

如果任何一项失败，不得通过降低阈值、扩大 allowlist 或跳过测试继续 Phase 4。

---

# 6. 纠偏后的续建顺序

Phase 3.5 通过后，原 Phase 4—6 调整为以下顺序。

## Phase 4A：Shadow 主工作流接入

### 目标

将 Claim 流程接入真实 ingest，但不影响正式 Canonical 内容。

流程：

```text
raw indexing success
→ shadow claim extraction
→ shadow matching
→ shadow merge
→ shadow validation
→ shadow projection
→ comparison report
```

### 要求

- raw 索引不受影响；
- legacy Wiki 继续正常生成；
- V2 输出写入隔离目录或 staging；
- 生成 legacy 与 V2 的差异报告；
- 统计：
  - 新 Claim 数；
  - 自动合并数；
  - unresolved 数；
  - 冲突数；
  - Evidence 缺失数；
  - Page diff；
  - LLM 成本；
  - 处理延迟。

### Shadow 退出条件

至少使用一组真实个人知识库数据运行，完成抽样人工核验。

---

## Phase 4B：Canary Canonical 切换

### 目标

只对显式选择的目录或知识项启用 Canonical V2。

### 约束

- 禁止全库直接切换；
- canary 对象必须可列出；
- canary 写入后继续允许 legacy read fallback；
- `contradicts`、`supersedes` 和低置信度 `refines` 强制 review；
- 每次 canary 操作生成可回滚 transaction ID；
- canary 期间关闭自动发布；
- canary 数据必须通过 parity 检查。

### Canary 退出条件

- 连续多轮 ingest 无半写；
- 无错误 supersede；
- 无错误 contradict；
- Projection drift 可自动修复；
- rollback 实测成功；
- 核心 MCP 检索无回归。

---

## Phase 4C：Primary 写路径切换

完成以下职责调整：

### `KnowledgeWorkflowService`

只负责流程编排：

```text
source page
→ extractor
→ matcher
→ merge engine
→ page composer
→ repository transaction
→ projection outbox
```

不得直接写 Canonical 文件。

### `WikiWriteService`

改为 Canonical 写入口的兼容门面：

- 保留旧返回字段一个 minor 版本；
- 新增 `page_id`、`canonical_saved`、`projection_pending`；
- 不再执行无一致性保证的双写；
- legacy 写入只能作为明确配置的兼容行为。

### `WikiCompiler`

降级为 compatibility adapter：

- 旧 API 保留；
- 委托新工作流；
- 输出 deprecation warning；
- 不再直接写独立 SQLite Wiki 内容。

### `WikiEntityUpdater`

改为页面组织建议服务：

- 不直接写文件；
- 不分配最终 Claim ID；
- 不决定最终 Claim 状态；
- 不覆盖整页；
- 输出建议，由 Merge Engine 和 Page Composer 应用。

---

## Phase 5：依赖图与失效传播

只有 Primary 写路径稳定后才能开始。

### 实施顺序

1. 先建立只读依赖图；
2. 实现 `get_impacted_by_source`；
3. 实现 `get_impacted_by_claim`；
4. 输出 rebuild dry-run；
5. 增加环检测和最大深度；
6. 实现单 source 手动 rebuild；
7. 实现 canary 自动 rebuild；
8. 最后才允许自动来源更新传播。

### 安全规则

- 来源变更不等于全部 Claim 失效；
- 先比较 block hash；
- 未变化 Evidence 保留；
- 变化 Evidence 标记 stale；
- 来源删除不物理删除 Claim；
- 仍有其他 supports Evidence 时保持 active；
- 无有效 Evidence 时改 unsupported；
- 受影响 Published Page 至少进入 review；
- rebuild 必须经过 staging、validation 和 diff；
- 默认禁止自动发布高风险变更。

---

## Phase 6：迁移、反馈与正式评测

### 迁移顺序

```text
dry-run
→ backup
→ isolated canonical generation
→ claim review report
→ validation
→ projection rebuild
→ parity
→ canary
→ primary suggestion
```

不得在 migration apply 成功后自动强制启用 primary。

### 用户反馈

反馈必须作用于 Claim 层：

- confirm；
- mark incorrect；
- provide correction；
- retract；
- resolve dispute。

反馈不得修改 Raw Source。

### 最终正式评测

最终发布报告至少包含：

- Retrieval 指标；
- Wiki 编译指标；
- Claim extraction 指标；
- Claim action confusion matrix；
- 错误自动合并率；
- Source update 正确率；
- Source delete 正确率；
- Projection parity；
- Crash recovery；
- Migration rollback；
- Windows smoke test；
- 性能与 LLM 调用成本。

---

# 7. 暂缓事项

以下内容不属于本轮 Wiki V2 主线，在 Canonical V2 稳定前不得展开：

- 自动裁决所有 disputed Claim；
- 多 Agent 同时编辑 Canonical Wiki；
- 通用本体系统；
- 复杂知识图谱推理；
- 新的 Page Type；
- GUI 大规模重写；
- RBAC 重构；
- 云端协作；
- 向量数据库迁移；
- 让 LLM 自动修改 Raw Source；
- 无审核的 Wiki 自动发布；
- 根据 Claim confidence 自动判断事实真伪；
- 将 Canonical Wiki 宣称为现实世界绝对真相。

---

# 8. Agent 执行纪律

每个 Task 必须：

1. 先读取当前 Spec、Plan、最新提交和真实代码；
2. 使用影响分析确认调用方；
3. 先写失败测试；
4. 实现最小改动；
5. 运行相关测试；
6. 运行架构守卫；
7. 运行 ruff 和 mypy；
8. 独立 commit；
9. 进行规格一致性 Review；
10. 进行代码质量 Review；
11. 修复 Review 问题；
12. 更新状态文档；
13. 不把“测试通过”写成“语义正确”，必须同时报告评测指标。

禁止：

- 在一个 commit 中跨多个纠偏任务；
- 顺手重构无关模块；
- 为通过测试降低断言；
- 为通过架构守卫扩大永久 allowlist；
- 把 warning 全部吞掉；
- 使用大范围 `except Exception: pass`；
- 在没有 golden case 的情况下增加语义规则；
- 未经验证将 `unresolved` 改为自动动作；
- 在失败时继续执行后续 Phase。

---

# 9. 建议 Commit 顺序

```text
docs(wiki-v2): audit current canonical implementation
docs(wiki-v2): freeze claim merge semantics
test(wiki-v2): add claim semantic golden evaluation
test(wiki-v2): harden canonical transaction recovery
refactor(wiki-v2): centralize wiki query fallback
feat(wiki-v2): add staged canonical cutover modes
refactor(wiki-v2): enforce explicit service dependencies
docs(wiki-v2): pass phase 3.5 correction gate
feat(wiki-v2): integrate shadow canonical workflow
feat(wiki-v2): enable canary canonical workflow
refactor(wiki-v2): switch primary canonical write path
feat(wiki-v2): add dependency impact planning
feat(wiki-v2): add staged source rebuild
feat(wiki-v2): add migration and feedback workflow
test(wiki-v2): complete knowledge evolution evaluation
```

---

# 10. 最终完成定义

Canonical Wiki V2 只有在满足以下条件时，才能被视为正式完成：

1. Raw Source、Canonical Store 和 Projection 三层职责清晰；
2. 所有 Canonical 写入经过 WikiRepository；
3. SQLite 可以完全删除并重建；
4. Claim 都有可验证 Evidence；
5. Claim action 有明确契约和专项评测；
6. 错误自动合并率处于可接受范围；
7. 来源更新和删除不会粗暴覆盖整页；
8. 任意中断不会留下半写知识；
9. V2 可以 shadow、canary、primary 分阶段启用；
10. legacy 模式仍可回退；
11. MCP、API 和 RAG 使用统一 Wiki 读取端口；
12. 核心检索质量不低于切换前基线；
13. Windows 和 Linux 均有基础验证；
14. migration dry-run、apply 和 rollback 均经过真实测试；
15. 高风险冲突仍由人类或明确审批流程裁决。

最终目标不是构建功能最多的 Wiki，而是构建一个：

> **证据可追溯、变更可审阅、写入可恢复、投影可重建、语义合并足够保守，并能稳定服务 AI Agent 的本地知识演进系统。**
