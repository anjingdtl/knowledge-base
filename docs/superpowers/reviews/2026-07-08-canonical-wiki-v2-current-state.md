# Canonical Wiki V2 现状审计与冻结（Phase 3.5 / C0）

> **审计性质：** Phase 3.5 Correction Gate 的 C0 任务，纯只读审计，不改任何业务代码。
> **审计日期：** 2026-07-08
> **审计分支：** `canonical-wiki-v2-correction`（从 `master` 最新稳定提交 `c134e5c` 创建；此前所有 wiki-v2 提交均直接落在 `master`，无功能分支）
> **执行依据：** `docs/superpowers/plans/2026-07-08-canonical-wiki-v2-correction-and-continuation.md`（§4 C0）
> **对照 Spec：** `docs/superpowers/specs/2026-07-07-canonical-wiki-claim-provenance-design.md`
> **审计方法：** 真实源码逐行核对（12 个核心文件）+ 3 个并行广度扫描子代理（写路径 / 读路径 / 全局依赖）+ 全量测试基线重跑。**不以旧 plan checkbox 推断完成状态。**

---

## 0. 总判定

Phase 0–3 的 Canonical 模型、Schema、Repository、Projection、Claim Extractor、Matcher、Merge Engine 代码与测试**已全部落地**，总体架构方向（Raw → Claim → Canonical Markdown → SQLite Projection → Retrieval）正确，与 Spec §1 一致。

但在进入 Phase 4（主工作流切换）前，存在 **4 类必须先纠正的阻断项**，且 C0 的验收标准"不存在未记录的 Canonical 写入口"**当前不满足**：

1. **写路径守卫存在盲区**：`tests/test_canonical_write_guards.py` 的 `GUARDED` 只覆盖 6 个模块，导致 `api/routes/wiki.py`、`wiki_lint.py`、`wiki_workflow.py` 三个模块共 **11 处绕过 `WikiRepository` 的直接写**对守卫完全不可见，`test_current_direct_writes_are_allowlisted` 恒绿。
2. **Claim 语义契约未冻结**：`ClaimMatchDecision.reasons` 仍是自然语言，缺 C1 要求的稳定 reason code；matcher 与 merge engine 未共用统一枚举。
3. **多对象事务非严格 staging**：`WikiRepository.transaction()` 是"stage 仅记对象、commit 才落盘"的轻量实现，未做 Spec §14.1 / C3 要求的 `_staging/<tx_id>` 落盘 + commit marker + 崩溃恢复扫描。
4. **读路径与配置状态机缺失**：三套 wiki 读取逻辑并存未统一、`WikiQueryService` 不存在、`wiki_pages_v2_fts` 被写入但零查询消费、`off/shadow/canary/primary` 状态机未实现。

详见第 9–11 节。本审计**不修复**上述任何问题，仅冻结现状供 C1–C6 逐项处理。

---

## 1. 当前模块图

### 1.1 已落地的新模块（Phase 0–3）

| 模块 | 行数 | 公共类 / 关键签名 | 落地 commit | 状态 |
|---|---:|---|---|---|
| `src/models/wiki_v2.py` | 268 | `PageType`/`PageStatus`/`ClaimStatus`/`EvidenceStance` 枚举；`Evidence`/`ClaimRelation`/`Claim`/`WikiPage`/`PageRegistryEntry`/`SaveResult`/`ValidationFinding` dataclass；模块级 `PAGE_TYPES` | `11ff572` | ✅ 完整 |
| `src/services/wiki_validator.py` | 89 | `WikiValidator.validate_page/validate_page_dict/validate_claim/validate_directory` → `list[ValidationFinding]` | `a611c9f` | ⚠️ 覆盖不足（见 4.3） |
| `src/services/wiki_repository.py` | 328 | `WikiRepository`（page/claim CRUD + registry/redirects/outbox + revision 乐观锁 + 路径安全）；`StaleRevisionError`；`WikiTransaction`；`new_page_id/new_claim_id` | `5f73460` | ⚠️ 轻量事务（见 2.1） |
| `src/services/wiki_projection.py` | 292 | `WikiProjection.process_outbox/rebuild/verify_parity/find_page_id_by_path`；`ProjectionResult` | `9b59abe`+`129cfe5` | ✅ 完整 |
| `src/services/wiki_claim_extractor.py` | 354 | `ClaimExtractor.extract` → `ClaimExtractionResult`；`ExtractionBlock` | `3e8a7d3`+`7569407` | ✅ 完整 |
| `src/services/wiki_claim_matcher.py` | 358 | `ClaimMatcher.match` → `ClaimMatchDecision`；纯函数式、不调 LLM | `7012d27`+`776f591` | ⚠️ 缺 reason code（见 4.1） |
| `src/services/wiki_merge_engine.py` | 506 | `WikiMergeEngine.apply` → `MergeResult`；所有变更经 `repository.transaction()` | `9499c5b`+`bb48e47`+`4efe0b9` | ✅ 完整（supersedes 已原子化） |
| `src/services/wiki_page_locator.py` | 213 | `WikiPageLocator.locate` → `(候选, 命中数)` | `25b17e6`+`8edaee8` | ⚠️ FS-first 反向 + 全局依赖（见 3.1、7） |
| `schema/wiki-page-v2.schema.json` / `wiki-claim-v1.schema.json` / `AGENTS.md` | — | 人类可读权威契约 | `a611c9f` | ✅ |
| `alembic/versions/j001_wiki_v2_projection.py` | 152 | 6 表 + FTS，`down_revision=i001_version_conflict`，全幂等，downgrade 不碰旧表 | `35e1996` | ✅ |
| `scripts/record_canonical_v2_baseline.py` + `artifacts/eval/canonical-v2-baseline.json` | — | 可复现基线聚合脚本 | (Phase 0 T0.1) | ✅ |
| `tests/test_canonical_write_guards.py` | 92 | AST 守卫 + `ALLOWED_DIRECT_WRITES`(7) + `GUARDED`(6) | (Phase 0 T0.2) | ⚠️ 盲区（见 2.3） |

### 1.2 尚未创建的 Spec 模块（Phase 4–6，预期未动）

`wiki_dependency_service.py`、`wiki_rebuild_service.py`、`wiki_feedback_service.py`、`wiki_v2_migrator.py`、`wiki_query_service.py`（C4 要新增的统一读端口）、`evals/run_wiki_evolution_eval.py` / `evals/wiki_v2/` 黄金集（C2 要新增）—— **均不存在**。

### 1.3 `container.py` 注入情况（`src/core/container.py`）

下列 wiki_v2 服务已注入为 lazy property，且在 container 层解析 config 后注入（符合 C6 外层 adapter 模式）：

- `wiki_repository`（:360）、`wiki_projection`（:376，读 `canonical_v2.enabled`）、`wiki_claim_extractor`（:389）、`wiki_claim_matcher`（:397）、`wiki_merge_engine`（:405）、`wiki_write_service`（:413，仍双写分发器）、`wiki_page_locator`（:424，注入 projection 但**未传 wiki_dir**）。

### 1.4 仍是 Phase 4 前旧实现的模块（预期保留，未改造）

- `knowledge_workflow.py`：仍是 4 编译器（source/entity/index/log）编排，**未接入 claim extractor→matcher→merge 链**。
- `wiki_write_service.py`（47 行）：仍是双写分发器（A轨 `WikiCompiler.save_answer` + B轨 `KnowledgeWorkflowService.save_query`），未改为 canonical 写入口。
- `wiki_compiler.py`（774 行）：仍是 LLM 重度 A 轨编译器，直接 `Database.insert_wiki_page`，未降级为 compatibility adapter。

---

## 2. 写路径图

### 2.1 唯一合法 canonical 写入口

```
业务服务 → WikiRepository（src/services/wiki_repository.py）
            ├── save_page / move_page   → write_markdown(原子) + registry + outbox
            ├── save_claim / delete_claim → yaml(原子) + outbox
            └── transaction() → WikiTransaction.stage_page/stage_claim → commit
```

**transaction 当前是轻量实现**（`wiki_repository.py:310-320`）：`stage_*` 只把对象记进内存 list，`commit()` 才逐个调 `save_page/save_claim` 落盘。中途异常天然无残留（因为没落盘），但**不满足 Spec §14.1 / C3 要求的**：`_staging/<tx_id>/` 物理落盘 → validate all → write transaction manifest → publish → commit marker → append outbox → cleanup staging，也无启动期未完成事务扫描。多对象原子性与崩溃恢复**未实现**（仅 supersedes 在 merge_engine 层做了"双 validate 后才双 stage"的应用级原子，:356-376）。

### 2.2 Projection 写（合法，唯一 v2 表写入点）

`WikiProjection`（`wiki_projection.py`）是 v2 projection 表的唯一写入方：`_upsert_page`/`_upsert_claim`/`_delete_*` 用 `INSERT OR REPLACE` + 先删后插（幂等），`rebuild` 在单事务内清空 6 表后全量灌入（:93-120，中途 rollback）。**Pattern C 维度干净**：src/ 其余模块无任何对 `wiki_pages_v2`/`wiki_claims`/`wiki_claim_evidence`/`wiki_page_claims`/`wiki_pages_v2_fts` 的 INSERT/REPLACE/UPDATE/DELETE。

### 2.3 已记录的直接写 allowlist（`tests/test_canonical_write_guards.py`，7 条）

| # | (module, call) | 用途 | 移除阶段 |
|---|---|---|---|
| 1-2 | `wiki_compiler.py: insert_wiki_page / update_wiki_page` | A轨 SQLite 写 | Phase 4 T4.3 |
| 3 | `wiki_entity_updater.py: write_markdown` | B轨 FS 写 | Phase 4 T4.1 |
| 4 | `knowledge_workflow.py: write_markdown` | B轨 FS 写 | Phase 4 T4.1 |
| 5 | `wiki_source_compiler.py: write_markdown` | B轨 FS 写 | Phase 4 T4.1 |
| 6 | `wiki_index_compiler.py: write_markdown` | index.md | Phase 4 评估 |
| 7 | `wiki_log_compiler.py: write_text` | log.md（`rebuild`） | Phase 4 |

**7 条全部仍真实存在（无空壳漂移）。**

### 2.4 ⚠️ 未记录的越界写（11 处，allowlist 之外，对守卫不可见）

**Pattern B — 直接 `Database.insert_wiki_page / update_wiki_page`（写旧 SQLite `wiki_pages`，绕过新 WikiRepository）：**

| file:line | 函数 | 说明 |
|---|---|---|
| `src/api/routes/wiki.py:72` | `create_wiki_page` | POST `/pages` 直接 `container.db.insert_wiki_page({...})` 建页（已复核） |
| `src/api/routes/wiki.py:107` | `update_wiki_page` | PUT `/pages/{id}` 直接 `container.db.update_wiki_page(...)` |
| `src/services/wiki_lint.py:210` | `WikiLint.run` | 批量回写 `lint_score` |
| `src/services/wiki_lint.py:301,306` | `mark/clear_complex_anomaly` | 写/清 `complex_anomaly` |
| `src/services/wiki_lint.py:389,415` | `repair_complex_issues` | 写 LLM 生成的 `concept_summary`；把重复老页置 `deprecated` |
| `src/services/wiki_lint.py:499` | `_fill_empty_page` | 写 `content`/`concept_summary`/`source_ids` |
| `src/services/wiki_workflow.py:161` | `restore_version` | 版本恢复后回写 title/content/status |
| `src/services/wiki_workflow.py:187` | `_do_transition` | 工作流状态流转写 `status` |

**Pattern A — FS 追加写（守卫 AST 探不到 `open("a")`）：**

| file:line | 函数 | 说明 |
|---|---|---|
| `src/services/wiki_log_compiler.py:26` | `append` | `with log_path.open("a")` 追加写 `wiki/log.md`（与 allowlist 第 7 条覆盖的 `rebuild().write_text` 是两条独立写路径） |

**潜伏平行写路径：** `src/repositories/wiki_repo.py` 定义了**第二个** `WikiRepository` 类（与新 canonical `src/services/wiki_repository.py` 同名易混），用裸 SQL 直接 `INSERT/UPDATE/DELETE wiki_pages` 及 `wiki_links`/`wiki_ops_log`/`wiki_workflow`/`wiki_page_versions`。它在 `container.py:535` 注入为 `container.wiki_repo`，**当前无任何写方法被调用**，但一旦将来有业务代码调用 `container.wiki_repo.insert_page(...)` 即产生新绕过。

### 2.5 守卫盲区（重大）

`test_canonical_write_guards.py` 的 `_scan()` 只遍历 `GUARDED` 字典里的 **6 个模块**（wiki_compiler/wiki_entity_updater/knowledge_workflow/wiki_source_compiler/wiki_index_compiler/wiki_log_compiler）。§2.4 的 `api/routes/wiki.py`、`wiki_lint.py`、`wiki_workflow.py` **根本不在扫描范围**，因此 10 处 `insert/update_wiki_page` 调用对守卫完全不可见。守卫也不识别 `open("a")` 追加写、裸 SQL `INSERT/UPDATE`、辅助表（`wiki_ops_log` 等）写。

→ **C0 验收项"不存在未记录的 Canonical 写入口"当前不满足。** 修复方向：C1/C6 前，先扩 `GUARDED` 为全 `src/` 扫描（至少纳入上述 3 模块 + 识别 `open("a")` / 裸 SQL），以失败测试暴露这 11 处，再决定纳入 allowlist（过渡）或改造经 Repository。

---

## 3. 读路径图

当前存在**三套 wiki 读取逻辑并存且未统一**，Spec C4 要求的统一端口 `WikiQueryService` **不存在**。

### 3.1 三套读取逻辑

| 轨 | 数据源 | 主要消费方 | 范围 |
|---|---|---|---|
| **A 轨**（最广） | SQLite `wiki_pages` 表（`db.search_wiki_fts` / `list_wiki_pages` / `get_wiki_page` / `get_wiki_page_by_title`） | SearchService(`:309`)、MCP search/search_fulltext/route_query(`mcp_server.py:469,2403`)、`WikiRetrievalStage`(`rag_pipeline.py:205`)、`RAGService._get_wiki_context`(`:1509`)、wiki_compiler、wiki_lint、wiki_site、wiki_workflow、transclusion、API routes、GUI wiki_view | 30+ 调用点 |
| **FS 轨** | `wiki/*.md` | `WikiPageLocator.locate`(`:72`)、`WikiFsLint`、`resolve_source_ids` helper、`WikiParentRetriever` | 仅 wiki_first 模式的 WikiReadStage/SizeAwareRouter 用 |
| **V2 轨**（基本未启用） | `wiki_pages_v2` projection | **唯一运行时调用**：`WikiPageLocator._enrich_with_projection`(`:176`) → `projection.find_page_id_by_path`（仅做 id 改写，非召回）；`verify_parity` 离线诊断 | 1 个运行时点 |

### 3.2 ⚠️ `wiki_pages_v2_fts` 零查询消费

`WikiProjection._upsert_page`（`:202-206`）持续向 `wiki_pages_v2_fts` 虚拟表灌数据，但**全代码库无任何 `SELECT ... FROM wiki_pages_v2_fts` / `MATCH` 读取它**。FTS 索引被建好却无查询路径消费——是"第三套读取逻辑"的温床，C4 落地时必须收敛（要么接查询，要么停写）。

### 3.3 ⚠️ `WikiPageLocator` 读取顺序与 Spec T2.3 相反

- Spec T2.3（plans:1636 / specs:1344）要求：**projection 优先 + FS fallback**。
- 当前实现（`wiki_page_locator.py`）：**FS-first**，projection 仅在 FS 已命中候选后，对 `metadata.page_id is None` 的 legacy 页用 `find_page_id_by_path` 把 slug-id 改写成稳定 `page_id`（enrichment，`:155-187`），**不参与候选召回**。若 FS 没扫到该页，projection 永不被咨询。
- `SizeAwareRouter` 只取 locator 的命中计数判档（不关心 id 稳定性），这部分未退化；但候选 id 稳定性依赖 frontmatter 已写 `page_id` 或 projection enrichment 成功，legacy 页且 projection 不可用时 id 仍是 slug。

### 3.4 额外重复实现

- `_read_body()` 在 `wiki_page_locator.py:206` 与 `wiki_fs_lint.py:31` **双份拷贝**。
- `WikiLint`（SQLite 轨）与 `WikiFsLint`（FS 轨）**并行存在**，产同构 `LintReport`。
- 同一次 ask 可能同时跑两条 wiki 读路径：`DEFAULT_PIPELINE_CONFIG` 里 `wiki_retrieval`（A轨 SQLite）与 `wiki_read`（FS）**同时 enabled**。

### 3.5 `projection_drift` warning 基本未在读路径体现

`projection_drift` 只作为 `WikiProjection.verify_parity()`（`:141,149,166,174`）的离线 `ValidationFinding`，不进运行时检索热路径。运行时仅 locator 在 projection 异常时发一条 generic warning（`:184`，类型非 `projection_drift`，且每实例只 warn 一次）。SearchService/MCP/`WikiRetrievalStage`/API/GUI **无一**感知 projection 状态。`canonical_v2.enabled=false`（默认）时 locator enrichment 直接跳过（`:164`），整个 v2 读路径相当于不存在。

---

## 4. 数据对象关系图

### 4.1 Canonical 文件系统对象

```
wiki/
├── <page_type>/<slug>.md     # WikiPage（frontmatter + body），page_type ∈ {sources,entities,concepts,comparisons,syntheses}
├── claims/<claim_id>.yaml    # Claim（schema_version=1）+ Evidence 列表 + ClaimRelation
├── _meta/pages.json          # PageRegistryEntry: page_id → {path,title,page_type,revision,content_hash}
├── _meta/redirects.json      # 旧路径 → page_id
└── _staging/                 # （目录已建，但当前轻量事务未真正使用）
data/wiki_projection_outbox.jsonl  # outbox 事件: page.created/updated, claim.created/updated/deleted
```

### 4.2 对象关系

```
WikiPage ──claim_ids──▶ Claim ──evidence──▶ Evidence
   │                       │                    │
   │                       └──relations──▶ ClaimRelation ──▶ Claim
   │                                            │
   └─source_ids──▶ knowledge_id                 └─knowledge_id + block_id + source_revision ─▶ Raw Block
```

- `Evidence` 强制 `knowledge_id`（`wiki_v2.py:61`）；`Claim.validate()` 要求 active Claim 至少一条 supports Evidence（`:113-122`）。
- `ClaimMatchDecision.action ∈ {new, supports, refines, contradicts, supersedes, duplicate, unresolved}`（`wiki_claim_matcher.py:29`），**但 `ClaimRelation.relation` 仍是自由 `str`**（`wiki_v2.py:82`），merge_engine 实际写入 `"superseded_by"`/`"supersedes"`/`"refined_by"` 等字符串，未与 action 枚举统一。

### 4.3 SQLite v2 Projection 表（`j001` migration，6 表 + FTS）

| 表 | 主键 | 说明 |
|---|---|---|
| `wiki_pages_v2` | page_id | path UNIQUE；含 content/content_hash/aliases_json/tags_json/source_ids_json/claim_ids_json |
| `wiki_claims` | claim_id | 含 claim_scope(当前恒 NULL)/normalized_statement |
| `wiki_claim_evidence` | evidence_id | `uq_wiki_evidence_triple`(claim_id+knowledge_id+block_id+stance+source_revision) |
| `wiki_page_claims` | (page_id, claim_id) | display_order |
| `wiki_dependencies` | (from_type,from_id,to_type,to_id,relation) | 依赖图（Phase 5 用，当前无写入） |
| `wiki_projection_state` | key | KV（当前无写入） |
| `wiki_pages_v2_fts` | — | FTS5 虚拟表（**零读取**，见 3.2） |

旧表 `wiki_pages`/`wiki_links`/`wiki_ops_log`/`wiki_fts`/`wiki_workflow`/`wiki_page_versions` 全部保留（downgrade 不碰），符合 Spec §6。

### 4.4 Validator 覆盖不足

`WikiValidator`（`wiki_validator.py`）当前只覆盖 3 类 finding：`schema_invalid`（from_dict strict）、`publish_gate_violation`（published 引用 draft claim，:47）、`claim_missing`（claim 文件不存在，:82）。Spec §7.1 要求的 **page registry parity、evidence 完整性（knowledge/block 存在）、source revision 过期、redirect 成环、claim anchor 与 claim_ids 一致** 等均未实现。`projection_drift` 校验独立在 `WikiProjection.verify_parity()`，未集成进 validator。

---

## 5. 配置与 feature flag

### 5.1 实际生效值（`config.yaml`，本地 gitignored）

```yaml
knowledge_workflow:
  mode: wiki_first          # ⚠️ 何大哥生产项目已是 wiki_first
  wiki_dir: wiki
  ...
wiki:
  enabled: true
  auto_compile: true
  auto_link: true
  auto_publish: false
  max_llm_calls_per_ingest: 3
```

### 5.2 ⚠️ `canonical_v2` 配置缺失 + 键路径偏差

- `config.yaml` 与 `config.example.yaml` **都没有 `canonical_v2` 段**，也没有 `wiki.claims` / `wiki.rebuild` / `wiki.projection` / `wiki.validation` 段（Spec §9 全部要求）。
- `container.py:380` 读的是**顶层** `canonical_v2.enabled`（默认 false），而 Spec §9 写的是 `wiki.canonical_v2.enabled`。**键路径不一致**。
- matcher 读 `wiki.claims.unresolved_threshold`(0.72)/`semantic_match_threshold`(0.88)/`enabled` 等（`wiki_claim_matcher.py:92-93`），extractor 读 `wiki.claims.*`（`wiki_claim_extractor.py:99-105`）——这些键在 config 里**不存在**，全靠代码默认值（`_cfg(key, default)`）兜底。

### 5.3 feature flag / 门控现状

- `canonical_v2.enabled`（默认 false）→ 控制 `WikiProjection.enabled` → 控制 locator enrichment 是否跳过。
- `knowledge_workflow.mode == wiki_first` → 控制 B 轨 FS wiki 编译、`WikiReadStage`、`SizeAwareRouter` 是否生效。
- `rag.size_aware.enabled` / `rag.wiki_read.sqlite_fallback` → 第二阶段遗留门控。
- **Spec C5 要求的 `off/shadow/canary/primary` 四态状态机完全不存在**，当前只有"开/关"二值。

---

## 6. 已知直接写 allowlist（汇总）

见 §2.3（7 条已记录）+ §2.4（11 处未记录越界写）+ §2.5（守卫盲区）。

**结论：** 当前 allowlist 不完整。Phase 4 前必须先把 11 处越界写纳入守卫视野（扩展 `GUARDED`），再按"过渡 allowlist"或"改造经 Repository"分类处理。每完成一个过渡任务必须**缩小** allowlist，不得扩大（Phase 3.5 铁律 §3.3）。

---

## 7. 全局依赖使用点

### 7.1 新服务合规判定

| 模块 | `Config` 全局 | `Database` 单例 | `get_active_container` | 判定 |
|---|:---:|:---:|:---:|---|
| wiki_repository.py | — | — | — | ✅ 标杆 |
| wiki_projection.py | — | —（`self._db` 注入） | — | ✅ 标杆 |
| wiki_claim_extractor.py | —（`self._config` 注入） | — | — | ✅ 标杆 |
| wiki_claim_matcher.py | —（`self._config` 注入） | — | — | ✅ 标杆 |
| wiki_merge_engine.py | —（`self._config` 注入） | — | — | ✅ 标杆 |
| wiki_write_service.py | — | — | — | ✅ 标杆 |
| **wiki_page_locator.py** | **✅ 违规** | — | — | ❌ **唯一违规** |

### 7.2 唯一新服务违规：`wiki_page_locator.py`

- `:24` `from src.utils.config import Config`（模块级 import 全局 Config）
- `:64` `WikiPageLocator.__init__` 的 `else` 分支：`self._wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))`

构造函数接受 `wiki_dir` 注入（合规的一半），但 `wiki_dir is None` 时**构造函数自身**回退抓全局 `Config.get()`。且 `container.py:428` 注入时**未传 wiki_dir**，所以生产路径必然走这个违规回退。修复量约 2 行（删 import + 要求 container 注入解析好的 wiki_dir）。

### 7.3 旧服务技术债（Phase 4 改造范围，非 C6 新增）

- `knowledge_workflow.py`：`:41/:110/:114/:119/:122/:126` 多处 `Config.get`、`:45` `Database.get_knowledge`（内部违规）；`:162-164` `try_knowledge_workflow_compile` 用 `get_active_container()`（**外层 adapter 合法**）。
- `wiki_compiler.py`：30+ 处 `Database.xxx()` 类级单例调用 + 多处 `Config.get`、`__init__` 直接 `LLMService()`。

---

## 8. 当前测试与指标基线（2026-07-08 重跑确认）

> 基线快照同步刷新至 `artifacts/eval/canonical-v2-baseline.json`（recorded_at 2026-07-08T19:35:52）。此为 Phase 3.5 纠偏前基线，后续不得低于（除 wiki_eval 债务外）。

| 门禁 | 结果 | 说明 |
|---|---|---|
| **pytest 全量** | **1346 passed / 2 skipped / 0 failed**（245.7s） | collection 1348（含 2 skip）；7 warnings 全是 `tests/mcp_post_fix_test.py` 的 `PytestReturnNotNoneWarning`（return bool 非 assert，与 wiki-v2 无关） |
| **ruff** | **7 errors**（`scripts/` 全部） | `_tmp_show_fails.py`(I001, 未跟踪临时文件) + `mcp_30round_prod_test_live.py`(F401 socket/tempfile、F541×2，commit `c134e5c` MCP 测试脚本)。**wiki-v2 代码 0 错误。** |
| **mypy** | **1 error** | `src/services/file_watcher.py:73` BaseObserver 赋值类型不兼容。**与 wiki-v2 无关。** |
| **retrieval_eval** | **passed** | recall@5=0.8667 / mrr=0.7800 / no_answer=0.6667（与 v1.3.1 基线一致，零回归） |
| **wiki_eval** | **❌ rc=1 崩溃** | `evals/run_wiki_eval.py:81` `Database.list_knowledge(limit=10000)` 把实例方法当类方法调 → `TypeError: missing 'self'`。v1.5.x 遗留 bug，wiki eval **当前完全跑不起来** |

**债务归属澄清：** ruff 7 + mypy 1 均与 Canonical Wiki V2 代码无关（scripts MCP 脚本 + file_watcher）。但 Phase 3.5 总门禁要求 ruff 0 / mypy 0，故仍需在 C1–C6 期间清理（不属于 wiki-v2 主线，但阻塞门禁）。`wiki_eval` 崩溃影响 C2 黄金集的 wiki 指标度量基线，须先修。

### 8.1 wiki_v2 专项测试覆盖（已存在）

`test_wiki_v2_models` / `test_wiki_validator` / `test_wiki_repository` / `test_wiki_v2_migration` / `test_wiki_projection` / `test_wiki_page_locator` / `test_wiki_claim_extractor` / `test_wiki_claim_matcher` / `test_wiki_merge_engine` / `test_wiki_v2_e2e` / `test_canonical_write_guards`。数量充足，但**测试数量不等于 Claim 语义准确率**（Phase 3.5 §2 第 7 条风险）——C2 黄金集尚不存在。

---

## 9. Phase 0–3 各 Task 实际状态（基于真实代码，非 checkbox）

| Phase | Task | 状态 | 依据 |
|---|---|---|---|
| 0 | T0.1 记录基线 | ✅ completed | `scripts/record_canonical_v2_baseline.py` + baseline.json 存在 |
| 0 | T0.2 架构守卫 | ⚠️ partially | 守卫机制工作，但 `GUARDED` 盲区（§2.5），11 处越界写不可见 |
| 1 | T1.1 模型 | ✅ completed | `wiki_v2.py` 完整，9 测试 |
| 1 | T1.2 validator+schema | ✅ completed | validator + 2 schema + AGENTS.md；但覆盖不足（§4.4） |
| 1 | T1.3 repository | ⚠️ partially | CRUD/registry/outbox/锁完整；transaction 是轻量实现（§2.1） |
| 2 | T2.1 migration | ✅ completed | `j001` 6表+FTS，幂等，不删旧表 |
| 2 | T2.2 projection | ✅ completed | process_outbox/rebuild/verify_parity 完整，rebuild 原子 |
| 2 | T2.3 page locator | ⚠️ deviated | 候选 id 切 page_id **部分**达成（有 page_id 用之，否则 slug + projection enrichment）；但读取顺序 FS-first **与 spec 的 projection-first 相反**（§3.3） |
| 3 | T3.1 extractor | ✅ completed | 规则切句+候选去重+LLM 抽取，LLM 失败降级 |
| 3 | T3.2 matcher | ⚠️ partially | 7 fixture 分类正确，保守（mid-range unresolved）；**缺 reason code**（§4.2） |
| 3 | T3.3 merge | ✅ completed | supports/duplicate/contradicts/supersedes(原子)/refines/unresolved/new；全经 transaction |
| 4 | T4.1–T4.3 | ❌ not started | workflow/write_service/compiler 均为旧实现 |
| 5 | T5.1–T5.3 | ❌ not started | dependency/rebuild/watcher 接入均未建 |
| 6 | T6.1–T6.4 | ❌ not started | migrator/feedback/eval 均未建 |

---

## 10. 与原 Spec 的偏差

| # | 偏差 | Spec 要求 | 现状 | 风险 |
|---|---|---|---|---|
| D1 | transaction 非严格 staging | §14.1：`_staging/<tx_id>` 落盘 + commit marker + 崩溃恢复扫描 | 轻量实现，stage 仅记内存对象（§2.1） | 高（C3 核心） |
| D2 | matcher 缺 reason code | §C1：稳定 reason code 枚举 | `reasons` 是自然语言（§4.2） | 高（C1 核心） |
| D3 | 守卫盲区 | §3.3 / T0.2：禁止绕过 Repository | GUARDED 只 6 模块，11 处越界写不可见（§2.5） | 高 |
| D4 | 读路径三套未统一 | §C4：统一 `WikiQueryService` | 不存在；wiki_pages_v2_fts 零读取（§3） | 高（C4 核心） |
| D5 | locator FS-first | §T2.3：projection-first + FS fallback | 反向（§3.3） | 中 |
| D6 | 配置状态机缺四态 | §C5：off/shadow/canary/primary | 仅开/关二值；canonical_v2 段缺失、键路径错（§5） | 中（C5 核心） |
| D7 | locator 全局依赖 | §C6：新服务纯构造注入 | wiki_page_locator 读全局 Config（§7.2） | 中（2 行修复） |
| D8 | ClaimRelation 非枚举 | §C1：matcher/merge 共用枚举 | relation 是自由 str（§4.2） | 中 |
| D9 | wiki_eval 崩溃 | §13.3：wiki eval 不低于基线 | run_wiki_eval.py:81 TypeError（§8） | 中（阻塞 C2 度量） |
| D10 | validator 覆盖不足 | §7.1：registry/evidence/redirect/projection parity | 仅 3 类 finding（§4.4） | 低（Phase 6 T6.2） |
| D11 | 流程：无功能分支 | 计划：feature 分支提交 | 所有 wiki-v2 提交直接在 master | 低（已纠正，C0 起用分支） |
| D12 | PROGRESS.md 过时 | §16：更新 PROGRESS | 停在 2026-07-03 / v1.4.0，未记 wiki-v2 | 低（文档债） |

---

## 11. Phase 4 前阻断项（按风险排序）

> 这些是 C1–C6 要逐项解决的，C0 仅记录。未全部解决前不得进入 Phase 4。

| 级别 | 阻断项 | 归属任务 | 依据 |
|---|---|---|---|
| 🔴 高 | **守卫盲区 + 11 处越界写**（含 HTTP 端点 `POST/PUT /pages` 直接落库） | C1 前置 / C6 | §2.4–2.5 |
| 🔴 高 | **Claim 语义契约未冻结**（无 reason code、ClaimRelation 非枚举、无决策矩阵文档） | C1 | §4.2 / D2 / D8 |
| 🔴 高 | **多对象事务无严格 staging + 无崩溃恢复**（半写风险） | C3 | §2.1 / D1 |
| 🔴 高 | **Claim 专项黄金评测集不存在**（语义准确率无法量化，contradicts/supersedes 误判会污染 Canonical Store） | C2 | §8.1 |
| 🟡 中 | **读路径三套未统一 + WikiQueryService 缺失 + wiki_pages_v2_fts 零读取** | C4 | §3 / D4 |
| 🟡 中 | **配置状态机缺 off/shadow/canary/primary**（仅二值，无法灰度） | C5 | §5.3 / D6 |
| 🟡 中 | **wiki_eval 崩溃**（阻塞 C2 wiki 指标度量） | C2 前置 / 基线修复 | §8 / D9 |
| 🟡 中 | **locator 全局依赖违规**（2 行修复，但属 C6 边界） | C6 | §7.2 / D7 |
| 🟢 低 | **locator FS-first**（与 spec 反向，C4 统一端口时一并修正） | C4 | §3.3 / D5 |
| 🟢 低 | **ruff 7 + mypy 1 基线债务**（与 wiki-v2 无关，但阻塞门禁 0 要求） | C1–C6 期间清理 | §8 |
| 🟢 低 | **repositories/wiki_repo.py 第二个 WikiRepository 潜伏写路径** | C6 / Phase 4 | §2.4 |
| 🟢 低 | **validator 覆盖不足** | Phase 6 T6.2 | §4.4 / D10 |
| 🟢 低 | **PROGRESS.md 过时**（未记 wiki-v2 进展） | C0 后更新 | D12 |

---

## 12. 下一步：C1 执行建议

C1（冻结 Claim 语义契约）建议执行顺序：

1. **先补守卫覆盖**（C1 前置，暴露 D3）：扩展 `tests/test_canonical_write_guards.py` 的 `GUARDED` 为全 `src/` 扫描（至少纳入 `api/routes/wiki.py`、`wiki_lint.py`、`wiki_workflow.py`），并让 AST 探测识别 `open("a")` 写与裸 SQL `INSERT/UPDATE INTO wiki_pages`。**先写失败测试**（当前 11 处越界写会让守卫红），再按"过渡 allowlist"逐条登记（每条注明移除阶段），**不得**直接改造业务代码（那是 Phase 4 范围）。
2. **新增契约文档** `docs/architecture/wiki-v2-claim-merge-contract.md`：定义 duplicate/supports/refines/contradicts/supersedes/new/unresolved 的严格决策矩阵（Phase 3.5 §C1 已给正例反例）+ 稳定 reason code 枚举（`EXACT_NORMALIZED_MATCH` / `SCOPE_MISMATCH` / `NUMERIC_CONFLICT` / `UNIT_INCOMPATIBLE` / `EXPLICIT_REPLACEMENT` / `AMBIGUOUS_CANDIDATES` / `INSUFFICIENT_EVIDENCE` / `LOW_CONFIDENCE` 等）。
3. **代码侧统一枚举**：把 `ClaimRelation.relation`（自由 str）与 `ClaimMatchDecision.action` 收敛到共用枚举；matcher 的 `reasons` 在自然语言之外**追加**稳定 reason code 字段（保持现有自然语言 reasons 以兼容已写测试，新增 code 字段）。
4. **迁移现有测试**到统一 reason code（`test_wiki_claim_matcher` / `test_wiki_merge_engine`），不降低断言。
5. **保守策略复核**：当前 matcher 的 exact-hash + object_refs 不同 → 直接 `contradicts`(score=1.0)（`wiki_claim_matcher.py:115-128`）有一定激进性，需对照 C1 决策矩阵审视（不同型号/地区/时间默认不得判矛盾），不确定时回落 `unresolved`。
6. 每个 Task 独立 commit（`docs(wiki-v2): freeze claim merge semantics` 等），跑守卫+ruff+mypy+相关 pytest，做规格一致性 + 代码质量两轮 Review，更新本审计文档状态。

**C1 不碰**：transaction 严格化（C3）、黄金评测集（C2）、读端口统一（C4）、配置状态机（C5）—— 严格按 C0→C1→C2→…→C6 顺序。

---

## 附：C0 Commit

```
docs(wiki-v2): audit current canonical implementation
```

本次 commit 仅新增本审计文档 + 刷新 `artifacts/eval/canonical-v2-baseline.json`（重跑确认当前基线），不改任何业务代码。分支 `canonical-wiki-v2-correction`。
