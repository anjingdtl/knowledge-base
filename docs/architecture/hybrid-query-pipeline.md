# Hybrid 查询管线

```text
User Query
  → Query Router（规则意图 + wiki/raw 权重）
  → 并行：Verified Claim 检索 ∥ Raw Hybrid（向量+FTS+RRF）
  → Wiki Serving Gate
  → Candidate Normalization
  → Conflict / Freshness Check
  → RRF 融合（非分数直接相加）
  → Context Assembly
  → Answer（answer_mode + citations）
```

## 编排源

**SearchService** 是唯一融合编排源；`VerifiedAnswerService` 在此之上装配 `ask` 结果。

## answer_mode

| 模式 | 含义 |
|---|---|
| `hybrid_verified` | Claim + Evidence |
| `raw_only` | 仅原始证据 / Wiki 降级 |
| `conflict_disclosure` | 冲突并列披露 |
| `no_answer` | 证据不足拒答 |

## 回滚

```yaml
rag:
  verified_knowledge:
    enabled: false
# 或
knowledge_workflow:
  mode: evidence_only
```
