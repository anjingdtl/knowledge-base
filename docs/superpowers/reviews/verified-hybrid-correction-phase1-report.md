# Verified Hybrid 收束纠偏 Phase 1 报告

> 日期：2026-07-13
> 状态：通过，可进入 Phase 2
> 提交：待提交

## 行为变化

- 新增 `EffectiveKnowledgeSettings`：显式字段优先，缺失字段按模式推导，且绝不写回用户配置；
- 旧 `wiki_first` 在缺失 `rag.verified_knowledge.enabled` 时，运行时仍解析为 `authoring + Verified Hybrid`；
- Search、Ask、MCP 工具筛选、Serving Gate 的运行时 Wiki Read、Maintenance Policy 和 Doctor 使用统一有效语义；
- `doctor --explain-config` 只输出脱敏后的 raw/resolved/source/warnings；
- `config migrate-verified-hybrid --dry-run` 只预览，不写文件；
- `config.example.yaml` 的 Canonical/Claim/Rebuild/Projection/Validation/Site 已移回 `wiki.*`，且 `mode: "off"` 保持字符串；
- 新建 authoring 配置（含 `--local`）默认 `local_confirm`，HTTP 写仍关闭。

## 验证

```text
python -m pytest tests/test_knowledge_settings.py tests/test_knowledge_mode.py tests/test_project_setup.py tests/test_docs_consistency.py tests/test_doctor.py tests/test_cli.py tests/test_verified_hybrid_search.py tests/test_verified_answer.py tests/test_mcp_write_policy_filter.py -q
138 passed

python -m ruff check <Phase 1 touched files>
All checks passed
```

## 兼容与回滚

旧模式别名、显式 write policy、未知字段均保留；只有运行时解析变化。回滚可撤销本阶段代码和配置示例，不会修改 `config.yaml`、数据库、Raw 或 Wiki 数据。

## 已知后续

全局 mypy 仍有既有错误；本阶段触及的 `search_service.py` 暴露其中 3 项，按 Phase 7 的全局门禁统一清零。严格 Claim Validation/Review/Published Revision Gate 尚未实施，Phase 2 开始前不得把现有 Claim 当作已完成收束的发布证据。
