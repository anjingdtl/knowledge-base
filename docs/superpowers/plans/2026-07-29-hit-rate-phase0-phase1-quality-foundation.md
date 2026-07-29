# MCP 命中率 Phase 0–1：质量地基重建实施方案

> 状态：待执行  
> 编制日期：2026-07-29  
> 适用基线：`master@19195d4`（执行前必须重新记录实际 HEAD）  
> 范围：Phase 0 工程与评分可信度恢复；Phase 1 Golden Set V2 与人工审核治理  
> 后续阶段：本方案完成后，才允许进入检索/回答核心链路重构

## 1. 目标与决策

本方案不以“把当前分数调到 90%”为目标，而是先保证：

1. 工程基线可重复、可验证，当前 HEAD 的架构、版本和测试门禁全部恢复；
2. 评分器能够真实识别误答、错误拒答、引用失效和无答案误答；
3. Golden Set 有结构化事实、证据定位、双人审核、争议裁决和冻结门禁；
4. 开发集、验证集和盲测集职责分离，停止在同一批高风险题上循环调参；
5. 原始评测运行产物不再无差别进入 Git，不再重复提交内部全文和敏感字段。

当前放行结论保持 **NO-GO**。Phase 0–1 期间：

- 不降低 Ask Fact、E2E、Citation 等放行阈值；
- 不通过删除失败题、弱化 required facts 或修改知识原文来提高分数；
- 不进行检索排序、回答抽取、证据门禁的质量调参；
- 不发布新版本，不声称达到生产试点门槛。

## 2. 已确认基线

| 项目 | 当前结果 | 处理阶段 |
|---|---:|---|
| 高风险可回答样本 | 15 | Phase 0 保留为回归集 |
| Top-1 | 12/15，80.00% | Phase 2+ 优化 |
| Recall@5 | 13/15，86.67% | Phase 2+ 优化 |
| Ask Fact | 7/15，46.67% | Phase 2+ 优化 |
| Citation | 13/15，86.67% | Phase 2+ 优化 |
| E2E | 6/15，40.00% | Phase 2+ 优化 |
| KB-032 无答案样本 | 实际返回无关内容，但评分为非误答 | Phase 0 修评分器 |
| 聚焦架构/V7 测试 | 36 passed / 6 failed | Phase 0 恢复 |
| 版本元数据 | `src=1.11.1`，其余仍为 `1.11.0` | Phase 0 统一 |
| 架构债门禁 | `PassageStore` 有白名单外 `Database._instance` | Phase 0 修复 |
| 正式 reranker | V7 探测超时，当前依赖 circuit breaker | Phase 0 固化评测口径 |
| Golden Set | 37 题，无 passage 证据定位和双人审核元数据 | Phase 1 升级 |
| 已跟踪 artifacts | 911 个，约 268 MB | Phase 0 建立新保留策略 |

以上数字仅作为方案输入。执行 Agent 必须在改动前重新运行并记录，不得直接复制为最终结果。

## 3. 不变量与禁止事项

### 3.1 数据与知识库

- `data/kb.db` 只读；执行前后记录 SHA256、文件大小和修改时间。
- 禁止为通过评测修改正式知识内容、删除旧版本文档或重写事实。
- 允许修复评测数据的标签、歧义和证据定位，但必须产生版本、原因和审核记录。
- 当前 `evals/golden_set_hit_rate.json` 作为 legacy regression 数据保留，不原地改写。

### 3.2 评测

- 评分器修复后分数下降属于预期结果，不得回退正确的评分逻辑。
- 无答案样本不得因为回答没有逐字包含 `forbidden_facts` 占位短语而自动通过。
- `Hallucination Rate=0` 只能在指标确实覆盖所有待判断断言时声明；仅基于 forbidden substring 时必须标记为 proxy。
- 正式评测只能读取 `frozen/` 数据；`candidates/` 和 `reviewed/` 不得进入正式分母。
- 任何 Agent 不得填入虚构的 reviewer、reviewed_at 或 adjudicator。

### 3.3 工程

- 不得通过扩大 `Database._instance` 白名单让架构门禁变绿。
- 不得删除、跳过或 xfail 当前失败的版本一致性和架构门禁测试。
- Phase 0–1 不改 MCP 对外工具名、参数和响应 Envelope。
- 不做 Git 历史重写；历史 artifacts 的清理另行审批。
- 不提交、推送或创建 PR，除非任务发起人明确授权。

## 4. 目标数据流

```text
legacy Golden V1（只读保留）
              │
              ▼
候选迁移/新候选生成 ──> candidates/
              │
              ▼
第一审核 + 第二审核 ──> reviewed/
              │
       有争议时独立裁决
              │
              ▼
Schema + Corpus + Review Gate
              │
              ▼
           frozen/
              │
              ▼
正式 Harness + Scorer V2 ──> 脱敏摘要/报告
```

## 5. 计划文件地图

下列路径是建议落点。执行 Agent 可在不破坏职责边界的前提下微调，但必须在交付报告中说明。

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/services/passage_store.py` | 修改 | 移除白名单外单例私有访问 |
| `src/version.py` | 核对 | 版本权威源 |
| `README.md` / `README_zh.md` | 修改 | 版本徽章一致性 |
| `client/package.json` / `client/package-lock.json` | 修改 | 前端版本一致性 |
| `tests/architecture/test_version_consistency.py` | 修改 | 版本契约一致 |
| `evals/hit_rate_v2/__init__.py` | 新建 | V2 评测包 |
| `evals/hit_rate_v2/scoring.py` | 新建 | 唯一评分权威 |
| `evals/hit_rate_v2/models.py` | 新建 | Golden V2 与评分模型 |
| `evals/hit_rate_v2/validation.py` | 新建 | Schema、review、freeze 校验 |
| `schema/hit-rate-golden-v2.schema.json` | 新建 | Golden V2 JSON Schema |
| `scripts/hit_rate_score.py` | 修改 | 薄 CLI，委托评分权威 |
| `scripts/hit_rate_finalize.py` | 修改 | 薄汇总/报告层 |
| `scripts/hit_rate_test_harness.py` | 修改 | 冻结数据与 manifest 校验 |
| `scripts/migrate_hit_rate_golden_v2.py` | 新建 | V1 → V2 candidates |
| `scripts/review_hit_rate_ground_truth.py` | 新建或复用 | 双人审核/裁决 CLI |
| `scripts/freeze_hit_rate_ground_truth.py` | 新建或复用 | 严格冻结门禁 |
| `scripts/hit_rate_artifact_sanitize.py` | 新建 | 生成可提交脱敏摘要 |
| `tests/eval/test_hit_rate_v2_*.py` | 新建 | 评分、Schema、冻结和隔离测试 |
| `tests/eval/datasets/hit_rate/candidates/` | 新建 | 规则辅助候选，不计分 |
| `tests/eval/datasets/hit_rate/reviewed/` | 新建 | 已人工审核，尚未冻结 |
| `tests/eval/datasets/hit_rate/frozen/` | 新建 | 正式评测唯一入口 |
| `docs/evaluation/mcp-hit-rate-phase0-phase1-report.md` | 新建 | 最终执行报告 |
| `PROGRESS.md` / `docs/README.md` | 修改 | 更新权威状态和入口 |

## 6. Phase 0：恢复可信工程与评分基线

### Task 0.1：冻结执行输入并生成只读基线

**目标：** 记录执行前真实状态，防止使用过期报告。

- [ ] 记录 `git status --short`、branch、HEAD、Python、依赖锁摘要。
- [ ] 记录 `data/kb.db` SHA256、大小、mtime；全程不得写库。
- [ ] 运行当前工程门禁并保存精简结果。
- [ ] 读取 attempt 20 的原始结果，记录当前 scorer hash 和 Golden hash。
- [ ] 基线报告只保留指标、失败 case ID、reason code、hash，不复制全文证据。

建议命令：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
python --version
Get-FileHash data/kb.db -Algorithm SHA256
python tools/report_closure_debt.py --json
pytest tests/architecture tests/answering/test_hit_rate_v7_anti_overfit.py tests/test_hit_rate_v7_harness_integrity.py -q
```

验收：

- 基线结果写入 Phase 0–1 报告；
- 数据库 hash 已记录；
- 没有修改正式数据库；
- 不把命令输出中的密钥、内部全文或个人信息写入报告。

### Task 0.2：恢复架构债与版本一致性门禁

#### 0.2.1 PassageStore

**要求：**

- [ ] 先补充 `PassageStore` 注入 DB 和兼容 class facade 的测试。
- [ ] 删除 `src/services/passage_store.py` 对 `Database._instance` 的直接读取。
- [ ] 优先使用已注入的 DB；兼容路径只调用 `Database` 的公开连接 API。
- [ ] 不修改 `tools/report_closure_debt.py` 白名单来规避失败。
- [ ] `python tools/report_closure_debt.py --strict` 必须返回 0。

#### 0.2.2 版本

当前 `src/version.py` 是 `1.11.1`。除非任务发起人另有明确决定，执行时将其视为权威版本，并同步：

- `README.md`
- `README_zh.md`
- `client/package.json`
- `client/package-lock.json` 顶层及 root package
- `tests/architecture/test_version_consistency.py`
- `PROGRESS.md` 当前状态

不得只改测试期望而遗漏产品元数据。

验收命令：

```powershell
python tools/report_closure_debt.py --strict
pytest tests/architecture/test_closure_debt_baseline.py tests/architecture/test_closure_debt_strict_mode.py -q
pytest tests/architecture/test_version_consistency.py -q
cd client
npm run build
cd ..
```

### Task 0.3：建立唯一评分权威并修复无答案漏判

**问题：**

`scripts/hit_rate_score.py` 与 `scripts/hit_rate_finalize.py` 重复实现评分。当前 no-answer 逻辑只有回答字面包含 `forbidden_facts` 时才判 false positive。像 KB-032 这种返回无关确定性内容的情况会被漏判。

**要求：**

- [ ] 新建 `evals/hit_rate_v2/scoring.py`，承载唯一的纯评分逻辑。
- [ ] 两个现有脚本保留 CLI 和兼容输出，但不得再复制核心评分函数。
- [ ] 输出增加 `metric_contract_version: "2.0"`。
- [ ] 保留旧字段时必须注明其兼容含义；新增字段使用稳定 reason code。

#### Answerable 判定

至少拆分：

- `retrieval_top1_hit`
- `retrieval_recall_at_5`
- `answer_fact_coverage`
- `answer_forbidden_assertion`
- `citation_lineage_valid`
- `answer_supported`
- `e2e_pass`

检索命中不能代替回答事实正确；回答碰巧含 required facts 不能掩盖检索失败。

#### No-answer 判定

正式通过至少同时满足：

1. `expected_no_answer=true`；
2. `answer_mode=no_answer`，或回答为空/只包含明确拒答语；
3. 无实质性确定断言；
4. sources 和 raw evidence 不得被作为确定答案引用；
5. 不包含已定义 forbidden assertion。

以下任一情况必须判 `false_positive=true`：

- `answer_mode` 是 `raw_only`、`verified` 等回答模式且给出实质性答案；
- 返回非空来源并以其支持确定性答案；
- 给出地址、金额、部门、品牌、进展等具体答案，即使没有逐字包含 Golden 中的占位短语；
- 回答先说“未找到”，随后又给出确定性事实。

建议 reason code：

- `unexpected_answer_mode`
- `substantive_answer_on_no_answer`
- `sources_present_on_no_answer`
- `forbidden_assertion`
- `refusal_with_assertion`

#### Hallucination 指标

- [ ] 将仅基于 `forbidden_facts` 的旧指标显式命名为 `Forbidden Assertion Rate`。
- [ ] `Hallucination Rate` 若无法验证所有回答断言，应输出 `null/N/A` 和 `not_fully_measurable`，不得默认 0。
- [ ] Phase 1 有结构化事实与证据后，再计算完整 `Unsupported Assertion Rate`。

必须新增的测试：

- KB-032 形态：无关非空回答、`raw_only`、有 sources → false positive；
- 虚构地址但不含“具体办公地址”字样 → false positive；
- 空回答、`no_answer`、空 sources → pass；
- 明确拒答且无后续断言 → pass；
- “未找到，但地址是……” → false positive；
- answerable 检索失败但答案碰巧包含 required fact → E2E fail；
- required fact 正确但 passage lineage 无效 → Citation/E2E fail。

建议命令：

```powershell
pytest tests/eval/test_hit_rate_v2_scoring.py tests/eval/test_hit_rate_v2_no_answer.py -q
pytest tests/mcp/test_hit_rate_regressions.py tests/mcp/test_hit_rate_v3_regressions.py -q
```

### Task 0.4：固化 reranker 与运行模式口径

**目标：** 不再把 provider fallback 当作 normal rerank，也不让远程 provider 故障污染算法结论。

- [ ] Harness 必须显式接收 `--rerank-profile`：
  - `deterministic-baseline`
  - `provider-enhanced`
- [ ] manifest 记录 requested/effective profile、provider availability、timeout 和 fallback reason。
- [ ] `provider-enhanced` 不可用时，该轨结果为 blocked，不得自动改名为 normal。
- [ ] `deterministic-baseline` 可独立运行，但不能冒充 provider 等价性证明。
- [ ] 同池 A/B 必须共用 capture fingerprint。
- [ ] provider 探测不得输出 API key、Authorization header 或完整响应体。

Phase 0 不要求修复外部 provider，但必须让状态和指标诚实可分。

### Task 0.5：评测产物脱敏与保留策略

**目标：** 原始 evidence 只留本地，Git 仅保留可复核的最小摘要。

- [ ] 新增 sanitizer，默认删除或哈希：
  - `text`、`content`、`raw_evidence_used` 全文；
  - prompt、Authorization、API key、Cookie；
  - 手机号、邮箱、个人联系人；
  - 绝对本地路径。
- [ ] 可提交摘要允许保留：
  - case ID、指标、reason code；
  - knowledge/passage ID；
  - source/scorer/golden/config/corpus hash；
  - 延迟和运行模式；
  - 不含全文的 fact coverage 结果。
- [ ] 为新的本地原始运行目录增加 `.gitignore`。
- [ ] 增加 sanitizer 测试，输入包含手机号/邮箱/密钥形态时输出不得保留原值。
- [ ] 不在本任务中执行 Git 历史重写或大批量删除既有 artifacts；只输出待治理清单。

建议目录：

```text
.local/eval-runs/              # 原始运行，Git ignored
artifacts/eval-summaries/      # 脱敏、可提交
```

### Task 0.6：只重评分，不调模型

- [ ] 使用新 scorer 对 attempt 20 原始结果做离线重评分。
- [ ] 输出到新目录，禁止覆盖原始 `final_scored.json`。
- [ ] 报告旧指标与新指标差异，尤其是 no-answer false positive。
- [ ] 不因分数下降修改检索、回答、阈值或 Golden。
- [ ] 更新权威结论为 NO-GO，并说明 Phase 1 尚未完成。

Phase 0 完成条件：

- 架构债 strict gate 通过；
- 版本一致性全部通过；
- scorer 只有一个核心实现；
- KB-032 形态被正确判为 false positive；
- reranker requested/effective 状态不混淆；
- 新原始 artifacts 默认不进入 Git；
- 全量测试与静态检查无新增失败。

## 7. Phase 1：Golden Set V2 与双人审核治理

### Task 1.1：定义 Golden V2 Schema

新 Schema 至少包含：

```json
{
  "case_id": "KB-XXX",
  "schema_version": "2.0",
  "split": "development|validation|holdout",
  "category": "string",
  "risk_level": "P0|P1|P2|P3",
  "query": "string",
  "answerability": "answerable|no_answer|clarification_required",
  "intent": "fact|numeric|policy|scope|version|cross_document|other",
  "expected_action": "answer|refuse|clarify",
  "expected_sources": [],
  "required_fact_groups": [],
  "forbidden_assertions": [],
  "acceptable_variants": [],
  "ambiguity": {},
  "corpus_snapshot": {},
  "annotation_source": "candidate|human_reviewed",
  "review": {}
}
```

#### expected_sources

每个 source 至少包含：

- `knowledge_id`
- `passage_id`，无法定位时必须有明确 reason，且不得冻结为正式回答题；
- 可选 `block_id`
- `evidence_hash`
- `document_family_id`
- `version_year`
- `source_role`: `primary|supporting|acceptable|forbidden`

包含内部敏感原文的 exact excerpt 应保存在本地审核包；进入 Git 的 frozen 数据优先使用 passage ID + hash + 最小必要短句。

#### required_fact_groups

每个事实组至少支持：

- `fact_id`
- `subject`
- `predicate`
- `object_text`
- `value`
- `unit`
- `condition`
- `scope`
- `version`
- `match_policy`: `exact|normalized|numeric_unit|semantic_review`
- `acceptable_variants`
- `required`: boolean

跨文档问题必须能表达：

- 所有事实都必须出现；
- 任一版本即可；
- 每个版本分别覆盖；
- 条件与数值必须绑定，不能只做全文 substring。

#### ambiguity

至少包含：

- `status`: `clear|needs_clarification|disputed`
- `reason`
- `clarifying_question`
- `adjudication_notes`

KB-009、KB-024 等现有疑似歧义样本必须进入人工复核，Agent 不得自行将其改为通过。

### Task 1.2：V1 迁移为 candidates

- [ ] 新建确定性迁移脚本。
- [ ] 保留 37 个 case ID，不删除、不改名。
- [ ] 所有迁移行写入 `candidates/`，`annotation_source=candidate`。
- [ ] 不自动填写 reviewer。
- [ ] 当前 37 题已多轮参与调参，全部标为 `development` 或 `regression`，不得标为 holdout。
- [ ] 自动解析出的 passage、facts、版本仅作为 proposal。
- [ ] 迁移必须幂等；不得覆盖已有 reviewed/frozen 文件。
- [ ] 输出迁移摘要：成功、缺 passage、歧义、待复核、失败数量。

### Task 1.3：复用双人审核与冻结门禁

优先复用现有：

- `scripts/review_production_pilot_ground_truth.py`
- `scripts/freeze_production_pilot_datasets.py`
- `tests/eval/test_ground_truth_review_metadata.py`
- `tests/eval/test_ground_truth_corpus_snapshot.py`

允许抽取公共 review/freeze 工具，但必须保持 production-pilot 现有行为和测试不退化。

冻结规则：

- `annotation_source=human_reviewed`
- primary 与 secondary reviewer 非空且不同；
- 两次审核时间为合法 ISO8601；
- corpus snapshot 与冻结时数据库一致；
- 所有 expected source 已检查标题和正文；
- 所有 required fact 有 passage 证据；
- 有分歧时必须由第三个独立 adjudicator 裁决；
- `needs_adjudication`、`disputed`、证据缺失的行不得冻结。

Agent 能完成的是工具、候选与校验。若没有两名真实审核人，必须输出：

```text
Phase 1 engineering complete; formal dataset freeze blocked by human review.
```

不得伪造审核元数据使门禁通过。

### Task 1.4：开发/验证/盲测集隔离

职责：

| Split | 用途 | 是否允许调参 |
|---|---|---|
| development/regression | 当前 37 题、定向回归 | 允许 |
| validation | 阶段验收、有限查看失败 | 仅阶段末 |
| holdout | 最终放行 | 禁止日常查看答案 |

目标规模是后续正式放行要求，不得用自动候选数量冒充人工冻结数量：

- answerable frozen：至少 120；
- no-answer frozen：至少 20；
- P0/P1 高风险：全部单列；
- 版本冲突、表格、跨文档、口语、数值单位、易混淆、拒答均有覆盖。

Phase 1 至少应交付：

1. 当前 37 题的 V2 candidates；
2. 新增样本的分层 candidate 生成能力；
3. split 隔离测试；
4. 审核和冻结工具；
5. 真实 reviewed/frozen 数量报告。

若人工审核尚未完成，不得宣称达到目标规模。

### Task 1.5：结构化评分 V2

评分器应消费 Golden V2，而不是继续依赖自由文本数组：

- 文本事实：normalized/acceptable variants；
- 数值事实：value + unit + condition 绑定；
- 版本事实：document family + version year + effective status；
- 跨文档：按 required fact group coverage 计分；
- 引用：source passage 必须同时在回答证据和 Golden supporting evidence 中；
- clarification：问题本身歧义时，正确追问可通过，不强迫输出单一答案；
- no-answer：按 Phase 0 合同评分；
- unsupported assertion：回答断言无法追溯到最终引用 passage 时计入。

兼容要求：

- legacy V1 可以通过 adapter 进入 scorer，但正式 V2 评测只能读取 frozen V2；
- V1 与 V2 指标不得混在同一个总体分母中；
- 报告必须带 `metric_contract_version`、dataset hash 和 split。

### Task 1.6：Harness 冻结入口与完整性

- [ ] `--golden` 指向 candidates/reviewed 时，formal 模式必须拒绝运行。
- [ ] 正式运行只接受 `frozen/` 文件。
- [ ] manifest 新增：
  - schema hash
  - dataset hash
  - split
  - review manifest hash
  - corpus snapshot
  - scorer contract version
- [ ] resume 时上述任一字段变化都必须拒绝复用。
- [ ] 缺 frozen 数据、审核未完成或 corpus 不一致时 fail closed。
- [ ] 开发模式可显式使用 candidates，但输出必须标记 `non_formal=true`。

必须新增测试：

- candidates 不能进入 formal；
- reviewed 未冻结不能进入 formal；
- reviewer 相同不能 freeze；
- corpus hash 不同不能 freeze/resume；
- disputed case 不能 freeze；
- development 与 holdout case ID 不重叠；
- V1 已曝光 case 不能进入 holdout；
- answerable 缺 passage evidence 不能 freeze；
- no-answer 缺明确 reason 不能 freeze。

### Task 1.7：Phase 1 报告与权威入口

创建 `docs/evaluation/mcp-hit-rate-phase0-phase1-report.md`，至少包含：

- 变更文件与职责；
- Phase 0 前后工程门禁；
- old scorer 与 scorer V2 指标差异；
- KB-032 漏判修复证据；
- Golden V2 candidates/reviewed/frozen 实际数量；
- 待人工复核与争议样本；
- corpus/schema/scorer/dataset hash；
- reranker profile 状态；
- 数据库前后 hash；
- 未完成项和明确阻塞；
- 最终结论，只能是：
  - `Phase 0–1 完成，可进入 Phase 2；仍不具备发布条件`
  - `Phase 0–1 未完成，不得进入 Phase 2`

同步更新：

- `PROGRESS.md`
- `docs/README.md`

不得覆盖历史报告中的原始结论。

## 8. 测试与验收矩阵

### 8.1 聚焦测试

```powershell
pytest tests/architecture/test_closure_debt_baseline.py tests/architecture/test_closure_debt_strict_mode.py -q
pytest tests/architecture/test_version_consistency.py -q
pytest tests/eval/test_hit_rate_v2_scoring.py tests/eval/test_hit_rate_v2_no_answer.py -q
pytest tests/eval/test_hit_rate_v2_schema.py tests/eval/test_hit_rate_v2_freeze_gate.py -q
pytest tests/eval/test_hit_rate_v2_split_isolation.py tests/eval/test_hit_rate_artifact_sanitization.py -q
pytest tests/test_hit_rate_v7_harness_integrity.py -q
pytest tests/mcp/test_hit_rate_regressions.py tests/mcp/test_hit_rate_v3_regressions.py -q
```

### 8.2 回归与静态门禁

```powershell
python tools/report_closure_debt.py --strict
ruff check .
mypy src
pytest tests/ -q
cd client
npm run build
cd ..
```

### 8.3 数据安全

```powershell
Get-FileHash data/kb.db -Algorithm SHA256
```

执行前后 hash 必须一致。若不一致，立即停止并报告，不继续冻结或评测。

### 8.4 验收清单

- [ ] 工作区改动仅在方案范围内；
- [ ] 当前 6 个已知工程门禁失败全部恢复；
- [ ] 没有通过修改白名单或跳过测试伪修复；
- [ ] 评分核心实现唯一；
- [ ] no-answer 非拒答内容能够被检出；
- [ ] Hallucination proxy 不再被描述为完整幻觉率；
- [ ] reranker requested/effective profile 可区分；
- [ ] 原始运行产物默认 Git ignored；
- [ ] Golden V1 未被原地改写；
- [ ] V2 Schema、candidate migration、review、freeze、split 已有自动测试；
- [ ] 没有伪造人工审核元数据；
- [ ] formal harness 只读 frozen；
- [ ] 数据库 hash 不变；
- [ ] 全量 pytest、ruff、mypy、前端构建通过；
- [ ] 报告如实写明 frozen 数量和人工审核阻塞。

## 9. Phase 1 后的放行门槛定义

本表只定义后续 Phase 2–5 的最终目标，Phase 0–1 不要求达到质量分数。

| Gate | 最终目标 |
|---|---:|
| P0/P1 高风险失败 | 0 |
| Ask Fact Correctness | ≥ 90% |
| E2E Pass Rate | ≥ 90% |
| Citation Validity | ≥ 95% |
| Recall@5 | ≥ 95% |
| No-answer False Positive | 0 |
| Unsupported Assertion | 0 |
| answerable frozen 样本 | ≥ 120 |
| no-answer frozen 样本 | ≥ 20 |
| 工程/架构/版本门禁 | 100% 通过 |

不得在 Phase 0–1 末尾使用 current 37 或 high-risk 15 的分数宣告发布。

## 10. 建议执行顺序

严格按以下顺序执行：

1. Task 0.1 基线；
2. Task 0.2 工程门禁；
3. Task 0.3 scorer 权威与 no-answer；
4. Task 0.4 reranker 口径；
5. Task 0.5 artifacts 治理；
6. Task 0.6 离线重评分；
7. Task 1.1 Schema；
8. Task 1.2 V1 candidates 迁移；
9. Task 1.3 review/freeze；
10. Task 1.4 split；
11. Task 1.5 scorer V2；
12. Task 1.6 formal harness；
13. Task 1.7 报告与全量验收。

若前置 Task 未通过，不得通过调整后续数据或指标绕过。

## 11. 执行 Agent 交付格式

最终回复必须包含：

1. 完成项和未完成项；
2. 变更文件列表；
3. 关键设计决定；
4. 测试命令与精确结果；
5. 数据库前后 SHA256；
6. scorer V1/V2 差异；
7. candidates/reviewed/frozen 实际数量；
8. 是否存在人工审核阻塞；
9. 是否可进入 Phase 2；
10. 明确说明没有提交/推送，或列出已获授权并成功完成的 Git 操作。

