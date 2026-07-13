# 代码审计分段审查方案（Post Wiki V2 / v1.6.0）

> 日期：2026-07-13  
> 版本：`src/version.py` → 1.6.0  
> 背景：Canonical Wiki V2 Phase 4C→6、C2 Matcher 收紧、MCP ask 超时加固等大规模改造后的系统性代码审计  
> 配套执行计划：`docs/superpowers/plans/2026-07-13-post-wiki-v2-code-audit-execution.md`

---

## 1. 审计目标

在**不大改架构、不引入新功能**的前提下：

1. 发现并修复**真实可复现**的功能/正确性/一致性/安全/资源类 BUG。
2. 验证近期改造是否破坏既有契约（MCP 工具、API、检索、Wiki 读写路径）。
3. 输出可归档的审计报告（发现、证据、修复、未修项与理由）。

**非目标（明确不做）：**

- 风格美化、大规模重构、性能优化（除非已构成正确性故障）
- 功能增强 / 新 API / 配置扩展
- 将历史 xfail 全部转绿（除非审计中确认已可安全修复且有回归测试）
- 从 `docs/archive/` 恢复旧待办

---

## 2. 改造风险地图（审计优先级依据）

| 风险带 | 改造来源 | 主要路径 | 优先 |
|--------|----------|----------|------|
| R1 主写切换 | Phase 4C Primary | `wiki_repository` / `wiki_projection` / `wiki_primary_workflow` / API wiki routes / lint / workflow | P0 |
| R2 失效传播 | Phase 5 | `wiki_dependency_service` / `wiki_rebuild_*` / delete 钩子 / CLI | P0 |
| R3 迁移反馈 | Phase 6 | `wiki_v2_migrator` / `wiki_feedback_service` / validator / evals | P0 |
| R4 Matcher | C2 收紧 | `wiki_claim_matcher` + 黄金集 | P1 |
| R5 MCP 稳定性 | ask timeout | `mcp_server` ask 路径 / worker 边界 | P1 |
| R6 双轨兼容 | shadow/canary/legacy 残留 | mode 切换、读 fallback、直接写守卫 | P0 |
| R7 基础设施 | 全期 | `container` / `db` / repos / search / auth | P1 |
| R8 外围 | GUI/docs | GUI 展示层、文档一致性 | P2 |

---

## 3. 分段划分（S0–S8）

每一段独立可验收：先建立基线 → 静态/动态审查 → 记录 findings → 仅对已确认 BUG 进入修复闭环。

### S0 — 基线与门禁快照（Baseline）

| 项 | 内容 |
|----|------|
| 范围 | 全量测试、静态检查、当前 git 干净度 |
| 入口 | `pytest tests/ -q`；`ruff check src tests`；`mypy src`（若环境可用） |
| 产出 | 基线数字（passed/failed/skipped/xfailed）；失败清单优先级排序 |
| 通过标准 | 基线数字落盘；任何 **unexpected fail** 直接进入修复队列 |

### S1 — 依赖注入与生命周期（Container / Config / Singletons）

| 项 | 内容 |
|----|------|
| 范围 | `src/core/container.py`、`src/utils/config.py`、`src/app.py`、`src/api/__init__.py` lifespan、`mcp_server` container fallback |
| 关注点 | 服务构造顺序；lazy 属性循环依赖；测试单例重置；配置缺省值与 mode 解析；密钥/keyring 失败路径 |
| 方法 | 读关键路径 + `tests/test_core.py` / `test_service_failure_config.py` / container 相关测试 |
| 严重度焦点 | 启动失败、服务拿到错误实例、测试污染 |

### S2 — 数据层与仓储（DB / FTS / Vector / Repositories）

| 项 | 内容 |
|----|------|
| 范围 | `src/services/db.py`、`vectorstore.py`、`block_store.py`、`src/repositories/*`、`alembic/` |
| 关注点 | 软删除过滤一致性；FTS 与向量不同步；事务边界；schema 迁移可回放；repo 与 db 双路径语义漂移 |
| 方法 | `tests/test_db.py`、`test_block_store.py`、`test_migration.py`、`test_search_excludes_soft_deleted.py`、`test_indexed_file_repo.py` 等 |
| 严重度焦点 | 数据丢失、软删泄漏、迁移破坏 |

### S3 — Canonical Wiki V2 主写与投影（Phase 4C 核心）

| 项 | 内容 |
|----|------|
| 范围 | `wiki_repository`、`wiki_projection`、`wiki_primary_workflow`、`wiki_merge_engine`、`wiki_claim_extractor`、`wiki_claim_matcher`、`wiki_write_service`、`wiki_workflow`、`wiki_compiler*`、`api/routes/wiki.py`、`test_canonical_write_guards.py` |
| 关注点 | `ALLOWED_DIRECT_WRITES` 为空仍扫描入口；projection 与 canonical 不一致；status 转换绕过 repository；matcher demote 误伤；事务失败半写入 |
| 方法 | 守卫测试 + wiki_v2 相关测试簇 + 静态搜索直接写 Markdown/DB |
| 严重度焦点 | 双写/漏写、错误 active claim、不可回滚状态 |

### S4 — 依赖图、失效传播与调度（Phase 5）

| 项 | 内容 |
|----|------|
| 范围 | `wiki_dependency_service`、`wiki_rebuild_service`、`wiki_rebuild_scheduler`、delete/update 触发、CLI rebuild |
| 关注点 | 影响集过宽/过窄；debounce 丢事件；cancel 半状态；stale evidence 未投影；删源后 claim 错误生命周期 |
| 方法 | `test_wiki_dependency_service`、`test_wiki_rebuild_*`、`test_wiki_v2_phase5_e2e` |
| 严重度焦点 | 陈旧知识仍 active、重建死锁/重复重建 |

### S5 — 迁移、反馈、校验（Phase 6）

| 项 | 内容 |
|----|------|
| 范围 | `wiki_v2_migrator`、`wiki_feedback_service`、`wiki_validator`、CLI `wiki migrate-v2/validate/claims` |
| 关注点 | apply 未加锁；rollback 不完整；误将 claim 标 active；feedback 写 Raw Source；strict validate 假阴性/假阳性 |
| 方法 | `test_wiki_v2_migrator`、`test_wiki_v2_migration`、`test_wiki_feedback_service`、`test_wiki_validator*` |
| 严重度焦点 | 迁移损坏生产数据、反馈污染源 |

### S6 — 检索 / RAG / 融合（非 Wiki 主路径）

| 项 | 内容 |
|----|------|
| 范围 | `hybrid_search`、`search_service`、`rag_pipeline`、`citation_builder`、`rerankers/*`、`parent_child_retrieval`、`wiki_parent_retrieval` |
| 关注点 | 软删进结果；citation 断链；RRF 空结果；阶段配置缺失崩溃；wiki stage 与 canonical 读路径 |
| 方法 | `test_search*`、`test_rag*`、`test_citation*`、`test_mcp_rag_full` |
| 严重度焦点 | 错误答案溯源、检索静默失败 |

### S7 — MCP / API / CLI 对外契约

| 项 | 内容 |
|----|------|
| 范围 | `mcp_server.py`、`mcp/tool_*`、`api/routes/*`、`api/auth.py`、`cli.py`、`mcp_config_templates/` |
| 关注点 | 工具 profile 漂移；错误 envelope 不一致；ask 超时/取消泄漏线程；写策略 `write_policy`；JWT/CORS；CLI 参数与服务层错位 |
| 方法 | `test_mcp_*`、`test_api.py`、`test_cli*`、`test_mcp_stability`、contract 快照 |
| 严重度焦点 | Agent 卡死、越权写、契约破坏 |

### S8 — 横切安全与一致性收口

| 项 | 内容 |
|----|------|
| 范围 | SSRF、路径穿越、SQL 动态拼装、异常吞没、竞态、资源泄漏、文档/版本一致性 |
| 关注点 | `parse_url` SSRF；裸 `except`/`pass` 掩盖故障；长时间持锁；`version.py` vs 文档 |
| 方法 | 定向 grep + 关键路径阅读 + `test_docs_consistency` / doctor |
| 严重度焦点 | 安全漏洞、静默数据损坏 |

---

## 4. Finding 分级标准

| 级别 | 定义 | 处理策略 |
|------|------|----------|
| **P0 Critical** | 数据损坏、错误主写、安全越权、测试门禁红灯且确认是产品缺陷 | 本轮必须修复 + 回归测试 |
| **P1 High** | 功能错误、契约破坏、可复现逻辑 bug、资源泄漏 | 本轮优先修复 |
| **P2 Medium** | 边界错误、可恢复失败、可观测性缺失导致难诊断 | 有把握则修；否则记入报告 |
| **P3 Low** | 代码异味、注释过时、非用户可见不一致 | 仅记录，**不**顺手大改 |

**修复准入门槛（全部满足才改代码）：**

1. 能描述触发条件或给出最小复现（测试或脚本）。
2. 能指出错误代码位置与期望行为。
3. 修复范围可手术式控制（Karpathy：Surgical Changes）。
4. 有验证命令（优先自动化测试）。

---

## 5. 每段审查工作流（统一）

```text
① 读该段关键模块 + 近期 commit 触达文件
② 跑该段相关 pytest 子集（记录 fail）
③ 静态检查清单（见执行 PLAN 各 Task）
④ 填写 Finding 表：ID / 段 / 严重度 / 证据 / 建议
⑤ 仅 P0/P1（及选定的 P2）进入修复 Task
⑥ 修复：先写/补失败测试 → 最小实现 → 复跑子集 → 必要时扩跑
⑦ 段末更新审计报告草稿
```

---

## 6. 产出物

| 产出 | 路径 |
|------|------|
| 本审查方案 | `docs/superpowers/plans/2026-07-13-post-wiki-v2-code-audit-charter.md` |
| 落地执行 PLAN | `docs/superpowers/plans/2026-07-13-post-wiki-v2-code-audit-execution.md` |
| 审计报告（执行后） | `docs/superpowers/reviews/2026-07-13-post-wiki-v2-code-audit-report.md` |
| 代码修复 | 对应 `src/` / `tests/` 手术式提交（按用户要求再 commit） |

---

## 7. 时间盒建议

| 段 | 建议时间盒 |
|----|------------|
| S0 | 15–25 min |
| S1–S2 | 各 30–45 min |
| S3–S5 | 各 45–75 min（改造核心） |
| S6–S7 | 各 30–45 min |
| S8 | 20–30 min |
| 修复批次 | 视 findings 数量；每修一项闭环验证 |

若某段时间盒内未穷尽，**优先深挖 P0 路径**，其余记入 Residual。

---

## 8. 成功标准

- [ ] S0–S8 均有书面结论（通过 / findings 列表 / 跳过原因）
- [ ] 所有 **已确认 P0/P1** 要么已修复并验证，要么有明确「暂不修」理由
- [ ] 全量 `pytest` 不低于审计前基线（failed 数不增加；xfail 不无故增加）
- [ ] 审计报告落盘且与 PROGRESS 可对账
- [ ] 无无关重构、无 scope creep
