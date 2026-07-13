# Verified Hybrid 最终验收评审

> 日期：2026-07-13  
> 版本：1.7.0  
> Spec：`docs/ShineHeKnowledge 融合收束开发规格说明.md`
>
> **状态：Historical / Superseded by correction。** 本文保留 v1.7.0 的历史判断；因缺少严格 Validation/Review/Published Revision Gate、持久化维护闭环、真实 Raw/Hybrid A/B 及完整发布工程门禁，不能作为当前发布或收束完成证据。当前执行依据见 `docs/superpowers/specs/2026-07-13-verified-hybrid-convergence-correction-design.md` 与对应 PLAN。

## 1. 阶段完成度

| Phase | 状态 | 代表提交 |
|---|---|---|
| 0 基线 | ✅ | `abbfa35` |
| 1 模式 | ✅ | `737d0a9` |
| 2 Serving Gate | ✅ | `ae62767` |
| 3 统一检索 | ✅ | `3892ce0` |
| 4 回答/冲突/引用 | ✅ | `7a7ac69` |
| 5 维护中心 | ✅ | `cd2ed9a` |
| 6 MCP 边界 | ✅ | `0f5f5d9` |
| 7 Hybrid Eval | ✅ | 本轮 |
| 8 文档与发布 | ✅ | 本轮 |

## 2. Spec 核心问题回答

| 问题 | 结论 |
|---|---|
| Wiki 是否提升最终准确性？ | 在可 Serving Claim 场景 Hybrid 正确率 ≥ Raw（离线黄金集 175 例 hybrid=1.0 ≥ raw=1.0）；冲突/综合类依赖 Claim 结构 |
| 哪类问题获益？ | 定义/实体总结、跨文档综合、已验证数值 Claim |
| 哪类仍应优先 Raw？ | 时效敏感（当前/最新）、页码定位、Wiki 空/故障、stale/unsupported |
| Unsupported / Stale 是否 Serving？ | **否**（Gate + answer 装配；eval rate=0） |
| 引用可追溯？ | Claim 须带 Evidence；citation_correctness=1.0 |
| Wiki 故障降级？ | 是，raw fallback success≈1.0 |
| Authoring 默认隔离？ | 是；verified 默认 authoring_enabled=false、write_policy=disabled |
| 保护性维护安全？ | R1 自动且单调；R3 审阅；R4 人工 |
| 维护故障影响查询？ | 否（隔离懒加载 + 关闭开关） |
| 旧用户兼容？ | wiki_first→authoring、legacy→evidence_only，不改写磁盘配置 |
| **是否建议发布？** | **建议发布 v1.7.0** |

## 3. 门禁证据

```text
Hybrid Eval: 175 cases, overall PASS
  raw_correct=1.0 wiki_correct=1.0 hybrid_correct=1.0
  stale_serving_rate=0 unsupported_serving_rate=0
  citation_correctness=1.0 conflict_detection_recall=1.0
Full pytest: 见发布前回归记录
```

## 4. 残留风险

- 真实模型 E2E 答案质量需生产数据复验  
- 维护 Job 持久化可在后续小版本增强  
- README 历史版本徽章与健康段落需与 1.7.0 对齐（本轮已更新）

## 5. 建议

历史建议（已失效）：**Go for v1.7.0 release.**
纠偏工作已接管：真实 embedding 混合评测、Job/Review SQLite 持久化、严格 Serving Gate 和发布门禁均为当前必做项。
