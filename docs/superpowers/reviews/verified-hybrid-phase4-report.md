# Phase 4 阶段报告：回答、冲突和引用

> 日期：2026-07-13  
> Spec：`docs/ShineHeKnowledge 融合收束开发规格说明.md` §7.7–§8 / §13.2 / Phase 4  
> 前置：Phase 0–3

---

## 1. 修改文件列表

| 文件 | 变更 |
|---|---|
| `src/services/verified_conflict.py` | **新增** 冲突检测 / 时效过滤 |
| `src/services/verified_answer.py` | **新增** answer 装配（answer_mode / claims_used / conflicts） |
| `src/services/citation_builder.py` | Claim + Evidence / Conflict 引用 |
| `src/services/search_service.py` | disclose 侧信道、时效过滤、冲突 trace |
| `src/services/verified_hybrid_fusion.py` | Claim citation 走 `build_claim_citation` |
| `tests/test_verified_answer.py` | **新增** Phase 4 测试 |

（MCP `ask`/`read` 接线见 Phase 6 提交中的 `mcp_server.py`）

## 2. 行为变化

- `answer_mode`: `hybrid_verified | raw_only | conflict_disclosure | no_answer`
- 冲突时 `conflict_disclosed=true`，并列披露双方 Evidence，不静默选边
- 时效敏感查询排除 stale Claim
- Claim 引用必须带 Evidence 链；裸 Claim 不作为主结论
- 无证据 → `no_answer`

## 3. 验收

| 项 | 状态 |
|---|---|
| 主结论不只有 Wiki 无 Evidence | ✅ |
| 冲突不静默选边 | ✅ |
| stale 不进最新性结论 | ✅ |

## 4. 测试

`pytest tests/test_verified_answer.py tests/test_verified_hybrid_search.py` 及相关套件通过。

## 5. 回滚

`git revert <phase4-sha>`；无 schema 变更。
