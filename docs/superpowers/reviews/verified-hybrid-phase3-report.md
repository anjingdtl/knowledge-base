# Phase 3 阶段报告：统一检索编排（Verified Hybrid）

> 日期：2026-07-13  
> Spec：`docs/ShineHeKnowledge 融合收束开发规格说明.md` §7 / §12.5 / Phase 3  
> 前置：Phase 0–2（`abbfa35` / `737d0a9` / `ae62767`）  
> 本阶段提交：`3892ce0`

---

## 1. 修改文件列表

| 文件 | 变更 |
|---|---|
| `src/services/verified_query_router.py` | **新增** 规则优先 Query Router |
| `src/services/verified_hybrid_fusion.py` | **新增** 候选归一化 + RRF 融合 + 打包 |
| `src/services/search_service.py` | Verified Hybrid 编排主路径 + Trace |
| `tests/test_verified_hybrid_search.py` | **新增** 路由/融合/集成测试 |
| `docs/superpowers/reviews/verified-hybrid-phase3-report.md` | 本报告 |
| `PROGRESS.md` / `docs/README.md` | 进度索引 |

## 2. 行为变化摘要

当 `rag.verified_knowledge.enabled=true` 且知识模式允许 Wiki 读（verified/authoring）：

1. **Route** — 规则意图 → wiki/raw 权重（可与配置权重混合）
2. **并行** — Query rewrite ∥ Gate Claim 检索 ∥ Raw Hybrid（超时隔离）
3. **Gate** — 仅 Phase 2 可 Serving Claim
4. **Normalize** — Claim / Raw → 统一 candidate schema
5. **Fuse** — 通道内排序 + RRF（非分数直接相加）；Claim 证据块去重
6. **Package** — `source=verified_claim` 必须带 `evidence[]`；`source=knowledge` 为 Raw
7. **Trace** — `SearchService.last_search_trace`（route / stages / fallback / counts）

`evidence_only` 或 flag 关闭 → 原 legacy 管线（含旧 wiki FTS），行为兼容。

**Wiki 异常 / 超时：只清空 Claim 路，Raw 继续。**

## 3. 兼容性

| 场景 | 结果 |
|---|---|
| 未开 `verified_knowledge.enabled` | 原 search 测试路径不变 |
| evidence_only | 不走 fusion |
| MCP `search_service.search` | 仍返回 `list[dict]`；新增 source 值 `verified_claim` |
| 旧 `source=wiki` FTS | 仅 legacy 路径 |

## 4. 测试

```text
pytest tests/test_search_service.py tests/test_verified_hybrid_search.py \
       tests/test_wiki_serving_gate.py tests/test_blend_fusion.py -q
→ 39 passed
```

验收：

- Wiki 失败不影响 Raw ✅  
- evidence_only 与 verified 路径分离 ✅  
- Claim 结果必带 Evidence ✅  

## 5. 指标

未重写 Raw Hybrid/Rerank 算法；全量 Retrieval Eval 未强制重跑（建议发布前补跑）。

## 6. 风险

- Claim 排序目前为确定性 lexical overlap，非向量；依赖 Eval 再调权
- 二次 hybrid（改写后）可能增加延迟；rewrite 失败不影响首次 raw
- `disclose_only` Claim 暂不进主结果列表（Phase 4 冲突披露）

## 7. 回滚

```bash
git revert <phase3-sha>
```

无 schema 变更。

## 8. 明确未做（Phase 4+）

- ask 冲突披露产品化 / 单一结论禁止  
- Claim+Evidence Citation 全链路 UI/MCP 文案  
- read 扩展 Claim/Evidence  
- 维护中心  
