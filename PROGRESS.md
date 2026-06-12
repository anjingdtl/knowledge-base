# ShineHeKnowledge 开发进度

> 最后更新: 2026-06-12
> 当前版本: v1.2.0 → v2.0.0 升级中
> 升级规格: `docs/superpowers/specs/2026-06-12-knowledge-base-upgrade-design.md`

---

## 总体进度

| 阶段 | 板块 | 目标版本 | 状态 | 完成时间 |
|------|------|----------|------|----------|
| Phase 1 | A1: 首次启动配置向导 | v1.3.0 | ✅ 已完成 | 2026-06-12 |
| Phase 2 | C2: 文件监控与增量索引 | v1.4.0 | ⬜ 未开始 | — |
| Phase 3 | B2: 多模态解析与可视化 | v1.5.0-a | ⬜ 未开始 | — |
| Phase 4 | C1: Obsidian 生态兼容 | v1.5.0-b | ⬜ 未开始 | — |
| Phase 5 | B1: GraphRAG 实体关系抽取 | v1.6.0 | ⬜ 未开始 | — |
| Phase 6 | D1: RBAC 权限隔离 | v2.0.0-a | ⬜ 未开始 | — |
| Phase 7 | A2: 零配置便携版 + 集成测试 | v2.0.0 | ⬜ 未开始 | — |

**整体进度: 1/7 阶段完成 (14%)**

---

## Phase 1: 首次启动配置向导 ✅

> Commit: `cca838c` — 2026-06-12

### 交付内容

| 文件 | 类型 | 行数 | 说明 |
|------|------|------|------|
| `src/utils/first_run.py` | 新增 | ~80 | 首次启动检测（标记文件 + API Key 占位符判断） |
| `src/gui/setup_wizard.py` | 新增 | ~590 | 4 步 PySide6 配置向导 |
| `src/app.py` | 修改 | +75 | 向导集成 + 示例知识包导入 |
| `src/data/samples/*.md` | 新增 | 5 文件 | 中文入门示例文档 |
| `tests/test_setup_wizard.py` | 新增 | ~170 | 单元测试 |

### 功能明细

- **首次启动检测**: `is_first_run()` 检查 `data/.first_run` 标记文件和 API Key 是否为占位符
- **4 步向导**:
  1. 欢迎页 — 功能亮点介绍
  2. AI 服务商选择 — 8 个预设模板（SiliconFlow / MiniMax / OpenAI / DeepSeek / 智谱 / Moonshot / Ollama / 自定义）
  3. 连通性测试 — 后台线程调用 Embedding API 验证，8 秒超时
  4. 配置摘要 — 可选导入示例知识包
- **安全存储**: API Key 通过 OS keyring 存储，不落盘 config.yaml
- **向后兼容**: 老用户不触发向导
- **优雅跳过**: 可随时跳过，在 Settings 中配置

---

## Phase 2: 文件监控与增量索引 ⬜

> 计划版本: v1.4.0 | 预估工作量: 2 周

### 待实现

- [ ] `pyproject.toml` 增加 `watchdog>=4.0` 依赖
- [ ] Alembic 迁移: `file_index` 表（文件路径、hash、索引状态）
- [ ] `src/repositories/file_index_repo.py` — 文件索引 CRUD
- [ ] `src/services/file_watcher.py` — watchdog 文件监控 + debounce
- [ ] `src/services/index_scheduler.py` — 增量索引调度器
- [ ] `src/core/container.py` — 注册新服务
- [ ] `config.yaml` 增加 `watcher` 配置节
- [ ] API 端点: `GET /api/indexer/status`, `POST /api/indexer/scan`
- [ ] GUI 状态栏索引指示器
- [ ] 测试覆盖

---

## Phase 3: 多模态解析与可视化 ⬜

> 计划版本: v1.5.0-a | 预估工作量: 1.5 周 | 依赖: Phase 2

### 待实现

- [ ] Alembic 迁移: `attachments` 表
- [ ] `src/services/image_analyzer.py` — 多模态 LLM 图片分析
- [ ] `src/services/attachment_store.py` — 附件存储管理
- [ ] `src/api/routes/attachments.py` — 附件 API
- [ ] React 客户端: `react-markdown` + `rehype-highlight` + 图片渲染
- [ ] 修改 `file_parser.py` 图片解析：元数据 → 智能分析

---

## Phase 4: Obsidian 生态兼容 ⬜

> 计划版本: v1.5.0-b | 预估工作量: 1.5 周 | 依赖: Phase 2 + 3

### 待实现

- [ ] `src/services/markdown_parser.py` — 双格式解析（Logseq + Obsidian YAML frontmatter）
- [ ] Alembic 迁移: `external_sources` 表
- [ ] `src/services/vault_sync.py` — Obsidian Vault 挂载与同步
- [ ] API 端点: `POST /api/sources`, `GET /api/sources`
- [ ] GUI 设置增加"数据源" Tab

---

## Phase 5: GraphRAG 实体关系抽取 ⬜

> 计划版本: v1.6.0 | 预估工作量: 3 周 | 依赖: Phase 2

### 待实现

- [ ] Alembic 迁移: `entity_triples`, `entity_index`, `community_summaries` 表
- [ ] `src/services/entity_extractor.py` — LLM 实体三元组抽取
- [ ] `src/services/entity_resolver.py` — 实体消歧与合并
- [ ] `src/services/community_detector.py` — 社区检测与摘要
- [ ] RAG 管线自定义阶段: `entity_extract` + `graph_retrieval`
- [ ] API 端点: 实体搜索、关系查询、社区列表

---

## Phase 6: RBAC 权限隔离 ⬜

> 计划版本: v2.0.0-a | 预估工作量: 1 周

### 待实现

- [ ] Alembic 迁移: `users` 表扩展（role, email, is_active）
- [ ] JWT payload 扩展: 增加 `role` 字段
- [ ] `src/api/permissions.py` — 角色权限装饰器
- [ ] 全路由添加权限标注
- [ ] 用户管理 API + GUI

---

## Phase 7: 零配置便携版 + 集成测试 ⬜

> 计划版本: v2.0.0 | 预估工作量: 1 周 | 依赖: Phase 1-6 全部完成

### 待实现

- [ ] `scripts/build_portable.py` — embeddable Python 便携版打包
- [ ] PyInstaller spec 修复（补齐 hidden imports）
- [ ] `scripts/install_windows.ps1` — 一键安装脚本
- [ ] 集成测试: 完整用户旅程 + 升级兼容性
- [ ] README / CLAUDE.md 文档更新

---

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.2.0 | 2026-06-10 | 当前稳定版: 45 MCP 工具, Neo4j 后端, 插件系统, Windows 服务 |
| v1.3.0 | 开发中 | Phase 1 完成: 首次启动配置向导 |
