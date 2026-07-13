# Verified Hybrid 收束纠偏 Phase 8 报告

> 日期：2026-07-14
> 状态：本地迁移、验收与文档完成；`master` 上的远端 Python matrix、Docker/API 健康检查、Windows 冒烟、前端、静态检查和检索评测均已通过。

## 迁移

- 新增 `VerifiedHybridConfigMigrator`：dry-run 零写、apply 创建字节备份并原子替换、rollback 恢复备份字节，未知字段保留。
- `shinehe config migrate-verified-hybrid` 支持 `--dry-run`、`--apply`、`--target-mode` 与 `--rollback`。
- Claim serving migration 的 dry-run 继续拒绝伪造 review/validation/publish 证明；不可证明的 Claim 保持非 Serving 并生成 review proposal。
- Canonical V2 与 Maintenance schema 的副本迁移/rollback 回归由 `test_wiki_v2_migration.py` 和既有 migrator tests 覆盖。

## 验收结果

```text
scripts/verified-hybrid-acceptance.ps1
37 passed
verified-hybrid release eval: total=60 raw=0.6667 hybrid=1.0000 lift=1.0000 overall_pass=true

retrieval eval: passed=true
hybrid eval: 175 cases, overall_pass=true
knowledge evolution eval: overall_pass=true
client npm ci && npm run build: passed
```

最终完整 pytest：`1693 passed, 2 skipped`（7 个历史测试返回值警告）。远端 CI 运行 `29267535492` 全绿；此前唯一的 PROGRESS 防误发布护栏已修复。工程发布门禁已满足，尚未创建 GitHub Release 标签。

## 文档与版本

- 版本提升至 `1.8.0`；前端作为 private 独立包保留 `1.4.0`，release notes 已明确。
- 新增 v1.7→v1.8 迁移手册、v1.8 release notes、最终评审、验收映射。
- v1.6→v1.7 文档标为历史资料；不再作为当前发布结论。

## 回滚

- 配置：使用 apply 输出的 `.bak` 和 `--rollback`。
- Canonical：`shinehe wiki migrate-v2 --rollback <timestamp>`。
- 代码：`git revert <phase8-sha>`；Maintenance 可通过 `maintenance.enabled=false` 停止而不影响 Raw。
