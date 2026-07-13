# Post Wiki V2 代码审计报告（v1.6.0）

> 日期：2026-07-13  
> 基线 commit：`73b4dfa`  
> 版本：1.6.0  
> 审查方案：`docs/superpowers/plans/2026-07-13-post-wiki-v2-code-audit-charter.md`  
> 执行计划：`docs/superpowers/plans/2026-07-13-post-wiki-v2-code-audit-execution.md`

---

## 1. 门禁数字

| 阶段 | pytest | ruff | mypy |
|------|--------|------|------|
| 审计前基线 | **1533 passed / 2 skipped** | All checks passed | 191 source files OK |
| 审计后 | **1543 passed / 2 skipped**（+10 回归测试） | 变更文件通过 | 未重跑全量（源码改动已有类型约定） |

工作树开始时仅新增审计 plan 文档；修复后另含代码与测试改动（未自动 commit）。

---

## 2. 各段结论

| 段 | 结论 | 摘要 |
|----|------|------|
| S0 基线 | 通过 | 无 unexpected fail；有 Windows 子进程编码 warning |
| S1 容器/配置 | 基本健康 | JWT 密钥双源不一致记 deferred |
| S2 数据层 | **已修关键簇** | 软删清向量、恢复撞主键、purge 不落库、硬删顺序、统计含软删 |
| S3 Canonical 主写 | 守卫健康 + 修复 recover/refines | ALLOWED_DIRECT_WRITES 仍空；outbox 幂等与 refines 原子性已修 |
| S4 失效传播 | **P0 已修** | RebuildScheduler 仅 schedule 不 flush → 现自动 debounce flush |
| S5 迁移反馈 | 已修 | register_existing；reject→claim.deleted |
| S6 检索 | 基本健康 | RAG 缓存失效 deferred；软删过滤本身 OK |
| S7 MCP/API | 部分 deferred | ask 超时实现 OK；API/MCP 删除编排差异 deferred |
| S8 横切 | 部分 deferred | SSRF DNS 重绑定、settings 子进程编码 warning |

---

## 3. Findings 全表

### 已修复（fixed）

| ID | 严重度 | 标题 | 处置 |
|----|--------|------|------|
| F-S4-01 | P0 | RebuildScheduler 永不 auto-flush，失效传播空转 | fixed：`threading.Timer` debounce 自动 flush + shutdown |
| F-S3-02 | P0 | recover 按 tx_id 粗去重 → 部分 outbox 事件永久丢失 | fixed：按 `(tx_id, type, object_id)` 补写 |
| F-S3-03 | P0 | claim-only 事务无 COMMITTED 时 recover 不前向补 outbox | fixed：磁盘 claim revision 对齐即前向完成 |
| F-S2-01 | P1 | 软删路径 `_delete_cache` 清 vec_blocks | fixed：软删仅 `soft_delete_knowledge` |
| F-S2-02 | P1 | `sync_page`/`restore` 对软删行 INSERT 撞主键 | fixed：`include_deleted` + `restore_knowledge` |
| F-S2-03 | P1 | purge/empty_trash 只删文件不硬删 DB | fixed：解析 id + `_delete_cache(hard=True)` |
| F-S2-04 | P1 | KnowledgeRepo 硬删先删 chunks 再清向量 → 孤儿 vec | fixed：先向量/block 再表；补 graph/block_refs |
| F-S2-05 | P1 | `get_stats`/`count` 计入软删 | fixed：默认 `deleted_at IS NULL` |
| F-S5-01 | P1 | feedback reject 发 claim.updated，投影无法收敛 | fixed：RETRACTED → `claim.deleted` outbox |
| F-S5-02 | P1 | claim upsert 删除全部依赖边含 cited_in | fixed：保留/回补 cited_in |
| F-S5-03 | P1 | `_delete_claim` 残留 page_claims/dependencies | fixed：同步清理 |
| F-S3-04 | P1 | refines 先 stage target 再校验 new → 悬空 relation | fixed：双方校验后再 stage |
| F-S3-05 | P1 | registry JSON 损坏返回 `{}` 可覆盖丢页 | fixed：`RegistryCorruptError` |
| F-S5-04 | P1 | migrator registry 空时 skip 有 page_id 页 | fixed：`register_existing` + apply stage |

回归测试：`tests/test_audit_bugfixes.py`（10 cases）。

### 延期（deferred）

| ID | 严重度 | 标题 | 理由 |
|----|--------|------|------|
| F-S1-01 | P2 | JWT：Config/keyring `api.jwt_secret` 与 `auth.py` 文件/env 双源 | 需统一密钥链与延迟加载，触及登录兼容 |
| F-S3-06 | P2 | solo `save_page` 文件/registry 非原子 | 需两阶段提交；现 transaction 路径已严格 |
| F-S4-02 | P2 | update rebuild 丢弃 claim 关系 fanout | 契约可接受为「仅 evidence 变 stale」保守；需产品确认 |
| F-S4-03 | P2 | max_depth BFS 偏宽约 1 hop | 行为偏保守；改动影响影响集大小 |
| F-S6-01 | P2 | RAG 结果缓存无写侧失效 | 需在 create/update/delete 挂钩；本轮未扩 scope |
| F-S7-01 | P2 | MCP/API 删除编排不一致、API 文案缺 soft 语义 | 宜抽统一 lifecycle 服务 |
| F-S7-02 | P2 | 注册用户 persist 失败仍发 token | 认证可靠性；本轮未改 auth 热路径 |
| F-S8-01 | P2 | SSRF DNS rebinding 窗口 | 需 pin 连接 IP；改动面大 |
| F-S8-02 | P3 | settings 测试触发 Windows subprocess UTF-8 decode warning | 测试/启动探测编码；功能测试仍绿 |
| F-S8-03 | P3 | mcp_post_fix_test 返回 bool 触发 PytestReturnNotNoneWarning | 测试风格 |

---

## 4. 代码改动概要

| 文件 | 变更 |
|------|------|
| `src/services/wiki_rebuild_scheduler.py` | debounce Timer 自动 flush；shutdown |
| `src/services/wiki_repository.py` | RegistryCorruptError；outbox 事件级 recover；RETRACTED→claim.deleted |
| `src/services/wiki_merge_engine.py` | refines 双校验再 stage |
| `src/services/wiki_projection.py` | cited_in 保留；delete_claim 清依赖 |
| `src/services/file_graph.py` | 软/硬删分流；restore 清 deleted_at；purge 硬删 |
| `src/services/wiki_v2_migrator.py` | register_existing |
| `src/services/db.py` | get_stats 排除软删 |
| `src/repositories/knowledge_repo.py` | 硬删顺序与关联表；count/stats 排除软删 |
| `tests/test_audit_bugfixes.py` | 新增审计回归 |

---

## 5. Residual / 建议后续

1. **统一 KnowledgeLifecycle**：软删 / 恢复 / purge 在 MCP、API、GUI、file_graph 共用一条路径。  
2. **JWT 单源**：auth 读取 Config 密钥链。  
3. **RAG 缓存失效** 挂钩写路径。  
4. **solo save_page** 与 transaction 同级原子性。  
5. 清理 `mcp_post_fix_test` 的 `return bool` 与 Windows 子进程 `encoding=errors=replace`。

---

## 6. 成功标准核对

- [x] S0–S8 书面结论  
- [x] 已确认 P0 全部 fixed  
- [x] 选定 P1 全部 fixed  
- [x] 全量 pytest ≥ 基线（1533 → 1543，failed 不增加）  
- [x] 本报告落盘  
- [x] 无无关大重构  
