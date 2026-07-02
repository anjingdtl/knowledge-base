# knowledge-base 仓库对照 Karpathy LLM 知识库方法论的深度评估报告

## 执行摘要

`knowledge-base` 当前已经不是“只有检索”的原型，而是一个本地优先、以 SQLite/sqlite-vec/FTS5 为底座、带增量索引、混合检索、重排序、MCP/REST/GUI 多入口、并且已初步具备 wiki/graph/lint 能力的工程化知识系统。它与 Karpathy 的“LLM Wiki”方法论**部分重合但未完全对齐**：最强的是本地检索、可解释引用、增量索引与工具化执行；最弱的是“以持久化 wiki 为主产物”的主工作流、`index.md`/`log.md` 约定、原始资料→结构化 wiki→查询沉淀回写的闭环。结论是：**不建议推倒重来**，应在现有 Block-First/RAG 基础上，把 wiki 从“实验性附属能力”升级为“一等公民”，形成“原始源不可变 + 编译型 wiki + 检索/问答/回写/体检”的双层架构。这样既能保留当前高精度检索优势，又能真正落到 Karpathy 所强调的“知识不断累积，而不是每次重找一遍”。 citeturn41view1turn35view0turn32view0turn36view1

## 仓库现状审查

从仓库根目录可见，项目包含 `src/`、`tests/`、`evals/`、`client/`、`.github/workflows/`、`docs/`、`Dockerfile`、`docker-compose.yml`、`CLAUDE.md` 等，说明它已具备服务层、前端、评测、CI、部署与 agent schema 文档等完整工程骨架。根目录还存在 `CLAUDE.md`，这对实现 Karpathy 所说的“schema layer”是非常关键的基础。 citeturn39view0turn40view0

`CLAUDE.md` 对架构给出的总结相对权威：GUI、REST API、MCP Server、Windows 服务四种运行模式共享同一服务层；`src/services/` 中包含 `db.py`、`vectorstore.py`、`block_store.py`、`path_indexer.py`、`file_watcher.py`、`embedding.py`、`hybrid_search.py`、`search_service.py`、`rag_pipeline.py`、`wiki_*.py` 等；`src/mcp/tool_profiles.py` 定义不同工具配置档；`src/api/` 使用 FastAPI 工厂；`src/core/container.py` 承担依赖注入。换言之，这个仓库的核心不是单一点功能，而是**检索引擎 + agent 工具层 + 多入口运行时 + wiki/graph 扩展层**的组合。 citeturn40view0turn22view2

### 仓库审查总表

| 维度 | 现状审查 | 结论 | 证据 |
|---|---|---|---|
| 关键模块 | `db.py`、`block_store.py`、`vectorstore.py`、`hybrid_search.py`、`rag_pipeline.py`、`path_indexer.py`、`file_parser.py`、`text_splitter.py`、`query_rewriter.py`、`wiki_*`、`graph_*`、`mcp_server.py`、`api/` | 已形成较完整知识系统栈 | citeturn22view2turn40view0 |
| 数据采集 | 支持 PDF、PPTX、DOCX、XLSX、CSV、TXT、Markdown、HTML、代码、图片；Excel 可拆为多 `ParsedFile`，PPT/PDF/DOCX/XLSX 可产生结构化 block 树 | 覆盖度高 | citeturn29view3turn28view1turn28view2turn28view3 |
| 数据格式 | `ParsedFile` 字段包括 `title/content/file_type/source_path/metadata/structured` | 清晰，适合后续 block 化与 wiki 编译 | citeturn29view3 |
| 数据流 | `parse_file` → `index_knowledge_item` → 去重 hash → chunk/block 生成 → embedding 缓存 → `vec_blocks`/FTS 写入 → `search/ask` 检索与生成 | 流程完整，偏“检索型” | citeturn29view3turn31view0turn31view1turn27view7turn37view1 |
| 分段策略 | 普通文本默认 `chunk_size=500/chunk_overlap=50`；Markdown 保留标题路径；代码默认 `800/50`；`config.example.yaml` 的推荐值已调为 `1200/180` | 存在“代码默认值”和“配置推荐值”双轨 | citeturn30view0turn30view1turn30view2turn35view0 |
| 索引方式 | SQLite FTS5 建有 `knowledge_fts`、`chunk_fts`、`block_fts`；向量侧当前主路径是 BlockStore/`vec_blocks`，`vectorstore.py` 中 `vec_chunks` 被标注为 legacy/逐步弃用 | 已迁到 Block-First，但遗留路径仍在 | citeturn27view4turn25view6turn27view5 |
| 检索策略 | `HybridSearcher` 采用向量 + 关键词并行检索，加权 RRF 融合；配置默认 `search_mode: blend`，并可重排序 | 已实现 hybrid + rerank | citeturn17view0turn17view1turn35view0 |
| 检索接口 | MCP 默认 `extended` 档暴露 20 工具；核心有 `search/ask/read/list_knowledge/index_path` 等；`ask` 返回 `answer/sources/source_graph/route/query_plan/block_contexts/warnings/wiki_context` | MCP 接口很强，适合 agent | citeturn41view0turn36view2turn37view1 |
| REST 接口 | 通过 `run_api.py` 启动 FastAPI；`src/api/routes` 存在 `auth/chat/graph/jobs/knowledge/settings` 等路由 | 已具备 REST 层，但具体 endpoint 细节本次未逐一核验，记为“部分指定” | citeturn19view1turn40view0 |
| 部署架构 | 支持桌面 GUI、REST API、MCP stdio/HTTP、Docker 多阶段镜像、docker-compose 双服务（API/MCP） | 多入口完备 | citeturn19view2turn20view0turn18view0turn19view0 |
| 目录增量更新 | `path_indexer.py` 用 `size/mtime/hash` 做差异检测；`file_watcher.py` 用 watchdog 推送到 `IndexScheduler` | 增量索引设计较成熟 | citeturn17view3turn27view2 |
| 缓存与去重 | Embedding 有三级缓存 L1 内存 → L2 SQLite → L3 API；内容去重用 SHA-256 | 已有成熟雏形 | citeturn25view4turn31view0turn31view1 |
| 评测 | `evals/` 含 datasets、metrics、`run_retrieval_eval.py`；CI 有 retrieval-eval job | 已具备可复现评测框架 | citeturn22view0turn15view0 |
| 测试 | 仓库 README/README_zh 声明 v1.4.0 全量测试 `951 passed, 1 skipped`；`tests/` 覆盖索引、检索、MCP、graph、watcher、评测数据等 | 覆盖面广 | citeturn41view1turn21view0 |
| CI | Lint、Test、Frontend Build、Retrieval Eval、Docker Build 五门禁 | 工程化成熟 | citeturn15view0 |
| 明显未指定项 | 对外 SLA、生产级容量规划、灾备方案、数据分级制度、精确 REST API 清单、正式监控仪表盘模板 | 报告中统一标注“未指定” | citeturn19view1turn15view0 |

### 现有数据流与实现判断

当前主数据流可以概括为：文件解析器将多种文档转成 `ParsedFile`，再由 `indexer.py` 完成内容 hash 去重、分段、block/chunk 生成、embedding 生成与缓存、向量索引和 FTS 建立；查询时由 `HybridSearcher` 并发执行语义检索与关键词检索，之后进入 `rag_pipeline.py` 的多阶段处理，再通过 MCP `ask` 对外返回结构化答案与来源图谱。这个链路非常适合“高精度可溯源检索”，但其默认目标仍是**把原始文档变成可问答的检索服务**，而不是把原始文档“编译”为一套持续演进的 wiki。 citeturn29view3turn31view1turn17view1turn17view4turn37view1turn41view1

这里还有一个值得明确指出的问题：文档存在一定不一致。英文 README 在一处写“Default `core` profile exposes 10 stable tools”，但同一 README 的 Quick Start 又写 `shinehe init --local` 生成 `mcp.tool_profile=extended`；中文 README、迁移指南和 `tool_profiles.py` 也都将 `extended` 描述为推荐/默认。对工程团队而言，这种不一致会直接影响部署与用户预期，应该尽快收敛。 citeturn41view2turn41view0turn14view1turn36view1

## 与 Karpathy 方法论的映射

Karpathy 在其原始 “LLM Wiki” 文档中强调，个人或团队知识库不应停留在“问的时候做一次 RAG”，而应在原始资料之上持续维护一套**持久化、互相链接、会随着摄取和提问不断丰富的 wiki**。他把架构拆成三层：不可变原始资料、LLM 维护的 wiki、以及约束工作流的 schema 文档；同时强调 ingest、query、lint 三种核心操作，以及 `index.md` 和 `log.md` 等内容索引/时间日志机制。 citeturn32view0

`knowledge-base` 与这套方法论之间的关系，不是“完全不符”，而是“底层能力很强，但默认产品心智仍偏 RAG 引擎”。仓库已经具备 Karpathy 路线中最难工程化的部分——多格式摄取、增量更新、可解释检索、结构化工具接口、schema 文档、wiki/graph 代码骨架——但还没有把这些能力组织成一个**明确的 wiki-first 主工作流**。 citeturn40view0turn35view0turn32view0

### 方法论映射表

| Karpathy 原则 | 原始来源含义 | 仓库现状 | 判断 | 依据 |
|---|---|---|---|---|
| 原始资料不可变 | Raw sources 是 source of truth，LLM 读但不改 | 仓库强调本地文档索引、保留 `source_path`，但未看到清晰的 `raw/` 目录约定与“不可修改”制度化说明 | 部分实现 | citeturn32view0turn29view3turn41view1 |
| 持久化 wiki 作为中间层 | wiki 不是聊天缓存，而是长期累积的主产物 | 仓库已有 `wiki_*` 服务、`wiki.enabled/auto_compile/auto_link/auto_publish` 配置，但 README 主叙事仍是“知识检索引擎” | 部分实现 | citeturn22view2turn35view0turn41view1 |
| schema 文档驱动 agent 行为 | 通过 CLAUDE.md/AGENTS.md 让 agent 按约定维护 wiki | 根目录已有 `CLAUDE.md`，并详细说明命令、架构、分层与运行方式 | 匹配 | citeturn39view0turn40view0 |
| ingest 是“吸收并整合知识”而非仅索引 | 新来源会更新 summary、entity page、concept page、log | 当前 ingest 强项是解析、分段、索引、异步作业；wiki 编译能力存在，但不是主 ingest 流程 | 部分实现 | citeturn35view0turn15view0turn41view1 |
| query 针对 wiki 而非仅原文 | 先读 index，再读相关 page，再综合回答 | 当前 `ask/search` 明显围绕 block/raw-doc 检索；wiki context 虽存在，但不是主数据面 | 缺失到部分实现之间，偏弱 | citeturn37view1turn41view1turn35view0 |
| 答案应可沉淀回 wiki | 好回答、比较表、分析结论应写回 wiki | 配置中有 `wiki.query_save_min_length`，安全守卫中出现 `save_to_wiki`，但默认 profile/README 并未把它做成常规闭环 | 部分实现 | citeturn35view0turn37view3turn36view1 |
| lint/health-check | 检查矛盾、过时内容、孤儿页、缺失链接与数据空白 | 仓库存在 `wiki_lint.py`、`wiki.lint_contradictions` 配置，但默认是 `false`，且实验性分组未默认暴露 | 部分实现 | citeturn23view0turn35view0turn36view0 |
| `index.md` 内容索引 | 以内容目录帮助 agent 先找页再深入 | 仓库未看到 `index.md` 作为 wiki 目录的显式规范 | 缺失 | citeturn32view0turn39view0 |
| `log.md` 时间日志 | 追踪 ingest/query/lint 历史 | 仓库有 `operation_log` 数据留存 90 天，但这是系统审计日志，不是面向 wiki 的 markdown `log.md` | 部分实现 | citeturn32view0turn34view2turn37view2 |
| 小规模用 index，大规模用搜索 | 随 wiki 增长引入 proper search/hybrid/rerank | 仓库已经有 hybrid + rerank + source graph，检索能力反而领先 Karpathy 示例 | 匹配 | citeturn32view0turn17view1turn34view2 |
| 本地化与可检查性 | 用 markdown/wiki/graph 让知识可审阅 | 仓库 local-first、SQLite、本地文件与 wiki/site/graph 都支持，但默认交互仍偏工具化检索 | 部分实现 | citeturn41view1turn40view0turn19view0 |

### 核心判断

如果以 Karpathy 的标准打分，我会给这个仓库一个很明确的结论：**“底座 8.5 分，方法论落地形态 6 分。”** 也就是说，它已经拥有实现 LLM Wiki 所需的大量工程能力，但还没有把产品默认路径切到“编译型知识库”。现在用户主要得到的是一个高质量的 RAG/MCP 引擎，而不是一个持续沉淀、持续改写、持续体检的 wiki 维护系统。这个差异不是技术能力缺失，而是**系统主引导、默认目录规范、工具默认暴露方式、以及 ingest/query/lint 流程编排**的问题。 citeturn41view1turn36view1turn32view0

## 关键优化建议

最重要的不是盲目更换模型或数据库，而是先把系统从“文档问答引擎”重构为“编译型知识底座 + 检索执行层”的双层结构。建议采用如下目标状态：

```text
raw/            # 原始资料，只读
wiki/
  index.md      # 内容索引
  log.md        # 时间日志
  sources/      # 单源摘要
  entities/     # 实体页
  concepts/     # 概念页
  comparisons/  # 对比页
  syntheses/    # 主题综合页
schema/
  AGENTS.md     # 维护规则
artifacts/
  eval/
  reports/
data/           # SQLite / vec / caches
```

这个结构直接对应 Karpathy 的三层：`raw/` 是 source of truth，`wiki/` 是持久化知识层，`schema/AGENTS.md` 则是 agent 行为规约。当前仓库已有 `CLAUDE.md`、`wiki_*`、`operation_log`、`source_graph`、`hybrid_search` 等能力，因此这更像一次**架构归位**而非推翻重建。 citeturn32view0turn40view0turn35view0

### 按优先级排序的改进清单

下表按“是否直接推动 Karpathy 对齐”与“工程影响面”综合排序。

| 改进项 | 当前状态 | 建议动作 | 优先级 | 预期收益 | 实施难度 | 所需资源 | 时间估算 |
|---|---|---|---|---|---|---|---|
| 把 wiki 设为一等公民 | wiki 能力存在，但默认心智仍是 RAG 检索 | 新增 `wiki-first` 运行模式；CLI 增加 `shinehe ingest-source`、`shinehe wiki lint`、`shinehe wiki save-answer`；默认文档与 demo 改为“原始源→wiki→查询” | 高 | 直接完成方法论对齐 | 中 | 后端 1-2 人 | 1-2 周 |
| 固化 `raw/wiki/schema` 目录契约 | 未形成 Karpathy 风格目录规范 | 初始化脚手架生成 `raw/ wiki/ schema/ artifacts/`，并声明 `raw/` 只读 | 高 | 降低团队协作歧义，利于审计与回滚 | 低 | 后端 1 人 | 2-3 天 |
| 引入 `index.md` 与 `log.md` | 目前更多是 DB 日志与操作日志 | 每次 ingest 自动更新 `wiki/index.md` 与 `wiki/log.md`；查询保存时也记录 | 高 | 形成可浏览、可追溯的人工可审阅知识面 | 中 | 后端 1 人 | 3-5 天 |
| 把 ingest 从“索引”升级为“编译” | 目前 ingest 主要是 parse + index | 每个新源先生成 `sources/*.md`，再更新实体页/概念页/综合页；可先以规则驱动，后加 LLM 决策 | 高 | 从检索库变成累积型知识库 | 中高 | 后端 + prompt 1-2 人 | 2-3 周 |
| 启用查询沉淀回写 | 存在 `query_save_min_length`、`save_to_wiki` 痕迹，但闭环不强 | 对 `ask` 增加 `save_mode=manual|auto`，将高价值回答写入 `comparisons/` 或 `syntheses/` | 高 | 让用户探索结果复利积累 | 中 | 后端 1 人 | 1 周 |
| 开启 wiki lint 闭环 | `wiki.lint_contradictions=false` | 新增 nightly lint：矛盾、孤儿页、缺失 backlinks、过时 claims、未落页实体 | 高 | 提升“维持知识健康”能力 | 中 | 后端 1 人 | 1-2 周 |
| 文档与配置一致性收敛 | README/README_zh/迁移指南/代码对默认 profile 叙述不一致 | 统一为 `extended` 或重新定义默认；废弃/保留项写清楚 | 高 | 降低接入成本与误配置 | 低 | 维护者 1 人 | 1-2 天 |
| 向量维度配置化 | 当前 legacy `vectorstore.py` 明确写 1024 维并注释为 bge-m3 | 将向量维度从代码常量迁至 schema/config，并提供迁移脚本 | 高 | 为 OpenAI/Qwen 等替换模型扫清障碍 | 中 | 后端 1 人 | 3-5 天 |
| 收敛 legacy 路径 | `vec_chunks` 已被标记 legacy，但配置里仍有 `chroma_dir` 等遗留味道 | 明确标注弃用计划；迁移到纯 `vec_blocks`；清理无效配置键 | 中 | 降低维护负担与认知噪声 | 中 | 后端 1 人 | 1 周 |
| 分段策略按文档类型细化 | 代码默认 800/50，文本默认 500/50，但 `config.example` 推荐 1200/180；风格混杂 | 建立“文档类型 → chunk policy”矩阵，分别针对 md/pdf/docx/xlsx/code/pptx | 高 | 提升召回、降低冗余上下文 | 中 | 检索工程师 1 人 | 1 周 |
| 将 parent-child 扩展到 wiki 检索 | 当前 parent-child 已支持 raw block 上下文扩展 | 在 wiki 页检索上保留“页级召回 + 段级定位 + 父页补全”双层策略 | 高 | 强化 Karpathy 式“页导向”问答 | 中 | 后端 1 人 | 1 周 |
| 对中文做更强的 lexical 通道 | 当前为 FTS5 + 预分词，已可用 | 若保留 SQLite，继续强化中文专名、缩略词、标签、标题 boost；若迁 Qdrant/pgvector，也保留强 lexical 通道 | 中 | 中文检索更稳 | 中 | 检索工程师 1 人 | 1 周 |
| 增强缓存治理 | 已有 embedding L1/L2/L3 缓存，但生成缓存未见强约束 | 引入 query rewrite cache、retrieval result cache、answer cache；按 source revision 失效 | 中 | 降低延迟与 API 成本 | 中 | 后端 1 人 | 1 周 |
| 成本面板化 | 仓库有评测和 trace，但成本 dashboard 未指定 | 记录每次 ingest/query 的 token、embedding 次数、rerank 开销、缓存命中率 | 中 | 便于选型与预算管理 | 中 | 后端 1 人 | 1 周 |
| 自动化真实模型夜测 | CI 当前 retrieval-eval 使用 fake-embedding | 保留 PR fake 模式，新增 nightly real-embedding/real-rerank 基准 | 高 | 防止“离线评测看起来很好，线上退化” | 中 | CI + 一套测试凭据 | 1 周 |
| 安全策略细化 | 已有 `write_policy`、`allow_http_write=false`、本地优先 | 再加源目录 allowlist、脱敏管线、自动发布人工审批门 | 高 | 降低泄露与误写风险 | 中 | 后端 1 人 | 1-2 周 |
| 自动发布默认改审阅制 | `wiki.auto_publish=true` 在配置示例中较激进 | 改为 review gate：`auto_compile=true`，`auto_publish=false`，通过审阅后发布 | 高 | 兼顾效率与合规 | 低 | 后端 1 人 | 1-2 天 |
| 监控与SLO | 未指定正式生产监控面板 | 加 Prometheus/OpenTelemetry 指标：索引延迟、ask P95、cache hit、rerank fail、lint deficit | 中 | 便于生产运行 | 中 | 平台/后端 | 未指定 |

以上建议的总体优先次序有一个明确逻辑：**先做知识形态，再做检索细节，再做扩展基础设施。** 也就是说，第一批落地不应是“换更大的 embedding 模型”，而是“把 wiki 工作流做对”。因为如果系统的主产物仍然是 raw-doc RAG，那么无论你把向量模型从 BGE-M3 换到更强模型，都只能提升“找文档”的效果，而无法实现 Karpathy 最看重的“知识不断沉淀”。 citeturn32view0turn35view0turn45view5

### 可直接采用的配置思路

下面是一个**更接近 Karpathy 工作流**的建议配置片段。它不是对当前仓库的精确最终 schema 定稿，而是面向现有配置结构的实施草案。

```yaml
mcp:
  tool_profile: extended
  experimental_tools_enabled: true
  write_policy: local_confirm
  allow_http_write: false

wiki:
  enabled: true
  auto_compile: true
  auto_link: true
  auto_publish: false
  lint_contradictions: true
  query_save_min_length: 80

rag:
  search_mode: blend
  enable_query_rewriting: true
  enable_rerank: true
  top_k: 12
  rerank:
    top_n: 6
  parent_child:
    enabled: true
    max_parent_chars: 5000

knowledge_workflow:
  mode: wiki_first
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

这个方案与仓库已有配置项高度兼容：`extended`、`experimental_tools_enabled`、`write_policy`、`wiki.*`、`rag.*` 都已经存在；真正新增的是少量用于组织目录与 wiki 维护约定的 workflow 配置。 citeturn35view0turn36view1

## 技术选型比较

### 嵌入模型比较

当前仓库配置示例直接使用 `BAAI/bge-m3`，而且 legacy 向量存储代码里 `_VEC_DIM = 1024` 也明确写着“bge-m3 输出维度”。这意味着**BGE-M3 与现有代码兼容性最高**。如果希望对齐 Karpathy 的“wiki-first”路线，我的建议不是立刻替换它，而是先把维度与 provider 解耦，再评估是否要分场景切换到更强但更重的模型。 citeturn33view0turn25view6turn45view4

| 模型 | 特性 | 优点 | 缺点 | 成本 | 吞吐/延迟预期 | 开源/托管 | 与现仓兼容性 |
|---|---|---|---|---|---|---|---|
| BAAI/bge-m3 | 1024 维，8192 tokens，100+ 语言，支持 dense/sparse/multi-vector，并明确建议 “hybrid retrieval + re-ranking” | 与当前仓库默认配置高度一致；中文/多语友好；天然适合 hybrid | 需要自托管或兼容 API；若使用其 sparse 能力，现仓还没完全吃满 | API 费未指定；自托管无 per-token 费 | 中等延迟，中等吞吐，适合本地或私有化 | 开源/可自托管 | 高 | citeturn44view0turn45view3turn45view5turn33view0 |
| OpenAI text-embedding-3-large | 3072 维；OpenAI 标注为对英语和非英语都更强；官方价 $0.13 / 1M tokens | 托管稳定、效果好、接入简单 | 维度与现仓 1024 假设不一致；本地优先与隐私策略会弱化；需联网 | 明确 API 价 $0.13 / 1M tokens | 托管模式下工程延迟低，吞吐受 API 配额控制 | 托管优先 | 中 | citeturn44view2turn42search7turn25view6 |
| Qwen3-Embedding-8B | 100+ 语言、32K 上下文、最高 4096 维、支持自定义维度和 instruction-aware | 长文能力强；多语能力强；适合复杂中文/代码/跨语言检索 | 8B 体量重；本地 GPU 成本更高；需要对仓库向量维度与 provider 做更强解耦 | API 价未指定；自托管算力成本高 | 吞吐相对低、延迟相对高，但质量上限高 | 开源/可自托管 | 中 | citeturn45view1turn45view2 |

**建议结论：**  
如果你的目标是“最快实现 Karpathy 方法论”，先保留 **BGE-M3**。原因不只是“它已经在用”，更关键的是它天生支持 dense+sparse 的统一检索思路，而仓库现在默认也是 `blend` + rerank，这与模型能力是同向的。若后续要做“高质量署名研究库”或“长文深度综合”，再把 **Qwen3-Embedding-8B** 作为高配选项；若要快速接入云端托管并容忍数据外发，再评估 **text-embedding-3-large**。 citeturn45view5turn34view4turn44view1turn44view2

### 向量数据库比较

从代码与配置看，项目当前主路径是 SQLite + FTS5 + sqlite-vec 的本地单机方案；`vectorstore.py` 中旧 `vec_chunks` 已被标成 legacy，`BlockStore` 负责新的 `vec_blocks` 路径。对 Karpathy 方法论而言，这条路线其实非常合理，因为 LLM Wiki 天然更适合“可检查、可携带、低运维”的本地数据底座。 citeturn25view6turn27view5turn41view1

| 向量库 | 优点 | 缺点 | 成本 | 吞吐/延迟预期 | 开源/托管 | 与现仓兼容性 |
|---|---|---|---|---|---|---|
| SQLite + sqlite-vec + FTS5 | 单文件、本地优先、部署最轻；FTS5 原生全文检索；sqlite-vec 可在 SQLite 内存储/查询向量；极适合 edge/local 工具 | 大规模并发与分布式能力弱；生态不如专用向量库完整；`sqlite-vec` 仍是 pre-v1 | 低 | 单机低延迟；高并发/大规模容量一般 | 开源，本地部署 | 高 | citeturn44view3turn43search2turn41view1 |
| Qdrant | 原生向量检索引擎；官方支持 Hybrid/Multi-Stage Queries；适合实时索引与更复杂服务端融合 | 引入独立服务，偏离当前“本地单文件”哲学；迁移与运维复杂度上升 | 中 | 高吞吐、较低延迟，适合服务化扩展 | 开源 + Cloud | 中 | citeturn44view5turn42search14 |
| pgvector | 与 PostgreSQL 一体化，支持 exact / ANN、稀疏向量、多距离函数、JOIN、ACID、PITR | 为向量检索引入 PostgreSQL 体系；若当前并不需要事务联表，收益不一定大于复杂度 | 中 | 中高吞吐；通常高于 SQLite，低于专用向量引擎的极限 | 开源 + 多托管支持 | 中 | citeturn44view4 |

**建议结论：**  
短中期不要迁库，先把 SQLite 路线做深。只有在出现以下条件之一时，才建议迁向 Qdrant/pgvector：  
其一，单实例文档规模和并发已经明显压到 SQLite；其二，你要把系统从个人/小团队本地知识库升级为共享服务；其三，你要在服务端做更复杂的多路召回和动态打分。否则，SQLite 的“简单、可本地携带、便于备份与审计”更符合 Karpathy 的个人 wiki 气质。 citeturn44view3turn43search2turn44view5turn44view4

### 检索策略比较

| 策略 | 优点 | 缺点 | 成本 | 延迟预期 | 适用场景 | 与现仓兼容性 |
|---|---|---|---|---|---|---|
| BM25 / FTS5 词法检索 | 对术语、缩写、文件名、标题、精确短语尤其稳；SQLite 已原生支持 FTS5 | 语义泛化差，跨语言与同义改写效果弱 | 低 | 低 | 规章、代码、缩写、知识页标题 | 高 | citeturn43search2turn27view4 |
| ANN / dense only | 语义理解强，适合问法变化与概念检索 | 对代号、编号、专名不稳；容易丢精确匹配 | 中 | 低到中 | 自然语言问题、概念召回 | 高 | citeturn44view2turn45view5 |
| Hybrid | 词法与语义互补；BGE-M3 官方也推荐 hybrid + rerank；Qdrant 官方支持 hybrid/multi-stage | 需要调权重、去重、融合与评测 | 中 | 中 | 通用企业知识库、中文混合语料 | 高 | citeturn44view0turn17view1turn44view5 |
| Parent-Child / 分层检索 | 子块定位准，父块补全上下文；很适合 wiki 页级与段级同时存在的场景 | 设计更复杂，评测维度更多 | 中 | 中 | 长文档、结构化 wiki、表格/PPT/PDF | 高 | citeturn27view0turn35view0 |

**建议结论：**  
当前仓库的 `blend + rerank + parent-child` 路线在方向上是正确的，甚至已经比很多常见 RAG 项目更接近“可工程化的高质量检索”。真正要做的不是推翻它，而是把这条检索管线同时服务于两类对象：**raw sources** 与 **compiled wiki pages**。前者解决出处与证据，后者解决沉淀与综合。 citeturn17view1turn35view0turn32view0

## 实验与评估计划

仓库已经具备不错的评测基础：`evals/` 下有 `datasets/`、`metrics.py`、`run_eval.py`、`run_retrieval_eval.py`，CI 中也有 `retrieval-eval` 任务，并用 `--fake-embedding` 跑基线回归门禁。这意味着你不需要从零搭评测，而应该做的是**把“检索评测”扩成“Karpathy 方法论评测”**。 citeturn22view0turn15view0

### 建议的评测层次

第一层是**检索层**。继续跟踪 Recall@k、MRR、nDCG@10、Citation Completeness、No-Answer Accuracy，并新增中文专名命中率、标题命中率、表格行级命中率、长文父块补全命中率。当前仓库已经在 README 中公开了 Recall@5、MRR、nDCG@10、No-Answer Accuracy、引用位置完整性等指标，因此扩展是自然的。 citeturn41view0turn41view1

第二层是**wiki 编译层**。这是现阶段最缺、也是与 Karpathy 最相关的评估。建议新增以下可复现指标：

| 评估对象 | 指标 | 含义 |
|---|---|---|
| source summary | Source Coverage | 新源关键信息被总结覆盖的比例 |
| entity/concept 更新 | Cross-page Update Rate | 一次 ingest 实际更新的相关页数量 |
| 结构健康 | Orphan Page Rate | 没有入链的 wiki 页占比 |
| 矛盾治理 | Contradiction Resolution Lag | 发现矛盾到被标注/修复的时间 |
| 沉淀效率 | Query Save Rate | 有价值回答被保存回 wiki 的比例 |
| 过时治理 | Stale Claim Ratio | 已被新来源推翻但未标注的 claim 比例 |

这些指标并不是项目现成已有，而是我建议你在现有 wiki/graph/operation_log 能力上补起来的“方法论级 KPI”。其目标不是测“问答像不像”，而是测“知识有没有真正累积”。这一点正是普通 RAG 与 Karpathy 式 LLM Wiki 的分水岭。 citeturn32view0turn35view0

### 建议的可复现实验流程

推荐建立三套固定实验集：

其一，**raw-doc retrieval set**。继续沿用当前 `evals/datasets` 的检索基准，验证 chunk、rerank、hybrid 权重与 parent-child 参数的变化是否退化。 citeturn22view0turn15view0

其二，**wiki-compilation set**。准备一组“跨 3-5 篇文档才能形成实体页/概念页/对比页”的合成或真人标注数据，检查 ingest 后 wiki 的目录、链接、矛盾标注、实体归档是否正确。这个集合应覆盖中文政策文档、会议纪要、PPT、表格、代码文档等与你实际业务相近的材料。其评价方式可采用“结构化 rubric + LLM-as-judge + 人工 spot check”。这类流程是对 Karpathy 模式最关键的验证。 citeturn32view0turn28view1turn28view2turn28view3

其三，**query-compounding set**。给出一批问题，要求系统先回答，再把答案/对比结论回写 `wiki/`，然后在下一轮问题中验证这些新沉淀是否缩短检索路径、减少原始文档扫描、提高 answer completeness。这个实验能直接检验“知识是否会复利”。 citeturn32view0

### 自动化脚本与 CI 建议

你现在已有的 CI 检索命令如下，建议保留它作为 PR 轻量门禁。 citeturn15view0

```bash
python evals/run_retrieval_eval.py \
  --all \
  --fake-embedding \
  --baseline evals/baselines/local.json \
  --max-regression 0.05 \
  --report json \
  --output retrieval_report.json
```

在此基础上，我建议新增两个 job：

```bash
# 夜间真实模型检索回归
python evals/run_retrieval_eval.py \
  --dataset evals/datasets/zh_mixed_retrieval.jsonl \
  --embedding-model BAAI/bge-m3 \
  --reranker BAAI/bge-reranker-v2-m3 \
  --report json \
  --output nightly_retrieval_report.json

# wiki 编译质量评估
python evals/run_wiki_eval.py \
  --sources-dir evals/fixtures/wiki_sources \
  --expected-dir evals/fixtures/wiki_gold \
  --schema-file schema/AGENTS.md \
  --report json \
  --output wiki_eval_report.json
```

对应 CI 伪代码建议如下：

```yaml
jobs:
  retrieval-eval-pr:
    if: github.event_name == 'pull_request'
    steps:
      - run: python evals/run_retrieval_eval.py --all --fake-embedding --baseline evals/baselines/local.json --max-regression 0.05

  retrieval-eval-nightly:
    if: github.event_name == 'schedule'
    steps:
      - run: python evals/run_retrieval_eval.py --dataset evals/datasets/zh_mixed_retrieval.jsonl --embedding-model BAAI/bge-m3 --reranker BAAI/bge-reranker-v2-m3

  wiki-eval-nightly:
    if: github.event_name == 'schedule'
    steps:
      - run: python evals/run_wiki_eval.py --sources-dir evals/fixtures/wiki_sources --expected-dir evals/fixtures/wiki_gold --schema-file schema/AGENTS.md
```

### 成本估算方法

如果继续使用当前本地/私有部署路线，最大的可变量通常不是存储，而是 **embedding + rerank + LLM 生成**。一个实用的成本公式是：

```text
总成本
= 初始摄取 embedding 成本
+ 增量摄取 embedding 成本
+ 查询重写成本
+ 重排序成本
+ 生成成本
- 缓存命中节省
```

若使用 OpenAI `text-embedding-3-large`，官方价为 **$0.13 / 1M tokens**；因此，假设一次初始化摄取 1000 万 tokens，单 embedding 成本约为 **$1.3**，1 亿 tokens 则约为 **$13**。如果用 BGE-M3 或 Qwen 自托管，则 API 单价可视为 0，但会转化为 GPU/CPU 推理成本，这部分在当前仓库中**未指定**。因此更合理的做法是把“token 数、embedding 调用次数、rerank 调用次数、cache hit ratio”纳入埋点，再做真实账单分析。 citeturn44view2turn25view4

## 风险与合规

最大的风险不是“模型不够强”，而是**把一个本来偏本地私有的系统，逐步接成了半云半本地，却没有同步升级治理边界**。仓库已经在这方面做了不少保护：README 强调 local-first；配置里默认 `allow_http_write: false`；MCP 写操作经过 `write_policy` 守卫；敏感字段建议走环境变量或 keyring；`operation_log` 默认保留 90 天。说明作者已经有安全意识。 citeturn41view1turn35view0turn37view2

但若你要按 Karpathy 模式把系统升级为“持续维护 wiki”，风险反而会增加，因为系统不再只是“索引”，而是会“生成、改写、发布”。特别是 `config.example.yaml` 里 `wiki.auto_publish: true` 这个默认值，在企业或团队环境中偏激进。建议默认改成“自动编译、非自动发布、人工审批后发布”。 citeturn35view0

### 风险矩阵

| 风险 | 触发方式 | 当前缓解 | 建议补强 |
|---|---|---|---|
| 数据泄露 | 接入云端 embedding/LLM；误开 HTTP 写；自动发布 | 本地优先、`allow_http_write=false`、密钥建议走 keyring/env | 增加目录级脱敏、源目录 allowlist、发布审批、租户隔离 |
| 版权风险 | 将受限原文大段转写入 wiki 或静态站点 | 当前未见显式版权策略，`wiki.auto_publish=true` 风险偏高 | 为每页维护 source metadata；默认只摘要不复刻原文；静态发布前审阅 |
| 幻觉与过度综合 | `ask` 生成阶段把低置信召回合成事实 | 当前有 rerank、source_graph、citations | 增加“不足证据禁止综合”提示模板、claim-level citation 校验 |
| 过时信息 | 新来源已导入，但旧 wiki 页未刷新 | 有增量索引与 operation log，wiki lint 默认未启 | 启用 nightly stale-claim lint |
| 错误回写 | 将糟糕答案沉淀进 wiki，造成“错误固化” | 存在 `save_to_wiki` 痕迹，但流程未标准化 | 加 review queue、confidence threshold、source count threshold |
| 配置分裂 | README/迁移文档/代码默认行为不一致 | 已有文档说明，但存在冲突 | 文档单源化，自动校验 README 与默认配置一致 |

这里值得特别强调的是：Karpathy 方法论很适合“不断往里加知识”，但一旦进入企业知识资产环境，就必须把“写入与发布”做成**有门的流水线**。也就是说，检索可以开放，编译可以自动，发布与覆盖旧结论则要有门槛。当前仓库已经有写策略基础，正适合在这上面继续加固，而不是另起一套治理系统。 citeturn32view0turn37view2turn35view0

## 可直接落地的交付物

如果你准备把这份报告转成工程任务，我建议第一阶段交付不要超过四周，且聚焦“让系统真正变成 LLM Wiki”。可直接产出的清单如下。

### 改进任务清单

| 交付物 | 说明 |
|---|---|
| `wiki-first` 模式设计文档 | 定义 raw/wiki/schema 三层目录、职责与 CLI 流程 |
| `schema/AGENTS.md` | 从现有 `CLAUDE.md` 派生一份面向知识维护的 schema，写清 page types、ingest/query/lint/playbook |
| `wiki/index.md` 生成器 | 每次 ingest 自动更新内容目录 |
| `wiki/log.md` 生成器 | 记录 ingest/query/lint 时间线 |
| `run_wiki_eval.py` | 新增 wiki 编译质量评估器 |
| `save_to_wiki` 标准化接口 | 将高价值问答沉淀到 `comparisons/` 与 `syntheses/` |
| `lint_wiki.py` | 孤儿页、矛盾、过时 claim、缺失 backlinks 检查 |
| 维度迁移脚本 | 把 1024 维假设改成配置化、可迁移 |
| 文档一致性修正 PR | 统一默认 tool profile、默认工作流与部署文案 |

### 可直接使用的 schema 草案

```markdown
# AGENTS.md

## Source of truth
- raw/ 下所有文件只读，不允许 agent 直接修改
- 所有综合结论必须可追溯到 raw/ 或已有 wiki 页

## Page types
- wiki/sources/*.md: 单源摘要页
- wiki/entities/*.md: 实体页
- wiki/concepts/*.md: 概念页
- wiki/comparisons/*.md: 对比页
- wiki/syntheses/*.md: 综合页

## Ingest workflow
- 读取新源
- 生成 source summary
- 识别需要更新的 entities/concepts
- 更新 wiki/index.md
- 追加 wiki/log.md
- 若存在与旧结论冲突，必须显式标注

## Query workflow
- 先读 wiki/index.md
- 再读相关 wiki 页
- 证据不足时回到 raw/ 检索
- 高价值回答可保存为新 wiki 页
```

### 监控仪表盘建议

建议至少做四块面板：

一块是**索引面板**：摄取速率、失败率、单文件耗时、解析器失败分布、hash 去重命中率。因为当前仓库已有增量路径、watcher 与内容去重能力，这类指标非常容易补齐。 citeturn17view3turn27view2turn31view1

一块是**检索面板**：P50/P95 延迟、top_k 命中率、rerank fail rate、cache hit rate、No-Answer rate。embedding 缓存和分阶段 timeout 都已存在，做可观测性回报很高。 citeturn25view4turn35view0turn37view2

一块是**wiki 健康面板**：新源数、被更新页数、孤儿页数、缺失 backlinks、矛盾页数、过时 claim 数。这个面板将是你判断“系统有没有真正走上 Karpathy 路线”的核心。 citeturn32view0turn35view0

一块是**成本面板**：embedding tokens、rerank 请求数、LLM 生成 tokens、缓存命中节省、按 provider 分摊的费用。若后续接 OpenAI 托管模型，这块会非常关键。 citeturn44view2turn25view4

### 最终落地判断

如果只用一句话概括：  
**这个仓库已经拥有实现 Karpathy “LLM Wiki” 的工程底座，但当前默认产品形态仍是 RAG/MCP 检索引擎；最优路径不是换库换模，而是把现有 wiki/schema/graph 能力上提为主工作流，并以 `index.md`、`log.md`、query 回写和 lint 闭环补齐“知识累积层”。** citeturn32view0turn40view0turn35view0turn41view1

在具体执行上，我建议你把路线分成两步：先在现有 SQLite + BGE-M3 + Hybrid/RRF + rerank 基座上完成 **wiki-first 化**；等这一步稳定后，再决定是否需要迁移到更重的嵌入模型或更复杂的向量库。这样做，既能最大化复用现有仓库价值，也最符合 Karpathy 所说的那种“知识越用越厚、而不是每次都从头拼”的系统哲学。 citeturn45view5turn17view1turn44view3turn32view0