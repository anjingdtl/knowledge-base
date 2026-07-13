# Verified Hybrid 收束纠偏 Phase 2 报告

> 日期：2026-07-13
> 状态：通过，可进入 Phase 3
> 提交：待提交

## 行为变化

- `ClaimServingValidation` 现作为 Claim revision 的持久化 Validation、Review 与 Publish 证明；旧 Claim 保持可读取，但严格 Serving Gate 默认拒绝其作为主结论。
- Serving Gate 对缺证明、未批准、过期 validated/published revision、空 serving evidence 与不可解析 Evidence 统一 fail closed，并保留 Raw fallback。
- Confirm 只记录 review approval；Validator 另行生成 serving evidence 与 validation proof；Publish 必须显式调用，且 Projection parity 失败时零写入。
- 新增只读 `WikiServingValidationMigrator` 与 `shinehe wiki serving-validation-migration --dry-run`，对无法证明的 Active Claim 仅生成 review proposal，绝不伪造历史 review/publish record；apply 仍锁定在 Phase 8。

## 验证

```text
python -m pytest tests/test_wiki_v2_models.py tests/test_wiki_repository.py tests/test_wiki_serving_gate.py tests/test_verified_hybrid_search.py tests/test_verified_answer.py tests/test_wiki_validator.py tests/test_wiki_validator_canonical.py tests/test_wiki_feedback_service.py tests/test_wiki_primary_workflow.py tests/test_wiki_serving_validation_migrator.py -q
89 passed

python evals/run_hybrid_eval.py --strict --json
175 cases; overall_pass=true; stale_serving_rate=0; unsupported_serving_rate=0; raw_fallback_success=1.0
```

## 兼容与回滚

新字段为 optional，旧 Claim 不会因读取失败而丢失。严格门禁仅缩小 Wiki Serving 面，任何证明缺失或维护失败都会继续走 Raw。回滚可撤销本阶段代码；不会自动改写现有 Claim、Raw 或用户配置。

## 已知后续

Maintenance Job、Review 与 Dead Letter 仍为进程内存储，尚不满足重启恢复、数据库幂等和 lease 要求；Phase 3 必须先替换这一事实存储，不能直接扩展自动化。
