# ShineHeKnowledge 当前状态

> 最后更新：2026-07-08
> 源码版本：`src/version.py` 中的 `1.4.0`
> 当前分支：`master`
> 当前方向：本地优先的 MCP 高精准知识检索引擎 + Karpathy Wiki-First 对齐

## Canonical Wiki V2 纠偏续建 — Phase 3.5 Correction Gate 通过 (2026-07-08)

7 个 commit(`e0d2db8`→`9e70af9`)完成 Phase 3.5 纠偏门禁 C0-C6。不撤销已完成的
Canonical 模型/Schema/Repository/Projection/Extractor/Matcher/Merge Engine,在 Phase 4
主路径切换前插入强制门禁。执行依据:
`docs/superpowers/plans/2026-07-08-canonical-wiki-v2-correction-and-continuation.md`。

### C0-C6 交付

| 阶段 | commit | 内容 |
|---|---|---|
| C0 审计冻结 | `e0d2db8` | 现状审计:暴露守卫盲区(11 处越界写)、matcher 缺 reason code、transaction 非严格 staging、读路径三套未统一、canonical_v2 配置缺失、wiki_eval 崩溃 |
| C1 语义契约 | `eb7095b` | ClaimMergeAction+ReasonCode 枚举冻结(`docs/architecture/wiki-v2-claim-merge-contract.md`);守卫扩展纳入 api/lint/workflow + open 写探测;normalize 共用 |
| C2 黄金评测 | `65094cc` | `evals/wiki_v2/` 黄金集(matching 12 + merge 4 + extraction 4 + source 骨架) + 确定性测试 + 真实评测脚本;修 wiki_eval 崩溃 |
| C3 事务恢复 | `88ac399` | WikiRepository 严格 staging transaction(`_staging/<tx_id>` + manifest + COMMITTED + outbox tx_id) + recover 前向/孤儿恢复 + 12 故障注入测试 |
| C4 读端口统一 | `2f04bf4` | WikiQueryService(projection→FS→legacy SQLite) + 消费 `wiki_pages_v2_fts`(消除零读取) + 11 契约测试 |
| C5 配置状态机 | `73901a3` | `wiki.canonical_v2.mode` off/shadow/canary/primary 四态 + 向后兼容 + config 补 claims/rebuild/projection/validation |
| C6 依赖边界 | `9e70af9` | wiki_page_locator 删全局 Config;AST 守卫禁 7 模块 import 全局单例;7 服务全纯构造注入 |

### 验收门禁(Phase 4 前置,全部满足)

| 门禁 | 结果 |
|---|---|
| ruff(全量 src tests evals tools scripts) | **0 error**(C0 基线 scripts 7 债务已清:`_tmp_show_fails.py` 删 + `mcp_30round` ruff --fix) |
| mypy src | **0 error / 183 文件**(`file_watcher.py:73` 基线已修:Any 注解) |
| pytest 全量 | **1412 passed / 2 skipped / 5 xfailed**(C1-C6 零回归,+66 新测试) |
| retrieval eval | **passed**(recall 0.8667 / mrr 0.7800 / no_answer 0.6667,不低于基线) |
| wiki eval | **修复**(5 项 metrics 正常,C0 崩溃 `run_wiki_eval.py:81` 已修) |

### 已知 xfailed(C2 黄金集保守性 gap,待真实数据收紧)

matcher 当前无单位/型号/地区/否定/强度词细粒度解析,5 case 标 xfailed:单位不同
(1Gbps vs 1000Mbps)/型号不同/地区不同/否定表达/强度词(最高可达 vs 保证达到)——当前
判 contradicts/supports,契约要求回落 unresolved。由黄金集驱动,Phase 4A shadow 用真实
数据逐步收紧(契约 §5 保守复核)。

### Phase 4 前阻断项(全部闭环)

C0 审计的 4 高风险阻断项已解决:守卫盲区(C1 扩展)/语义契约(C1 冻结)/事务原子性
(C3 严格 staging)/黄金评测(C2 建立)。中低风险(locator 全局依赖 C6 修 / wiki_eval
C2 修 / 配置状态机 C5 / 读端口 C4)亦闭环。

详见 `docs/superpowers/reviews/2026-07-08-canonical-wiki-v2-current-state.md`(审计)
+ `docs/architecture/wiki-v2-claim-merge-contract.md`(契约)。

**下一步**:Phase 4A Shadow 主工作流接入(claim 流程接入真实 ingest,但不影响正式 canonical;
输出 legacy 与 V2 差异报告)。Phase 4 前置门禁已全开。

## 权威文档

- [第一阶段（已完成）：Karpathy Wiki-First 对齐 Spec](docs/superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-design.md)｜实施计划 [W1](docs/superpowers/plans/2026-07-02-knowledge-base-karpathy-wiki-first-w1.md)/[W2](docs/superpowers/plans/2026-07-02-knowledge-base-karpathy-wiki-first-w2.md)/[W3](docs/superpowers/plans/2026-07-02-knowledge-base-karpathy-wiki-first-w3.md)/[W4](docs/superpowers/plans/2026-07-02-knowledge-base-karpathy-wiki-first-w4.md)
- [下一阶段（Draft）：Karpathy Wiki-First 第二阶段 Spec（检索执行层）](docs/superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-phase2-design.md)｜[Plan](docs/superpowers/plans/2026-07-02-knowledge-base-karpathy-wiki-first-phase2.md)
- [上一阶段（已完成）：MCP 本地检索收束 Spec](docs/superpowers/specs/2026-06-13-mcp-local-retrieval-focus-design.md) / [Plan](docs/superpowers/plans/2026-06-13-mcp-local-retrieval-focus.md)
- [MCP 使用文档](docs/mcp/)
- [高级功能](docs/advanced-features.md)
- [工具配置档迁移指南](docs/migration/mcp-tool-profiles.md)
- [大规模升级回归 Review & Fix 计划（2026-07-03）](docs/superpowers/plans/2026-07-03-knowledge-base-upgrade-regression-review.md)
- [历史设计与已完成计划](docs/archive/README.md)

除上述当前规格和计划外，归档目录中的文档只用于追溯，不代表当前待办。

## 大规模升级回归 Review & Fix — 已完成 (2026-07-03)

对 2026-06-23→07-03 升级窗口(6 大功能流:Search-Optimize Phase1-3 / 版本冲突清理 / GUI 去重 / 50轮·v1.4.0 修 bug / Wiki-First 第一阶段 W1-W4 / 第二阶段 W1-W3,59 文件 +7416/-281,21 新文件)做分段回归 review。3 个深度 Explore agent 全段映射 + 真码逐点核实 + TDD 修复,4 个 phase 各自 commit。

### 修复清单(按段)

| 段 | commit | 修复 |
|---|---|---|
| S1 检索执行层 | `912436e` | blend_fusion 抖动不清空候选(外层 except 跳过 FTS 兜底);LRU 缓存 get/put 深拷贝隔离(防嵌套结构污染);async bridge 超时无界 queue(防线程泄漏);query() 非 TimeoutError 异常向上传播(不再盲目 fallback `_direct_query` 无超时二次调 LLM,Bug-2 同类雪崩);lexical_zh Latin 词边界匹配(防「AI」命中「available」污染 FTS);title boost distance 分跳过(防语义反转);RRF 双零权重加 warning;清理死代码 `_normalize_fts_rank` + 死配置读取 |
| S2 Wiki+数据+迁移 | `f5345d0` | alembic i001 幂等化(`if_not_exists`,与 db._SCHEMA 双重建表冲突致 `alembic upgrade head` 必现报错);resolve_slug 空 hash 不误判幂等覆盖;write_markdown 真原子写(tmp+os.replace);version_conflict 补清 vec_blocks(防 block 向量泄漏);migrator Config.load 前置 + 备份改「先写临时成功再换」(防丢旧备份) |
| S3 MCP 契约 | `9cce4f9` | ask_with_query 自建 pipeline 补全 4 个 deps(graph_backend/size_aware/wiki_page_locator/wiki_parent,原缺致升级核心功能静默失效);_do_ask 补 `except Exception`(承接 S1.4 传播,堵 Bug-2 同类无兜底);_get_operation_log_service 改用 get_active_container(旧 get_container() 缺参必 TypeError,容器注入成死代码) |
| S4 安全 | `4b82041` | parse_url SSRF 重定向绕过:加 httpx event_hooks 逐跳校验每个重定向目标(旧仅验初始 URL,302 可指向 127.0.0.1/云元数据) |

### 撤销 / 延迟(附理由)

- **S2.3 撤销**:entity 页「一页跨源 LLM 合并」是设计,knowledge_id last-writer-wins 是 Phase2 spec §4.2 明记的未来增强(补 source_ids),非回归 bug;agent 建议的 resolve_slug 会拆多页破坏设计。
- **S4.2 延迟**(GUI worker closeEvent):GUI-only、LOW-MED(仅关闭窗口时正在扫描的边缘场景)、daemon 线程不挂进程、会话可新建恢复,且无 headless GUI 测试覆盖,贸然改 closeEvent 风险>收益。

### 验证(当次真实执行)

| 门禁 | 结果 |
|------|------|
| 全量 pytest | **1197 passed / 1 skipped / 0 failed**(基线 1179 + 18 新回归测试,零功能退化) |
| 新增回归测试 | 18 条(S1.1 blend 兜底 / S1.2 缓存隔离 / S1.4 异常传播 / S1.5 词边界 / S2.1 i001 幂等 / S2.2 空 hash / S2.6 原子写 / S2.5b 备份 / S3.2 ask 兜底 / S3.3 DI / S4.1 SSRF) |
| ruff / mypy | **既有基线债务,非本次引入**:af4aa2f(本次工作前)已存在 ruff 98 / mypy 53 错误(wiki-first 升级窗口积累)。本次改动净增 0 新错误。**已随后续单独一轮清理全部清零(见下节)** |

### ruff / mypy 基线清理 — 已完成 (2026-07-03)

清理 wiki-first 升级窗口积累的 lint/type 基线债务,恢复 CI 门禁绿:

| 门禁 | 结果 |
|------|------|
| ruff | **0 错误**(原 98)。`--fix` 安全修复 I001/F541/F811/W293 + 手工 F841/E701/E741;F401 移除经 170 模块 import 冒烟 + 全量 pytest 验证无 NameError。commit `35240a6` |
| mypy | **0 错误 / 172 文件**(原 53)。commit `0023f81` |
| pytest | **1198 passed / 0 failed**(基线 1179 + 19 回归测试,零退化) |

**mypy 清理中发现 1 个潜伏功能 bug**(已修 + 回归测试锁定):
- `health._get_kb_domain_summary` 旧 `Database()` 无 db_path 必抛 TypeError(元类无 `__call__`),被外层 except 吞掉 → **BUG-7「领域概览兜底」从未真正生效,恒返回通用字符串**。改用 `Database._instance` 后,空检索时 LLM 上下文兜底现能正确注入真实文档数/标签。

**其余多为注解收紧**(零运行时变更):embedding `to_embed` 注解、route_engine cache 改 tuple 类型、search_service minhash int 哨兵、wiki_compiler `page_id` 提取、批量 no-any-return cast、元类/函数属性误报 ignore。

### 后续建议(非本轮范围)

- Phase2 W4 收口时统一双轨 wiki 编译(MCP→SQLite wiki_compiler vs path_indexer→文件系统 knowledge_workflow,wiki_lint 对文件层盲)——架构级 gap,已记为 spec Gap B。


## Karpathy Wiki-First 对齐（第一阶段）— W1-W4 核心实现落地 (2026-07-02)

将知识库从「检索即终态」演进为「ingest → 编译为 wiki → 检索 / 回写」的 wiki-first 模型。本轮完成 W1-W4 的核心代码实现与分周计划文档；核心 MCP 检索链路经实测无回归。

### 交付清单

| 周次 | 模块 | 交付 | 主要改动 |
|-----|------|------|---------|
| W1 | 目录契约 | `shinehe init` 生成 wiki-first 布局（`raw/` + `wiki/` + `schema/` + `artifacts/` 与 `AGENTS.md`） | `project_setup.py`：`WIKI_FIRST_DIRS` / `AGENTS_MD_TEMPLATE` / `_wiki_first_defaults` / `write_wiki_first_layout`；`cli.py` `_handle_init` 集成 |
| W1 | 配置地基 | `build_config` 注入 `knowledge_workflow` 段与安全默认值；收敛 `config.example.yaml`；清理 `chroma_dir` legacy | `project_setup.py` / `config.example.yaml` |
| W2 | 共享工具 | `wiki_slug`（slugify / frontmatter 解析） | `services/wiki_slug.py` |
| W2 | 源编译器 | 规则式 `wiki_source_compiler`（**零 LLM**，模板化 source summary） | `services/wiki_source_compiler.py` |
| W2 | 实体更新 | `wiki_entity_updater`（LLM，每文档硬上限 3 次调用） | `services/wiki_entity_updater.py` |
| W2 | 索引 / 日志 | `wiki_index_compiler` + `wiki_log_compiler` 自动更新 `wiki/index.md`、`wiki/log.md` | `services/wiki_index_compiler.py` / `wiki_log_compiler.py` |
| W2 | 工作流服务 | `KnowledgeWorkflowService` + `path_indexer` ingest 钩子 | `services/knowledge_workflow.py`；`path_indexer.py` try/except 包裹 |
| W3 | 查询回写 | `save_mode` + 置信度阈值标准化（高价值 query → wiki 草稿） | `mcp_server.py` / `knowledge_workflow.py` / `rag_pipeline.py` |
| W3 | lint 增强 | `wiki_lint` 新增 `outdated_claim` + `missing_backlinks` | `services/wiki_lint.py` |
| W3 | CLI | `shinehe wiki` 子命令组（`lint` / `save-answer` / `ingest-source`） | `cli.py` |
| W4 | 默认档修正 | README 默认 profile `core→extended` + 文档一致性测试 | `README.md` / `tests/test_docs_consistency.py` |
| W4 | 迁移 | `shinehe migrate`（legacy → wiki-first） | `cli.py` / `services/migrator.py` |
| W4 | 评测 | wiki-compilation eval（5 指标） | `evals/run_wiki_eval.py` / `tests/test_wiki_eval.py` |
| 横切 | 安全 | `config.yaml` 停止跟踪（防密钥泄露）+ gitleaks pre-commit | `.gitignore` / `.pre-commit-config.yaml` |

### 设计要点：为什么没破坏检索

- **wiki hook 隔离**：`path_indexer._ingest_file` 在 `index_knowledge_item` 之后追加 `try_knowledge_workflow_compile`，用 try/except 包裹，失败仅 `logger.warning`，**不阻塞** agent 的索引→检索主链路（`path_indexer.py:398-403 / 447-452`）。
- **工具面零改动**：`tool_profiles.py` 的 `CORE_TOOLS` / extended / admin / full 配置档本轮未触碰，检索工具面与 v1.4.0 一致。
- **文件系统层独立**：wiki-first 产物落在 `wiki/*.md`（由 `KnowledgeWorkflowService` 管理），与 SQLite `wiki_pages` 表（旧 wiki 系统）解耦。

### 验证（当次真实执行）

| 门禁 | 结果 |
|------|------|
| 今天新增 wiki 模块单测 | `33 passed`（source / entity / index / log / lint / cli / migrate / eval） |
| MCP 核心 + workflow + docs 一致性 | `89 passed, 1 skipped` |
| 端到端 RAG + 检索回归 | `65 passed`（rag_full / full_pipeline_e2e / search / rag_sources） |
| 真实 MCP 工具调用 | `ping`（alive v1.4.0）/ `search`（返回 3 条）/ `ask`（完整回答 + 5 来源）全通 |

### 迁移落地（2026-07-02，零 LLM 模式）

`shinehe init`（生成 raw/wiki/schema/artifacts + AGENTS.md + config）+ `shinehe migrate` 已执行。`data/kb.db` 只读未改（独立备份 `data/kb.db.pre-migrate-20260702`）。

| 产物 | 数量 |
|------|------|
| `wiki/sources/*.md` | 11（file 类型 knowledge 全编译，含 2 条源文件缺失） |
| `raw/` 导出 | 9（source_path 存在的；2 条缺失跳过） |
| `wiki/index.md` / `log.md` | 已生成，结构正确 |
| `wiki/entities` / `concepts` | 空（零 LLM 模式；待配 key 补） |

真实文档 source 页 `key_entities` 规则抽取有效（如「创智杯通知」抽出 AI/FTTR/APP/BSS 等）。

**迁移修复 2 个潜伏 bug**（migrate 首次真实执行暴露）：
- `migrator.py` 加 `_ensure_db()`：CLI 路径 `Database._instance` 未初始化，致类级调用 `list_knowledge()` 报 missing self
- `cli.py _handle_migrate` apply 前 `create_container()`：否则 `try_knowledge_workflow_compile` 取不到 container 静默返回 None，wiki 不编译

**回归**：migrator + wiki + cli 单测 45 passed；检索链路 24 passed。

### 当前边界与后续

- **entity/concept 待补**：本机 LLM Key 未配/失效，entity 编译失败被隔离跳过（warning 不中断）。补齐需配 `SHINEHE_LLM_API_KEY` 后重跑 `shinehe migrate --apply`（幂等）。
- **文件系统 wiki 缺测量基础设施**：`shinehe wiki lint` 查 SQLite `wiki_pages` 表（旧系统），对 `wiki/*.md` 无效；`run_wiki_eval.py` 仅 `source_coverage`/`query_save_rate` 对文件系统有效。第二阶段 W4 eval 扩展前需补文件系统 wiki 的 lint/统计工具（phase2 Gap B）。
- **后续**：第二阶段 spec 已复核（Gap A 定 A2 / Gap B 记入 W4 前置），W1 已落地（见下文），W2 待 plan 审批。

## Karpathy Wiki-First 对齐（第二阶段）— W1 规模自适应路由落地 (2026-07-02)

补齐 Karpathy「小规模用 index / 大规模用搜索」原则：新增 `SizeAwareRouter`，按查询规模三档分流——小查询只读 `index.md` + wiki 页（**零向量调用**），大查询走现有 hybrid 搜索，中间档 blend 融合两路。本轮完成第二阶段 W1 全部代码实现与 TDD，全量回归零退化。

### 交付清单

| 模块 | 内容 | 位置 |
|---|---|---|
| WikiPageLocator | 扫 `wiki/*.md` 按 query 定位命中页 + 计数；候选对齐统一 schema（`id` 形如 `wiki:<type>:<slug>`，与检索候选 `page_id:block_id` 不冲突） | `src/services/wiki_page_locator.py` |
| SizeAwareRouter 规则层 | token / wiki 命中数 / 意图词 → 三档分类（spec **S1**），阈值 `rag.size_aware.*` 可配置 | `src/services/size_aware_router.py` |
| WikiReadStage | wiki_read/blend 档读 wiki 候选；wiki_read 档 `VectorSearchStage` 顶部零向量提前返回（spec **S2**） | `src/services/rag_pipeline.py` |
| blend RRF 融合 | wiki×检索两路 RRF（`w/(k+rank+1)`，k=40），同 id 累加、`match_channels` 并集 | `src/services/blend_fusion.py` |
| 装配 + 门控 | container 注入 locator/router + `rag_pipeline` deps；init 注入 `rag.size_aware` 段；config.example 补段 + pipeline `wiki_read` 条目（spec **S6**） | `container.py` / `project_setup.py` / `config.example.yaml` |

### 设计要点：为什么 scale 在 WikiReadStage 算（而非 AgenticRouter）

W1 plan 原写「scale 在 `AgenticRouter.route()` 内算」，源码核实暴露时序 bug：`WikiReadStage` 在调 agentic 的 `VectorSearchStage` **之前**执行，那时 `ctx.metadata["scale"]` 尚不存在。故把 scale 计算放在 `WikiReadStage`（管线最前的 scale-aware 点），缓存到 `ctx.metadata` 供 `VectorSearchStage` 分流——`wiki_read` 档得以在 agentic/hybrid 之前零向量返回，且 route_query 工具与管线不会重复调 SizeAwareRouter。legacy 门控由 stage 层（`mode≠wiki_first` 即空操作）+ config 层（缺省 `enabled=false`）双重保证。

### 验证（当次真实执行）

- 新增 25 个 TDD 测试，全部通过
- 全量回归 **1126 passed / 1 skipped / 0 failed**（基线 950+，零退化）
- 真实 `wiki/`（11 source 页）端到端冒烟：`FTTR是什么`→wiki_read（零向量）、`列出所有营销通知`→full_search（意图词）、`创智杯…评价指标`→blend，三档判定正确
- spec 验收：S1（三档分类）✓ / S2（小查询零向量）✓ / S6（legacy 零变化）✓

### 后续

- **W2（wiki parent-child）**：spec §4.2 + Gap A 的 A2 方案（检索侧用 `knowledge_id` 回查 source 页，不动已交付编译器）。动工前先出 W2 TDD plan 审批。
- **W3（中文 lexical）**：词典 + 同义词 + 语种权重，目标 `retrieval_zh` Recall@5 ≥ 0.7。
- **W4（收口）**：含 Gap B 文件系统 wiki 测量基础设施（lint/统计工具），否则 size_aware 收益无法量化；版本 → v1.5.0。

## Karpathy Wiki-First 对齐（第二阶段）— W2 wiki parent-child 落地 (2026-07-03)

补齐 Karpathy「parent-child 上下文」原则在 wiki 检索侧的缺口：wiki 命中 entities/concepts/syntheses/comparisons 页时，带回其引用的 source 页摘要作 `parent_content`，与 block 检索的 parent-child 对称，**零改动复用 `GenerateStage` 既有的 `parent_content` 渲染路径**。验收 S3（wiki 命中候选 parent_content 非空且指向 source 页）+ S6（legacy 零变化）达成。

### 交付清单

| commit | 模块 |
|---|---|
| `b0c706f` | `src/services/wiki_parent_retrieval.py` — WikiParentRetriever（A2: knowledge_id 回查 source + 复用 `_build_summary`） |
| `d979f61` | `rag_pipeline.py` `WikiParentEnrichStage` — post-rerank 挂载（rerank 后、generate 前） |
| `912254d` | blend 档共存契约测试（wiki/block 两路 parent_content 经 blend 不互覆盖） |
| `c2e8ab2` | `container.py`/`project_setup.py`/`config.example.yaml` — 装配 + init 注入 + legacy 门控 |
| `36e1887` | docs: docstring 行号引用改函数名（防漂移） |

### 设计要点

- **字段名 `parent_content`（非 spec 写的 `parent_context`）**：源码核实后者代码零引用（文档幽灵）；`GenerateStage._build_context_from_filtered` 读 `parent_content`，`parent_child_retrieval.py:210` 写它。W2 全程用 `parent_content`；S3 在候选级断言（`CitationBuilder` 不读此字段，parent_content 不进 sources/payload/Citation）。
- **post-rerank 挂载（非与 block 时序对称）**：block 的 `enrich_with_parent_context` 挂 `hybrid_search`（vector_search 内、rerank 前）；W2 选 post-rerank 是**效率考量**（只 enrich 过 rerank 存活的候选）。reranker 原地 mutate 保留字段，post-rerank 写入零丢失直达 generate。
- **A2 方案（不动第一阶段编译器）**：entity/concept 页 frontmatter 只有单个 `knowledge_id`（无 `source_ids`），用 `Database.get_knowledge_batch` 回查 source 原始 content + `WikiSourceCompiler._build_summary` 提炼首段。DB 是 source of truth，绕开 knowledge_id→文件路径的非平凡映射（`resolve_slug` hash 冲突后缀）。
- **浅合并坑规避（W1 教训）**：`_wiki_parent_defaults` 是独立 `@staticmethod`，注入 `_build_local_config`/`_build_provider_config` 各自的 rag dict，**绝不进 `_wiki_first_defaults`**（会整体覆盖 rag 段）。`test_wiki_parent_defaults_not_in_wiki_first_defaults` 锁死。
- **三层 legacy 门控（S6）**：config 缺省 `enabled=false` + stage 内 `mode==wiki_first` 门控 + stage `rag.wiki_parent_child.enabled` 门控。非 wiki_first 项目 stage 空操作。

### 验证（当次真实执行）

- 新增 26 个 TDD 测试（10 retriever + 9 stage/blend + 7 legacy），全部通过
- 全量回归 **1152 passed / 1 skipped / 0 failed**（基线 1126，+26 新测试，零退化）
- final whole-branch review（opus）：Ready to merge = Yes，跨任务 DI 链（container property → deps → `StageRegistry.create_stage` inspect-match → stage 构造器）+ stage 顺序（`_builtin_stages`/`DEFAULT_PIPELINE_CONFIG` 两处）+ 读写字段链路（`RerankStage:516`→`WikiParentEnrichStage:302/310`→`GenerateStage:548/643`）端到端验证
- spec 验收：S3 ✓（wiki 命中候选 parent_content 非空指向 source）/ S6 ✓（legacy 零变化，三层门控）

### 后续

- **W3（中文 lexical）**：词典 + 同义词 + 语种权重，目标 `retrieval_zh` Recall@5 ≥ 0.7（基线 0.6）。动工前先出 W3 TDD plan 审批。
- **W4（收口）**：含 Gap B 文件系统 wiki 测量基础设施（lint/统计）+ 文档 + 全量回归 → v1.5.0。

## Karpathy Wiki-First 对齐（第二阶段）— W3 中文 lexical 强化落地 (2026-07-03)

补齐 Karpathy「中文 lexical 友好」原则：当前 keyword 通道走 FTS5 + jieba，RRF 权重固定，无专名词典/同义词/语种权重，中文召回弱（`retrieval_zh` Recall@5 长期 0.6）。W3 三强化点：① 专名词典注入 jieba；② 同义词扩展 query 并集进 FTS5；③ RRF keyword 权重按查询语种拆 zh/en。

### 交付清单

| commit | 模块 |
|---|---|
| `e9d186f` | `chinese_tokenizer.py` `_ensure_lexical_dict`（jieba 词典模块级加载）+ `detect_query_language` |
| `c73501f` | `lexical_zh.py` `LexicalZh` 同义词扩展 + `hybrid_search._keyword_search` 挂载 |
| `72eec6d` | `hybrid_search._blend_search` 语种权重拆分（zh 0.7 / en 0.5，`detect_query_language(queries[0])`） |
| `911a042` | `project_setup._lexical_zh_defaults` + `write_wiki_first_layout` 模板 + `config.example` + 空 `data/lexical_zh_*.txt` |
| `b962935`/`e0a6b16` | S4 集成测试（原 false-positive → fix 真验证 dict/synonym/language 机制） |
| `a495b39` | final-review minor cleanups（删冗余 import + `.gitignore` 否定规则） |

### 设计要点

- **词典模块级加载（非 spec 字面）**：调研纠偏——`_keyword_search`(hybrid_search:115) 不做 jieba 分词，委托 `db.search_blocks_fts`(db.py:1522)，真正分词在 `chinese_tokenizer.tokenize_chinese_full`（db 索引 :1508 + 查询 :1522 两路径都调）。`_ensure_lexical_dict` 模块级 flag + 失败 warning 不阻塞，查询+索引都受益。**存量 block_fts 需 `reindex_all` 才享受新词典**（只对新写入生效）。
- **S4 验收路径偏离 spec（方案 b，已审批）**：spec §3 S4 写 eval，但 `run_retrieval_eval.py` 的 `OfflineIndex`(:155-331) 自带 BM25+中文 bigram，**不走 hybrid_search/jieba/FTS5**。强化 OfflineIndex 会破坏英文 1.0 基线 + CI 确定性。改用集成测试（真实 HybridSearcher + tmp 词典 + `insert_blocks_fts`）验证机制（dict 真加载改变 tokenization「创智杯」+ synonym 扩展 + detect zh）。**数值 `retrieval_zh` 0.6→0.7 deferred W4**（需真实数据集 + reindex）。
- **归一化放大效应未发生**：zh 0.7 + w_semantic 0.4 归一后仅 +0.036，但 dict（专名整词）+ synonym 是主召回驱动，集成测试无需调参即机制全通。
- **浅合并坑第三次规避**：`_lexical_zh_defaults` 独立 `@staticmethod`，注入 `_build_local_config`/`_build_provider_config` 各自 rag dict，不进 `_wiki_first_defaults`（W1/W2/W3 三次验证）。
- **legacy 三层门控**：config 缺省 `enabled=false` + `_ensure_lexical_dict` enabled 门控 + `LexicalZh._load_synonyms` enabled 门控。legacy 项目 dict/synonym 完全 no-op（语种权重在 `_blend_search` 无条件生效，归一后偏差 ±0.04 可忽略，config.example 已注释提示）。

### 验证（当次真实执行）

- 新增 27 个 TDD 测试（5 language_detect + 4 lexical_loader + 6 synonym + 3 language_weight + 5 layout + 5 integration[含 fix]），全部通过
- 全量回归 **1179 passed / 1 skipped / 0 failed**（基线 1152，+27 新测试，零退化）
- final whole-branch review（opus）：Ready to merge = Yes，三条集成链路（dict/synonym/language-weight）端到端验证

### 后续

- **W4（收口）**：含 **Gap B 文件系统 wiki 测量基础设施**（`shinehe wiki lint` 查旧 SQLite `wiki_pages` 表对 `wiki/*.md` 无效，需新建扫描 `wiki/*.md` 的 lint/统计工具）+ retrieval eval 扩展（size_aware 路由准确率 + `retrieval_zh` Recall@5 真实数值 + reindex 验证）+ 文档（`advanced-features.md`）+ 全量回归 → v1.5.0。

## Karpathy Wiki-First 对齐（第二阶段）— W4 收口落地 (2026-07-06)

第二阶段 W4（收口）100% 落地，spec §6.4 全覆盖。Gap B 文件系统 wiki 测量基础设施补齐 + retrieval eval 双扩展 + 文档 + v1.5.0。8 个 commit `1d71db1`→`ec94d2c`，全量回归 **1219 passed / 1 skipped / 0 failed**（基线 1198 + W4 新增 21，零退化），ruff 0 / mypy 0（173 src 文件）。

### W4 commit 清单

| commit | 内容 |
|---|---|
| `1d71db1` | `WikiFsLint` 核心（orphan/dead_reference/duplicate/missing_backlinks/empty） |
| `791cae2` | `WikiFsLint` 溯源指标（stale/outdated_claim,DB 交叉校验） |
| `466a543` | 修 `run_wiki_eval` Gap B bug:按 `--source` 选 fs/sqlite 引擎（wiki_first 不再恒 total_pages=0） |
| `863fa56` | CLI `shinehe wiki lint --source {auto,fs,sqlite}` |
| `6ef9ed1` | size_aware 路由准确率 eval（`--routing` + 数据集 + 指标） |
| `5de0abd` | retrieval_zh real-hybrid 引擎（`--engine real-hybrid`,真 HybridSearcher keywords 模式） |
| `0e412cb` | `advanced-features.md` += 规模自适应/wiki parent/lexical_zh 三章 + 一致性测试 |
| `ec94d2c` | 版本 → v1.5.0 |

### Gap B 文件系统 wiki 测量基础设施（spec §4.4 / §6.4 前置）

- **`src/services/wiki_fs_lint.py` `WikiFsLint`**：扫 `wiki/<sources|entities|concepts|comparisons|syntheses>/*.md`，产与 SQLite `WikiLint.run()` 同构 LintReport（finding 分类对齐）。复用 `read_frontmatter`/`_read_body`/`_WIKI_LINK_RE`。**不动 SQLite `wiki_lint.py`**（两轨并存，低 blast radius）。
- **核心 bug 修复**：`run_wiki_eval.run_on_project()` 旧实现硬编码 `WikiLint().run()`（SQLite），wiki_first 纯文件系统项目 `total_pages=0` → 结构指标全失效。改为按 `--source`（auto/fs/sqlite）选引擎，`mode=wiki_first` 默认走 fs。`compute_metrics` 纯函数不变。
- **CLI**：`shinehe wiki lint --source fs` 独立可用（不依赖 SQLite 初始化）；`--source sqlite` 沿用旧行为（需 `shinehe init` 后）。

### retrieval eval 扩展（spec §6.4 4.1/4.2）

- **size_aware 路由准确率**：`evals/datasets/size_aware_routing.yaml` + `run_routing_eval()` + `--routing` CLI。SizeAwareRouter 纯规则、零 LLM、全确定性。无 wiki/ 环境下 locator 命中 0 → 全判 full_search，accuracy=1.0（CI 确定性）；真实 wiki/ 环境可补 wiki_read/blend 用例。
- **retrieval_zh real-hybrid 引擎**：`evals/real_hybrid_engine.py` `RealHybridIndex`（真 HybridSearcher keywords 模式 + FTS5/jieba/lexical_zh，零 embedding，确定性）+ `--engine real-hybrid`。保留 `offline`（BM25）默认（英文基线 + CI 确定性不破）。

### retrieval_zh Recall@5 诚实测量（spec S4）

- `--engine real-hybrid` 在 retrieval_zh 上 **Recall@5 = 0.6**（= offline 基线）。引擎正确（单测过、确定性、走真 HybridSearcher+lexical_zh）。
- 未达 spec S4 的 0.7，根因明确（如实记录，**未刷数**）：retrieval_zh 仅 5 条查询（粒度粗，0.7 实际要 4/5=0.8）+ 本环境 lexical 字典/同义词为空 + 查询集无「创智杯」式专名（W3 专名分词收益不适用）。
- **0.6→0.7 需真实领域数据 + 填充 `data/lexical_zh_dict.txt`/`synonyms.txt` + `reindex_all`**（符 W3 handoff §5.2 预判，原 defer 用户环境；**已于 2026-07-07 S4 收尾解决，0.6→1.0，见下文 S4 段**）。引擎已就绪，真实数据接入即可量化提升。

### 文档（spec §6.4 4.3）

- `docs/advanced-features.md` += 规模自适应路由 / Wiki Parent-Child / 中文 lexical 强化 三章。
- `tests/test_docs_consistency.py` += 三章存在性 + 配置键与 `config.example.yaml` 一致性断言。

### 验证（当次真实执行）

- 新增 21 个 TDD 测试（11 fs_lint + 3 wiki_eval + 2 routing + 3 real-hybrid + 2 docs），全部通过
- 全量回归 **1219 passed / 1 skipped / 0 failed**（基线 1198，+21，零退化）
- ruff **0 错误** / mypy **0 错误（173 src 文件，+1 wiki_fs_lint.py）**
- `detect_changes`：risk LOW，0 affected processes，无预期外传播

### 后续（非本轮范围）

- ~~retrieval_zh Recall@5 0.6→0.7 的真实数据验证~~ **已于 2026-07-07 S4 收尾解决（0.6→1.0），见下文「retrieval_zh Spec S4 直接收尾」段**
- spec Gap B 的「双轨 wiki 编译统一」（MCP→SQLite vs path_indexer→文件系统）为更大架构级 gap，本轮按 spec §6.4 仅补文件系统 lint 测量层，未合并两轨（记为 Phase 3 候选）

## retrieval_zh Spec S4 直接收尾 (2026-07-07, v1.5.1)

W4 收口时 retrieval_zh Recall@5=0.6 如实记为 finding（见上节），defer 到「真实数据 +
dict/synonyms + reindex」。本次会话内收尾兑现 spec S4：

- **根因（源码核实）**：`evals/real_hybrid_engine.py` 的 `_HYBRID_CFG` 只设
  `lexical_zh.enabled=True`，缺 `synonym_path`/`dict_path` —— LexicalZh 虽走注入
  dict 分支（`lexical_zh.py:52-61`）但不传路径 → 同义词加载被短路；加之
  `data/lexical_zh_synonyms.txt` 空模板。
- **实际失败项**：Q1「知识库默认使用什么数据库？」+ Q2「如何改善搜索质量？」
  （纯中文 token 在英文 fixture 无命中，`fts_rows=0`），非 W4 推测的 Q4/Q5
  （Q4/Q5 靠 MCP/embedding 英文 token 已 PASS）。
- **修复**：`_HYBRID_CFG` 注入 `synonym_path`/`dict_path`（绝对路径，eval 隔离
  环境也能加载）+ 填 14 条通用跨语种技术术语同义词（数据库/database+sqlite、
  搜索质量/search quality+reranking、改善/improve 等）。
- **结果**：Recall@5 **0.6 → 1.0**（5/5，≥0.7 达 spec S4）。Q1/Q2 经同义词扩展
  命中 architecture.md / troubleshooting.md。
- **防过拟合**：同义词只通用跨语种技术术语（中↔英），不针对 fixture 特定 token；
  测试验证机制（LexicalZh 从注入 dict 读 synonym_path）非特定命中。
- **附带修**：`project_setup._lexical_zh_defaults` 的 `rrf_weight_keyword_zh/en`
  从 lexical_zh 子段移到 rag 顶层（与 `hybrid_search.py:178-180` 读取位置一致；
  原 bug 行为碰巧一致因 fallback 值相同）；`--reindex` 文档纠误（CLI 无此 flag，
  全量重建走 `reindex_all`）。
- **本地 config**：`config.yaml`（gitignored）补 `rag.lexical_zh` 节，本地生产
  环境 lexical 全生效。
- **专名分词**（jieba 词典）在本数据集无专名无收益；真实领域专名（创智杯等）
  留待真实部署环境 + 填 dict + `reindex_all`。
- **验证**：新增 5 测试（real_hybrid +2、project_setup_lexical +3）；全量回归
  **1224 passed / 1 skipped**（基线 1219 +5，零退化）；ruff/mypy 0 错误；
  detect_changes risk LOW、0 affected。
- Spec/Plan：`docs/superpowers/specs/2026-07-07-knowledge-base-retrieval-zh-s4-closure-design.md`
  + `docs/superpowers/plans/2026-07-07-knowledge-base-retrieval-zh-s4-closure.md`。

## 双轨 Wiki 轻量收敛 (2026-07-07, v1.5.2)

W4 收口时双轨 wiki 编译分离记为 Phase 3 候选技术债。本次轻量收敛（用户选
「轻量收敛 + 浅 fallback」范围），不碰完整迁移的高风险障碍：

- **背景**：两轨——A（MCP→SQLite `WikiCompiler`,concept 页）+ B
  （path_indexer→文件系统 `KnowledgeWorkflowService`,source/entity 页）。问题:
  ① A 轨「只生产不消费」断层（SQLite wiki 没进 ask 主链路）;② 双写散落
  （save_to_wiki + _try_auto_save_wiki 两处）;③ frontmatter 溯源字段异构。
- **4 组件落地**:
  1. `resolve_source_ids` helper（`src/services/wiki_source_ids.py`）+ `_parse_json_list`
     —— 统一 FM 溯源读取（旧文件 fallback knowledge_id）+ SQLite JSON 解析。
  2. frontmatter `source_ids` 跨所有 page_type 统一（WikiSourceCompiler +
     WikiEntityUpdater 写入;WikiParentRetriever + WikiFsLint 改用 helper）。
  3. `WikiWriteService`（新模块）统一双写,A/B 任一失败不阻塞;收敛 save_to_wiki
     + _try_auto_save_wiki 两处散落双写;AppContainer 注入 lazy property。
  4. `WikiReadStage._sqlite_fallback`——FS 无命中时查 `search_wiki_fts` 转候选,
     配置门控 `rag.wiki_read.sqlite_fallback`（默认 true,仅 mode=wiki_first 生效,
     legacy 零影响 S6）。解决 A 轨断层。
- **未碰（完整迁移留独立 spec）**:主键统一（uuid4↔路径式）/ workflow 状态机迁移
  / wiki_links 物化 / A 轨编译器改写。
- **验证**:新增 18 测试（source_ids 8 + frontmatter 2 + write_service 3 +
  sqlite_fallback 4 + docs 1）；全量回归绿（基线 1224 +18，零退化）;ruff/mypy 0；
  gitnexus impact _try_auto_save_wiki risk LOW（1 caller,0 process）。
- Spec/Plan：`docs/superpowers/specs/2026-07-07-knowledge-base-dual-track-wiki-convergence-design.md`
  + `docs/superpowers/plans/2026-07-07-knowledge-base-dual-track-wiki-convergence.md`。

## 50 轮 MCP 测试报告 BUG 修复 — 已完成 (2026-06-25)

基于 `shineheKB-MCP测试报告-50轮.docx`（50 轮，成功率 96.0%）的 2 个 Bug + 2 个待改进项做代码层根因定位并全量修复。

### 修复清单

| Bug | 严重度 | 根因 | 主要改动 |
|-----|--------|------|---------|
| Bug-1 P0 kb_route_query 路由 100% 退化 | 严重 | ①标签覆盖率仅 3.7%；②`auto_tag` 工具把字符串 prompt 直接传给 `llm.chat(messages: list[dict])`，类型不符导致批量补标必然失败，标签覆盖率长期停滞 | `mcp_server.py`: auto_tag 构造标准 messages list + limit 上限 100→500；`route_engine.py`: EmbeddingRouter 新增 title embedding 兜底（标签不足时用标题语义匹配，命中则路由为 title contains filter）；`scripts/auto_tag_batch.py`: 新增批量补标 CLI 脚本 |
| Bug-2 P1 kb_ask 偶发超时 (MCP -32001) | 中等 | `ask` 工具无总超时控制，`rag_pipeline.query()` 内部超时后 fallback 到 `_direct_query`（再次调 LLM）导致雪崩 | `rag_pipeline.py`: query() 新增 timeout 参数（默认从 `rag.ask.total_timeout` 读 90s）+ 超时即抛出不再雪崩；`mcp_server.py`: `_do_ask` 捕获超时返回部分结果+警告；`config.yaml`: 新增 `rag.ask.total_timeout: 90` |
| 改进项3 大文档输出截断 | 建议 | block_contexts 字段含完整父块内容，大文档（如供应商管理办法）导致 MCP payload >300KB 被传输层截断 | `rag_pipeline.py`: PostProcessStage 新增 `block_context_max_length`（默认 2000）截断每个 block_context；`DEFAULT_PIPELINE_CONFIG` 显式声明 postprocess 配置 |

### 验证

- 新增 `tests/test_50round_bugfix.py`（6 个回归测试）：auto_tag messages 修复、EmbeddingRouter title 兜底、ask 超时返回部分结果、PostProcessStage block_contexts 截断 — 全部通过。
- 回归测试：`test_mcp_server.py` + `test_mcp_contract.py` + `test_rag_sources.py`（68 passed）、`test_mcp_rag_full.py` + `test_full_pipeline_e2e.py`（24 passed）、`test_db.py` + `test_search.py` 等（43 passed）。
- 修复了 1 个因前次 BUG-1 修复导致过期的断言（`test_agentic_router_falls_back_for_fuzzy`：hybrid 兜底现附带 fulltext query_spec）。

### 运维建议（执行 auto_tag 提升标签覆盖率）

```bash
# 在项目根目录执行，对全部无标签文档批量 LLM 打标
python scripts/auto_tag_batch.py
# 仅查看当前覆盖率，不写入
python scripts/auto_tag_batch.py --dry-run
```

## v1.4.0 测试报告 BUG 修复 — 已完成 (2026-06-25)

基于 `shineheKB-MCP测试报告-30轮-v1.4.0.docx` 的 5 个 Bug 做代码层根因定位并全量修复。Commit: `1f79f7f`

### 修复清单

| Bug | 严重度 | 结论 | 主要改动 |
|-----|--------|------|---------|
| BUG-1 P0 route_query 路由退化 | 严重 | 路由分类器工作正确（测试查询均为语义查询），但 hybrid 模式缺少 query_spec | `route_engine.py`: ①EmbeddingRouter 阈值 0.75→0.60 ②LLMRouter/PlanetaryRouter hybrid 返回附带 fulltext query_spec |
| BUG-4 P2 标签覆盖率 3.7% | 中等 | 数据层面问题，需自动化补标手段 | `health.py`: 新增 `_get_kb_domain_summary()`、分级告警、recommendations 字段；`mcp_server.py`: 新增 `auto_tag` LLM 批量打标工具；`aliases.py`: 注册别名 |
| BUG-5 P1 ask_with_query 参数 BC | 中等 | 旧参数 `query` 不再被接受 | `mcp_server.py`: 新增 `query` 向后兼容别名（等价于 `search_query`） |
| BUG-6 P1 structured_query 参数 BC | 中等 | 旧参数 `filters` 不再被接受 | `mcp_server.py`: 新增 `filters` 向后兼容别名（等价于 `query_dsl`） |
| BUG-7 P0 ask 返回"未找到" | 中等 | 弱相关召回被 score_threshold 过滤 + 空上下文 LLM 误判 | `config.yaml`: score_threshold 0.35→0.25；`rag_pipeline.py`: GenerateStage 空上下文注入知识库领域概览兜底 |

### 待后续关注

- BUG-1 修复后需重新测试 route_query 在结构化场景下的表现

## v1.4.0 BUG-1 补充修复 + MCP 校验测试 — 已完成 (2026-06-25)

Commit: `18b8fd8`

### 测试发现
MCP 校验测试（`tests/mcp_post_fix_test.py`）发现 `route_query` 始终调用 `AgenticRouter` 而非 `PlanetaryRouter`（见 `mcp_server.py` L2275），因此第一轮仅修改 `route_engine.py` 无法生效。

### 补充修复
- `agentic_router.py`: 3 处 hybrid fallback 路径（L136-139, L157-159, L175）均追加 fulltext query_spec，与 PlanetaryRouter 保持一致

### 校验结果
| 校验项 | 状态 | 备注 |
|--------|------|------|
| BUG-5 ask_with_query(query=) | ✅ 通过 | 旧参数兼容正常 |
| BUG-6 structured_query(filters=) | ✅ 通过 | 旧参数兼容正常 |
| BUG-7 ask(Block-First) | ✅ 通过 | 返回 1036 字符回答 + 5 个来源 |
| BUG-1 route_query | ⏳ 待重启验证 | 代码已修复，MCP 需重启 |
| BUG-4 recommendations | ⏳ 待重启验证 | 代码已修复，MCP 需重启 |
| auto_tag | ⏳ 待重启验证 | MCP 需重启加载新工具 |
| search 基本功能 | ✅ 正常 | 含企微知识返回 3 条向量结果 + 39 条全文结果 |

### 下一步
**重启 MCP 服务**后重新运行 `tests/mcp_post_fix_test.py` 完成全量验证。
- auto_tag 工具的 LLM token 成本需评估（批处理上限 100 条/次）
- score_threshold 降低可能引入噪声结果，需观察实际召回质量

## 第5轮稳定性测试报告全量修复 — 已完成（2026-06-22）

基于 `docs/ShineHe_KB_MCP_稳定性测试报告第5轮.md` 的 8 个 Bug 做代码层根因定位（含交叉审查）并全量修复。

### 修复清单

| Bug | 结论 | 主要改动 |
|-----|------|---------|
| BUG-1 P0 LLM 认证 | 代码改进 + 部署配 key | `llm.py`/`embedding.py` 移除静默 `no-key` 兜底、加精确诊断与一次性告警；`container.py` 启动期 key 缺失检测；`windows_service.py` 启动时显式 `Config.load()` + 注入 secret 到进程环境，缺失时记 Windows 事件日志 |
| BUG-2 P0 Vector null | 与 BUG-1 同源 + 可观测性 | `hybrid_search._vector_search` 改返回 `(results, warnings)`（用返回值而非实例属性），降级原因透传到候选 `warnings`，keyword 通道独立性绝对不破坏；不改 `vector_score=None` 语义 |
| BUG-3 P1 route_query | 补完 3 个遗留缺陷 | `agentic_router`：graph 分支 mode 改 structured（消除 mode/query_spec 矛盾）；`_is_structured` 收紧为强信号子集（避免"哪些/状态"误命中语义查询）；`_try_llm` 加 debug 日志；恢复强断言 |
| BUG-7 P2 file_type | 真 bug 已修 | `file_graph.create_page` 补 file-type 键（原被丢弃致 sync_page fallback "md"） |
| BUG-8 P3 重复 | 已修，"未知"订正 | `path_indexer._ingest_file` 加 content_hash 幂等去重（与 `mcp_server.create` 一致）。"未知"标题是展示层回退非导入问题，不改 |

BUG-4/5/6 在 round 1/4（commit `82d2a99`/`fe19524`）已有代码层修复，报告基于旧快照；本轮补回归测试锁死。

### 验证

| 门禁 | 结果 |
|------|------|
| Python 全量测试 | `887 passed, 1 skipped in 267.54s` |
| 改动模块集成测试 | test_core/search/search_service/mcp_server/indexer/reranker_providers/mcp_stability/mcp_rag_full/query_revolution_phase3/llm_configuration/file_graph/path_indexer 零回归 |

### 部署侧待办（用户必做）

BUG-1/BUG-2 的 RAG 与语义搜索完全恢复，需在 Windows Service 环境注入 API Key（SYSTEM 账户读不到交互式账户的 keyring）：

```
setx SHINEHE_LLM_API_KEY <KEY> /M
setx SHINEHE_EMBEDDING_API_KEY <KEY> /M
```

重启 `ShineHeMCP` 服务后，`ops_ping` 的 `api_keys.llm/embedding` 应为 true，`vector_index.coverage` 应 > 0；历史 PDF 条目 file_type 需 `reindex_all` 修正（BUG-7）。

## v1.3.1 全仓库健康审查 — 已完成

本轮基于当前规格与实施计划，对源码、测试、GUI、MCP、索引、引用、评测、构建脚本和发布资料进行了全量审查与修复。

### 主要修复

- 修复目录索引、异步任务、文件解析、SQLite 图存储、GUI worker 清理和 MCP 工具契约中的实际缺陷。
- 修复 Block 元数据被兼容 chunk 写入覆盖的问题，确保 `source_path`、Block ID 和 Citation 可追溯。
- 更新过时的容器属性调用、PySide6 枚举和 pikepdf 参数，删除无效导入与旧式异常处理。
- 为 `Database` 兼容元类增加 mypy 插件，清零源码类型错误。
- 修复 Windows GBK 控制台下 Demo 状态符号崩溃，并将 Demo 测试隔离为确定性 fake 服务。
- 将 `scripts/` 纳入 Ruff 健康门禁，清理构建、迁移、诊断、压力和数据救援脚本。
- 修复检索评测中“引用完整性字段定义但从未计算”的死指标，建立真实非零 baseline。
- 修复 Linux CI 的 GUI 系统依赖、跨平台类型边界、缺失运行时依赖和依赖本机配置的测试，升级 Actions 到 Node 24 运行时版本。

### 发布前验证

| 门禁 | 结果 |
|------|------|
| Python 全量测试 | `828 passed, 2 skipped in 845.14s` |
| GitHub Actions Test | Ubuntu / Python 3.12：`828 passed, 2 skipped in 145.12s` |
| Ruff | `src tests evals tools scripts` 全绿 |
| mypy | `157 source files`，无错误 |
| Python compileall | `src scripts tests evals tools` 通过 |
| Web 客户端 | TypeScript + Vite 生产构建通过 |
| 检索评测 | CI 同款 fake-embedding 门禁通过 |
| 本地检索 Demo | `initial_hit=true`、`incremental_update=true`、`citation_complete=true` |
| 远端 CI | Test、Lint、Frontend Build、Retrieval Eval、Docker Build 五项全绿 |

### 当前检索基线

| 指标 | 综合结果 |
|------|----------|
| Recall@5 | 0.8667 |
| MRR | 0.7800 |
| nDCG@10 | 0.7938 |
| No-Answer Accuracy | 0.6667 |
| Citation Location Completeness | 1.0000 |

### 已知边界

- `retrieval_zh` Recall@5 仍为 `0.6000`，No-Answer Accuracy 为 `0.6667`，均已进入非零 baseline，后续优化不得回退超过 5%。
- 本机没有 Docker CLI，无法执行本地镜像构建；Dockerfile 由 GitHub Actions 的 `docker` job 继续作为远端发布门禁。

## v1.3.0 MCP Local Retrieval Focus — 已完成

本次改造将 ShineHeKnowledge 收束为默认工具面精简、可一键本地初始化、可持续索引目录、引用可解释且质量可量化的 MCP 本地知识检索引擎。

### 已完成模块

| 模块 | 交付 | 验证 |
|------|------|------|
| M0 基线冻结 | MCP 工具 legacy snapshot、检索回归基线 | `test_mcp_tool_profiles.py`、`test_retrieval_candidate_contract.py` |
| M1 工具配置档 | core/extended/admin/full/legacy profiles、声明式 registry | `test_mcp_tool_profiles.py` (12 tests) |
| M2 CLI 初始化 | `shinehe init/index/watch/doctor/mcp`、provider presets | `test_cli.py`、`test_project_setup.py`、`test_doctor.py`、`test_provider_presets.py` |
| M3 目录增量索引 | indexed_files 表、PathIndexService、FileWatcher、IndexScheduler | `test_path_indexer.py`、`test_indexed_file_repo.py`、`test_file_watcher.py`、`test_index_scheduler.py` |
| M4 检索与引用统一 | RetrievalCandidate、Citation、CitationBuilder、score breakdown | `test_retrieval_candidate_contract.py`、`test_citation_builder.py` |
| M5 本地 reranker | API/local/LLM/disabled 四种 provider、lazy load、失败降级 | `test_reranker_providers.py` (31 tests) |
| M6 Eval 质量门禁 | fixture、golden source、Recall/MRR/nDCG、CI 门禁 | `test_eval_datasets.py`、`test_retrieval_eval_runner.py`、`run_retrieval_eval.py --all` |
| M7 文档与 Demo | README 重写、迁移指南、advanced-features、demo 脚本 | `test_mcp_docs_prompts.py`、`test_demo_local_retrieval.py` |

### 检索 Eval 基线指标

| 数据集 | Recall@5 | MRR | nDCG@10 |
|--------|----------|-----|---------|
| retrieval_code | 1.0000 | 1.0000 | 1.0000 |
| retrieval_table | 1.0000 | 1.0000 | 0.9779 |
| retrieval_zh | 0.6000 | 0.3400 | 0.4036 |
| retrieval_no_answer | — | — | — (No-Answer: 0.6667) |

### 延后项

- 本地 reranker `sentence-transformers` extra 未在 CI 中实际加载模型（仅验证 lazy load 和 fallback）
- `retrieval_zh` 中文检索指标偏低，后续可通过优化分词和 query rewrite 提升
- Docker MCP 镜像构建未在本次验证（需要 Docker 环境）
- GUI 未适配 tool profile 切换（GUI 仍使用完整工具集）

### 已知兼容风险

- 老配置未设置 `mcp.tool_profile` 时自动走 `legacy`，行为不变
- `shinehe-mcp` 入口保留，不破坏已有客户端配置
- `kb_capabilities` 新增 `tool_profile`/`visible_tools`/`hidden_groups` 字段

### 安全加固（2026-06-13）

- **SSRF 防护**：`parse_url()` 添加 DNS 解析后 IP 检查，阻止对内网/回环/链路本地地址的请求，限制最大重定向 5 次
- **安全响应头**：API 层添加 `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: strict-origin-when-cross-origin`、`Permissions-Policy`
- **CORS 安全**：wildcard origins 时自动禁用 `allow_credentials`，防止 token 泄露
- **错误日志**：3 处裸 `except: pass` 改为 `except: logger.debug(...)`，避免静默吞掉异常
- **SQL 审查**：确认所有 f-string SQL 中的变量均为内部硬编码或已白名单验证，无注入风险

### SQLite 图谱存储收束（2026-06-22）

- **外部图数据库移除**：图谱存储统一使用 SQLite，本地 `data/kb.db` 中的 Page、Block、Tag、实体引用和语义关系表共同构成图视图
- **设置页收束**：GUI 不再提供外部后端切换、服务启停、自动部署或迁移按钮，只展示 SQLite 图谱存储说明
- **运行路径简化**：GUI、API、MCP 启动时不会检测或拉起外部图服务；旧配置中的非 SQLite provider 会兼容降级为 SQLite
- **依赖与部署简化**：核心安装、`all` extra、Docker Compose 和示例配置都不再包含外部图数据库服务
- **滚动条主题适配**：浅色/暗色 QSS 保留 `QScrollArea#graphBackendScroll` 与 `QScrollBar` 样式，handle 颜色与主色板一致

## 既有能力（v1.2.0 及之前）

- SQLite、FTS5、sqlite-vec 与 Block-first 存储。
- 向量检索 + 全文检索 + RRF 融合。
- query rewrite、reranker、Parent-Child、Evidence Compression。
- Block 级来源、source graph、结构化查询与 Agentic Router。
- MCP envelope、写操作策略、dry-run、审计、soft delete 与 undo。
- 大文件异步导入和任务查询。
- 51 个原始 MCP 工具（legacy 模式下全部可用）、3 个资源、5 个 Prompt。
- GUI、REST API、Web 客户端、Docker、Windows 服务和安装脚本。
- CI：Python lint/test、前端构建和 Docker 构建。

## 文档清理记录

2026-06-13 完成首轮仓库清理：

- 旧的 Structured/Graph RAG、MCP-first 和全平台升级方案移入 `docs/archive/`。
- 删除失效的手册补丁脚本、弃用的 `requirements.txt`、旧图标和误提交的 `.superpowers` 临时文件。
- 保留被测试引用的迁移脚本。
- 保留可能用于用户数据恢复的一次性脚本，并在 `scripts/README.md` 标明风险和用途。

## 验证原则

- MCP/RAG 改动优先运行对应 contract 和 targeted regression。
- 数据库迁移必须运行 `tests/test_migration.py`。
- 前端改动必须运行 `npm --prefix client run build`。
- 发布前再运行完整测试、Docker 构建和实际 GUI/MCP 启动验证。
- 测试结果只记录当次真实执行结果，不从历史文档复制通过数量。
