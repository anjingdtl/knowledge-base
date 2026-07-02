# Knowledge-Base Karpathy Wiki-First 对齐设计(第一阶段)

- **状态**:Draft(待用户复核 → 进入 writing-plans)
- **日期**:2026-07-02
- **范围**:第一阶段 4 周 wiki-first 化
- **上游依据**:`docs/knowledge-base 仓库对照 Karpathy LLM 知识库方法论的深度评估报告.md`
- **承接**:`docs/superpowers/specs/2026-06-13-mcp-local-retrieval-focus-design.md`(本地检索收束,已完成)
- **适用版本**:ShineHeKnowledge v1.4.0+

## 1. 背景与动机

评估报告对当前仓库的结论:**工程底座 8.5 / 方法论落地 6**。仓库已具备 Karpathy "LLM Wiki" 路线中最难工程化的部分——多格式摄取、增量索引、hybrid + rerank、可溯源引用、MCP 工具层、schema 文档、wiki/graph 代码骨架——但默认产品形态仍是 RAG/MCP 检索引擎,wiki 仅作"实验性附属能力",未形成主工作流。

核心缺失(均已核实):

| 缺失项 | 核实结果 |
|---|---|
| `index.md` / `log.md` 约定 | `src/` 下零匹配,完全缺失 |
| ingest-as-compile | 当前 ingest 仅 parse + index,无 wiki 编译主流程 |
| query 回写闭环 | `save_to_wiki` 痕迹存在但未标准化、未默认启用 |
| wiki lint 闭环 | `wiki_lint.py` 存在但 `lint_contradictions` 默认 `false` |
| 目录契约 | 无 `raw/ wiki/ schema/` 规范 |
| 安全默认值 | `auto_publish=true`、`experimental_tools_enabled=false`、`write_policy=""` 偏激进/偏弱 |

**最优路径**(报告结论):不是换库换模,而是把现有 wiki/schema/graph 能力**上提为主工作流**,以 `index.md`、`log.md`、query 回写、lint 闭环补齐"知识累积层"。本 spec 落地该路径的第一阶段。

## 2. 目标与非目标

### 2.1 目标

把 ShineHeKnowledge 升级为 **「编译型知识底座 + 检索执行层」双层架构**,使 wiki 成为与检索并列的一等公民主产物,落地 Karpathy 核心闭环:

```
raw(原始源,不可变) → wiki(编译产物,持续累积) → 检索/问答/回写/体检
```

### 2.2 非目标(明确排除,留待第二阶段 spec)

下列属报告"检索细节 / 扩展基础设施",本阶段不做:

- 向量维度配置化迁移、embedding 模型替换(BGE-M3 保持不变)
- 向量库迁移(Qdrant/pgvector,继续 SQLite 路线)
- parent-child 扩展到 wiki 检索、中文 lexical 通道强化
- 缓存治理(query rewrite cache / result cache / answer cache)
- 成本面板、Prometheus/OpenTelemetry、SLO
- 真实模型夜测 CI(保留现有 fake-embedding PR 门禁)
- 安全策略细化(脱敏管线 / 源目录 allowlist)

## 3. 成功标准(可验证)

| # | 标准 | 验证方式 |
|---|---|---|
| S1 | `shinehe init` 在空目录生成合规 `raw/ wiki/ schema/ artifacts/` + `AGENTS.md` | 单元测试断言目录结构与 AGENTS.md 存在 |
| S2 | 任一文档 ingest 后,`wiki/sources/` 出现 source summary,`wiki/index.md`、`wiki/log.md` 自动更新 | 集成测试:ingest 一个 PDF → 断言三处产物 |
| S3 | `shinehe wiki lint` 检出 4 类问题:孤儿页 / 矛盾 / 过时 claim / 缺失 backlinks | 单元测试:构造 4 类缺陷 fixture → 断言全部命中 |
| S4 | `ask` 高价值回答可 `save_mode=manual\|auto` 写入 `wiki/comparisons/` 或 `wiki/syntheses/` | 契约测试:auto 模式按阈值写入 draft 状态页 |
| S5 | 新增 wiki-compilation eval 在 fixture 上产出 5 项指标 | `evals/run_wiki_eval.py` 在 gold set 上运行通过 |
| S6 | 全量 pytest 无回归(基线约 950+ passed) | CI Test job 绿 |
| S7 | README 与 `config.example.yaml` 无矛盾描述 | 文档-配置一致性测试 |

## 4. 架构设计

### 4.1 目标目录契约(`shinehe init` 生成)

```
<project>/
├── raw/                # 原始资料,只读(source of truth),agent 不可改
├── wiki/               # 编译型知识层 = 主产物
│   ├── index.md        # 内容索引(每次 ingest 自动更新)
│   ├── log.md          # 时间日志(ingest/query/lint 时间线)
│   ├── sources/        # 单源摘要页 *.md
│   ├── entities/       # 实体页
│   ├── concepts/       # 概念页
│   ├── comparisons/    # 对比页(query 回写)
│   └── syntheses/      # 综合页(query 回写)
├── schema/
│   └── AGENTS.md       # agent 行为规约(page types / ingest / query / lint playbook)
├── artifacts/
│   └── eval/           # 评测产物
└── data/               # 现有 SQLite/vec/caches(保留,检索底座不变)
```

### 4.2 与现有架构的关系

- `data/`(SQLite + `vec_blocks` + FTS5)**保持不变**,继续承担检索底座
- `raw/` + `wiki/` 是**新增的知识形态层**,由 wiki 编译器增量生成
- 现有 6 个 `wiki_*` 模块(`wiki_compiler/workflow/site/site_renderer/seo/lint`)在 `wiki_compiler` 上扩展,`wiki_lint` 增强 4 类检查
- `AppContainer` 注入新 `KnowledgeWorkflowService`,依赖拓扑不变:`Config → Database → VectorStore → BlockStore → Embedding/LLM → Repositories → KnowledgeWorkflowService → 业务服务`

### 4.3 运行模式:`wiki_first` 与 `legacy`

新增 `knowledge_workflow.mode`:

| 模式 | 行为 | 适用场景 |
|---|---|---|
| `wiki_first` | ingest 触发 wiki 编译;index/log 自动维护;lint 默认开;query 回写可用 | 新 init 项目、已迁移项目 |
| `legacy` | 现有行为;ingest 仅索引;wiki 工具保持实验性 | 老项目升级兼容(默认) |

**默认值规则**:
- `shinehe init` 生成 `mode: wiki_first`
- 现有项目升级时,若无 `knowledge_workflow` 段 → 默认 `legacy`(向后兼容,不破坏现部署)
- `shinehe migrate` 把项目从 `legacy` 切到 `wiki_first`

## 5. 配置变更

### 5.1 新增 `knowledge_workflow` 段

```yaml
knowledge_workflow:
  mode: wiki_first           # wiki_first | legacy
  raw_dir: raw
  wiki_dir: wiki
  schema_file: schema/AGENTS.md
  source_summary_dir: wiki/sources
  entity_dir: wiki/entities
  concept_dir: wiki/concepts
  synthesis_dir: wiki/syntheses
  comparison_dir: wiki/comparisons
  maintain_index_md: true
  maintain_log_md: true
```

### 5.2 安全默认值收敛(`config.example.yaml`)

| 键 | 现值 | 新值 | 理由 |
|---|---|---|---|
| `wiki.auto_publish` | `true` | `false` | 改 review gate:自动编译、非自动发布、审阅后发布 |
| `wiki.lint_contradictions` | `false` | `true` | 启用 lint 闭环 |
| `mcp.experimental_tools_enabled` | `false` | `true` | 暴露 wiki/graph 工具组(与 wiki-first 一致) |
| `mcp.write_policy` | `""` | `local_confirm` | 写操作本地确认 |
| `storage.chroma_dir` | `chroma` | 移除 | legacy 清理(`vec_chunks` 已弃用) |

> 向后兼容:老配置无 `knowledge_workflow` 段时,`mode` 缺省为 `legacy`,上述收敛项对新 init 生效;`config.yaml` 不再纳入版本控制(已由 commit `dac146a` 处理),示例值收敛不影响已部署实例。

## 6. 分阶段任务(W1–W4)

每个涉及现有符号的任务,**实现前必须先 `gitnexus impact` 评估 blast radius**(见 §10)。

### 6.1 W1 — 地基

| # | 任务 | 改动位置 | 验收(DoD) |
|---|---|---|---|
| 1.1 | `shinehe init` 生成四目录 + 子目录 + `AGENTS.md` 模板 | `src/cli.py`;新增 `src/services/project_scaffolder.py` | 空目录 init → 结构完整(测试断言) |
| 1.2 | `knowledge_workflow` 配置加载 + `mode=legacy` 缺省兜底 | `src/utils/config.py`;`src/core/container.py` | 老配置启动通过;新段解析正确 |
| 1.3 | 安全默认值收敛 + 移除 `chroma_dir` | `config.example.yaml` | 加载测试通过;无残留 `chroma_dir` 引用 |
| 1.4 | `chroma_dir` 引用清理 | `src/services/vectorstore.py`(impact 后定) | 移除后向量检索功能不变 |

### 6.2 W2 — 编译器

| # | 任务 | 改动位置 | 验收(DoD) |
|---|---|---|---|
| 2.1 | **source summary 规则模板**(零 LLM):从 `ParsedFile` metadata + 结构化 block 抽取标题/关键句/识别实体 → `wiki/sources/<slug>.md` | 新增 `src/services/wiki_source_compiler.py`;挂到 `wiki_compiler.py` | ingest PDF → sources/ 出现 summary;幂等 |
| 2.2 | **LLM 实体/概念页更新**:识别新源涉及的实体/概念,更新 `entities/`、`concepts/`;矛盾显式标注;`max_llm_calls_per_ingest=3` 硬上限 | 新增 `src/services/wiki_entity_updater.py` | 单次 ingest LLM 调用 ≤3;无重复页 |
| 2.3 | **`wiki/index.md` 生成器**:按 page type 分组聚合所有 wiki 页 | 新增 `src/services/wiki_index_compiler.py` | ingest 后 index.md 自动刷新 |
| 2.4 | **`wiki/log.md` 生成器**:追加 ingest/query/lint 时间线(复用 `operation_log`) | 新增 `src/services/wiki_log_compiler.py` | log.md 含时间戳条目 |
| 2.5 | ingest 触发钩子:`mode=wiki_first` 时 ingest 完成后异步调用编译器(复用 jobs 框架);失败只告警不阻塞索引 | `src/services/indexer.py`、`path_indexer.py` | 编译失败时检索主流程不受影响 |

**source summary 模板字段**(2.1 无歧义约定):`title` / `source_path` / `file_type` / `summary`(首段 + 标题路径精炼,≤500 字)/ `key_entities`(规则抽取的专名/缩略词)/ `ingested_at`(由调用方传入时间戳,不在编译器内取系统时间)/ `source_hash`。

**slug 规则**(2.1/2.2 共用):文件名安全化——小写、标点去除、空格转连字符;中文保留原字;冲突时追加 `-{short_hash[:8]}`。

### 6.3 W3 — 闭环

| # | 任务 | 改动位置 | 验收(DoD) |
|---|---|---|---|
| 3.1 | **query 回写标准化**:`save_to_wiki` 增加 `save_mode=manual\|auto`;auto 按 `query_save_min_length`(≥100) + confidence(≥0.6) + source_count(≥2) 阈值写入 `comparisons/`/`syntheses/`;默认 `draft` 状态走 review gate | `src/mcp_server.py`;`src/services/rag_pipeline.py`(ask 后回写钩子) | ask 高质量回答 → auto 写入 draft 页 |

> **confidence 定义**(任务 3.1):初版取 rerank 后 top 候选的 `score`;实现期 `gitnexus impact` 评估后,可调整为 LLM 自评分或 rerank + 自评融合。阈值 0.6 基于 `config.example.yaml` 现有 `score_threshold: 0.35`,取其上沿以保证仅高置信回答被沉淀。
| 3.2 | **wiki lint 增强 4 类**:孤儿页(无入链)/ 矛盾(`lint_contradictions`)/ 过时 claim(source `updated_at` 晚于 wiki 页)/ 缺失 backlinks | `src/services/wiki_lint.py` | `shinehe wiki lint` 输出分类报告 |
| 3.3 | **CLI 补全**:`shinehe wiki lint` / `save-answer` / `ingest-source` | `src/cli.py` | 三子命令可用,help 文档完整 |

### 6.4 W4 — 收口

| # | 任务 | 改动位置 | 验收(DoD) |
|---|---|---|---|
| 4.1 | 文档一致性:修正 `README.md:103` "Default core profile" 措辞,统一 `extended`;config 与 README 对齐 | `README.md`;`README_zh.md` | 文档-配置一致性测试通过 |
| 4.2 | **`shinehe migrate`**:默认 `--dry-run` + 强制备份 `data/`;导出源文件到 `raw/`(按 `source_path`)→ 触发 wiki 重编译 → 切 `mode=wiki_first` | 新增 `src/services/migrator.py`;`src/cli.py` | dry-run 输出计划;apply 后 raw/wiki 就绪,data/ 不损毁 |
| 4.3 | **wiki-compilation eval**:`run_wiki_eval.py` + fixtures;指标 Source Coverage / Cross-page Update Rate / Orphan Page Rate / Query Save Rate / Stale Claim Ratio;CI **nightly**(不进 PR 门禁) | `evals/run_wiki_eval.py`;`evals/fixtures/`;`.github/workflows/ci.yml` | fixture 上产出 5 项指标 |

## 7. schema/AGENTS.md 模板(`shinehe init` 生成)

```markdown
# AGENTS.md

## Source of truth
- raw/ 下所有文件只读,agent 不得直接修改
- 所有综合结论必须可追溯到 raw/ 文件或已有 wiki 页

## Page types
- wiki/sources/*.md     单源摘要页(规则模板生成)
- wiki/entities/*.md    实体页(LLM 维护)
- wiki/concepts/*.md    概念页(LLM 维护)
- wiki/comparisons/*.md 对比页(query 回写)
- wiki/syntheses/*.md   综合页(query 回写)

## Ingest workflow
- 读取 raw/ 新源
- 生成 source summary(wiki/sources/)
- 识别并更新相关 entities/concepts
- 更新 wiki/index.md,追加 wiki/log.md
- 与旧结论冲突时显式标注

## Query workflow
- 先读 wiki/index.md 定位相关页
- 再读相关 wiki 页
- 证据不足时回到 raw/ 检索
- 高价值回答可保存为新 wiki 页(comparisons/syntheses,draft 状态)

## Lint workflow
- 孤儿页、矛盾、过时 claim、缺失 backlinks 四类检查
- 发现问题标注待修,不自动删除
```

## 8. 测试与验收策略

| 层级 | 范围 |
|---|---|
| 单元 | 每个新模块独立测试:`project_scaffolder` / `wiki_source_compiler` / `wiki_entity_updater` / `wiki_index_compiler` / `wiki_log_compiler` / `migrator` |
| 集成 | ingest → compile → lint → query → save 全链路 e2e |
| 回归 | 全量 pytest 不退化(基线约 950+ passed) |
| 契约 | MCP 工具契约测试(`save_to_wiki` 新参数 + 向后兼容) |
| eval | wiki-compilation nightly |
| 门禁 | PR:fake-embedding 检索门禁 + 全量 pytest;nightly:wiki-compilation eval |

每阶段 DoD 即 §6 任务表"验收"列。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 现有用户兼容性 | `mode=legacy` 兜底 + `migrate` 渐进,不强制迁移 |
| LLM 成本/延迟 | 规则模板优先 + `max_llm_calls_per_ingest=3` 硬上限 + 编译异步化 |
| 幻觉/错误回写 | auto 回写走 draft review gate + confidence/source_count 双阈值 |
| 编译失败影响检索 | 编译器与索引主流程解耦,失败只告警不阻塞 |
| 改动既有符号 blast radius | 每个涉及现有模块的改动,实现前先 `gitnexus impact` |
| 编译可复现性 | wiki 页时间戳由 ingest/查询调用方传入,编译器内不取系统时间,保证可复现测试 |

## 10. 迁移方案

`shinehe migrate` 流程:

1. `--dry-run`(默认):扫描 `data/` 知识条目,输出迁移计划(将复制哪些源文件到 `raw/`、将重编译哪些 wiki 页),不写盘
2. `--apply`:备份 `data/` → 按 `source_path` 导出源文件到 `raw/` → 触发 wiki 重编译 → 生成 `index.md`/`log.md` → 写回 `mode: wiki_first`
3. **不删除 `data/`**,双轨过渡;迁移失败可从备份回滚

迁移指南文档随 CLI 一并提供(`docs/migration/wiki-first-migration.md`)。

## 11. Karpathy 方法论对齐映射(本 spec 落地后)

| Karpathy 原则 | 本 spec 落地点 |
|---|---|
| 原始资料不可变 | `raw/` 只读契约 + AGENTS.md 规约(§4.1/§7) |
| 持久化 wiki 为中间层 | `wiki/` 主产物 + `mode=wiki_first`(§4.3) |
| schema 驱动 agent | `schema/AGENTS.md` 强制模板(§7) |
| ingest 是编译非仅索引 | W2 全部任务(§6.2) |
| query 针对 wiki | query 回写闭环 + wiki context 主数据面(§6.3) |
| 答案可沉淀回 wiki | `save_to_wiki` 标准化 + auto 模式(§6.3 任务 3.1) |
| lint/health-check | wiki lint 4 类 + 默认开(§6.3 任务 3.2) |
| `index.md` 内容索引 | W2 任务 2.3(§6.2) |
| `log.md` 时间日志 | W2 任务 2.4(§6.2) |
| 本地化与可检查性 | SQLite + markdown wiki + graph 不变 |

本 spec 不覆盖的 Karpathy 项(小规模用 index/大规模用搜索、parent-child 扩展)在第二阶段。

## 12. 依赖与前置(实现期)

以下符号在本 spec 实现时会改动,实现前必须 `gitnexus impact` 评估:

- `src/services/wiki_compiler.py`(W2 扩展挂载点)
- `src/cli.py`(W1 init / W3 wiki 子命令 / W4 migrate)
- `src/utils/config.py`(W1 配置加载)
- `src/core/container.py`(W1 注入 KnowledgeWorkflowService)
- `src/services/indexer.py` / `path_indexer.py`(W2 触发钩子)
- `src/mcp_server.py`(W3 save_to_wiki)
- `src/services/rag_pipeline.py`(W3 回写钩子)
- `src/services/wiki_lint.py`(W3 增强)
- `src/services/vectorstore.py`(W1 chroma 清理)

## 13. 阶段交付里程碑

| 里程碑 | 交付 | 后续 |
|---|---|---|
| W1 完成 | 目录契约 + schema + 安全默认值可用 | 可 init 新 wiki-first 项目 |
| W2 完成 | ingest 自动编译 wiki + index/log | 知识开始累积 |
| W3 完成 | 回写 + lint + CLI 闭环 | 方法论闭环可用 |
| W4 完成 | 文档收敛 + migrate + eval | 可迁移老项目 + 可量化质量 |

每完成一个里程碑跑对应回归与契约测试,全量 pytest 不退化方可进入下一阶段。
