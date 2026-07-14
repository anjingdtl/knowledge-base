# Handoff：Maintainability Phase-2 → Phase-3

> **日期：** 2026-07-14  
> **版本：** v1.8.2  
> **来源 Spec：** `docs/superpowers/specs/02-maintainability-phase-2-retrieval-wiki.md`  
> **目标 Spec：** `docs/superpowers/specs/03-maintainability-phase-3-application-infrastructure.md`

## 第三工期只能依赖

```text
RetrievalOrchestrator.search()
SearchExecution
EvidenceOnlyPolicy
VerifiedPolicy
VerifiedProvider
统一后的 SearchService Facade（execute / search）
```

## 第三工期不得重新调用

- Legacy Raw Search 私有方法（`_raw_retrieve` / `_hybrid_search` 等）作为业务入口
- Wiki Repository 的内部查询细节绕过 VerifiedProvider / Gate
- SearchService 历史状态字段（`last_*` 已删除）
- MCP Server 中的 Search 内部实现细节

## 当前默认行为

- `retrieval.orchestrator` 缺省 / example = **`legacy`**
- `unified` 与 `shadow` 已实现，可用于测试与灰度
- Legacy 主管线代码仍保留（`execute_primary_legacy` + `_search_*`）

## 切换建议

1. 测试 / 预发开启 `shadow`，观察日志 `retrieval_shadow`（无全文）
2. 门槛：Top-5 source overlap ≥ 95%、claim/conflict/fallback/citation 一致、无新异常
3. 达标后改 `unified`；保留 legacy 至少一个正式版本再删主管线

## 关键路径

| 路径 | 说明 |
|---|---|
| `src/retrieval/` | 编排包 |
| `src/services/search_service.py` | Facade + Legacy 实现 |
| `tests/retrieval/` | 二期门禁 |
| `config.example.yaml` | `retrieval.orchestrator` |
