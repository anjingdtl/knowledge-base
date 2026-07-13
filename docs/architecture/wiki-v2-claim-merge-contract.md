# Wiki V2 Claim Merge 语义契约（冻结）

> **状态：** Frozen（Phase 3.5 / C1）
> **生效范围：** `WikiClaimMatcher`（`src/services/wiki_claim_matcher.py`）、`WikiMergeEngine`（`src/services/wiki_merge_engine.py`）、`ClaimExtractor`（`src/services/wiki_claim_extractor.py`）
> **铁律：** Matcher 与 Merge Engine **共用同一份 action 枚举与 reason code**，**不得各自实现 normalize**。保守原则优先——无法可靠判断时一律 `unresolved`，宁可漏合并进人工审阅，不可错误合并污染 Canonical Store。

---

## 1. ClaimMergeAction 枚举（唯一 action 真源）

定义于 `src/services/wiki_claim_matcher.py`：

| action | 含义 | 自动允许？ |
|---|---|---|
| `new` | 现有 Claim 中无足够接近且作用域兼容的对象 | ✅ |
| `supports` | 新旧语义等价 + 作用域/时间兼容 + 新 Evidence 不同 → 为现有 Claim 加 Evidence | ✅ |
| `refines` | subject/predicate 一致 + 新 Claim 增限定/精度/范围/单位 + 旧在新区间仍成立 | ✅（数值/型号/地区/单位/时间不同时**不得**自动判 refines） |
| `contradicts` | subject+predicate 相同/互斥 + 作用域重叠 + 时间重叠 + object/极性/数值/关系不能同时成立 | ⚠️ 高风险，宁回落 unresolved |
| `supersedes` | 明确替代信号（新标准废止旧/新版本声明替代/明确生效时间/来源写"替代/废止/自某日起执行"） | ⚠️ 高风险，仅凭"时间更晚/数字更大/LLM 觉得更合理"**不得**自动 supersede |
| `duplicate` | 语义等价 + 作用域等价 + 时间范围等价 + Evidence 唯一键已存在 | ✅（重复 Evidence 不再写入） |
| `unresolved` | 缺时间/作用域、单位不清、subject_refs 不可靠识别、多候选分数接近、可能 contradict 也可能不同场景、Evidence 质量不足、LLM 与规则不一致、低于自动阈值 | ✅（**默认**，进 review） |

### 1.1 不得自动判 `refines` 的情况

- 数值直接不同；
- 产品型号不同；
- 地区不同；
- 时间范围不同但未明确关联；
- 单位无法安全转换；
- 否定词不同。

→ 一律 `unresolved`。

### 1.2 不得自动判 `contradicts` 的情况

不同版本、不同时间、不同区域、不同产品型号、不同适用条件 → 默认**不**判矛盾，回落 `unresolved`。

### 1.3 不得自动判 `supersedes` 的情况

- 新来源发布时间更晚；
- 新数字更大；
- LLM 认为新说法更合理；
- 两条事实相似但措辞不同。

→ 必须有**明确替代信号**（"废止"/"替代"/"自某日起执行"）才允许自动 `supersedes`，否则 `unresolved`。

---

## 2. ReasonCode 枚举（稳定 reason code）

`ClaimMatchDecision.reasons`（自然语言，人类可读，兼容已有测试）之外，**必须**同时填充 `reason_codes`（稳定机器可读 code）。ReasonCode 定义于 `wiki_claim_matcher.py`：

| ReasonCode | 触发条件 | 通常伴随 action |
|---|---|---|
| `NO_CANDIDATES` | 无候选 Claim | new |
| `EXACT_NORMALIZED_MATCH` | sha256(normalized_statement) 完全一致 | duplicate / contradicts |
| `OBJECT_REFS_CONFLICT` | 双方 object_refs 集合不同（数值/实体差异），subject+predicate 一致 | contradicts |
| `LOW_CONFIDENCE` | best score < unresolved_threshold | new |
| `AMBIGUOUS_CANDIDATES` | unresolved_threshold ≤ score < semantic_threshold（语义灰区） | unresolved |
| `TEMPORAL_SUPERSEDES` | new.valid_from > target.valid_from/valid_to + subject+predicate 一致 + object 不同 | supersedes |
| `NEW_HAS_CONTRADICTS_EVIDENCE` | 新 Claim 自带 contradicts stance Evidence | contradicts |
| `REFINES_SUPERSET` | new.subject_refs 或 object_refs 是 target 的真超集 + predicate 一致 | refines |
| `SUPPORTS_FALLBACK` | 高语义相似但无 conflict/supersede/refine 信号 | supports |
| `INSUFFICIENT_EVIDENCE` | Evidence 质量不足（预留，extractor/match 检测） | unresolved |

> **C2 已落地：** `SCOPE_MISMATCH` / `UNIT_INCOMPATIBLE` / `POLARITY_MISMATCH` / `INTENSITY_MISMATCH` 由 matcher 规则启发式产出，并始终附带 `AMBIGUOUS_CANDIDATES` 回落 `unresolved`。  
> 仍为未来增强：`TIME_RANGE_MISMATCH` / `NUMERIC_CONFLICT`（细粒度数值区间）/ `EXPLICIT_REPLACEMENT`（自然语言“废止/替代”信号，非仅时间字段）。

---

## 3. ClaimRelation.relation 合法值

`ClaimRelation.relation`（`src/models/wiki_v2.py`）为 `str`（向后兼容已写 claim YAML），但**只允许**下列值，由 `WikiMergeEngine` 写入：

| relation | 含义 | 写入方 |
|---|---|---|
| `supersedes` | new Claim 替代 old（new→old 方向） | merge `_apply_supersedes` |
| `superseded_by` | old 被 new 替代（old→new 方向） | merge `_apply_supersedes` |
| `refines` | new 细化 old（new→old） | merge `_apply_refines` |
| `refined_by` | old 被 new 细化（old→new） | merge `_apply_refines` |
| `contradicts` | 互相反驳（预留，disputed 时可选） | — |

合法值集合定义为 `CLAIM_RELATION_KINDS`（`wiki_merge_engine.py`），新增 relation 必须先扩此集合 + 契约。

---

## 4. normalize 共用（禁止各自实现）

`normalize_statement(text)` 定义于 `src/models/wiki_v2.py`，是 matcher 与 extractor 的**唯一**归一化实现：

```python
def normalize_statement(text: str) -> str:
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)  # 去标点
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)                        # 合并空白
    return text
```

- `WikiClaimMatcher._exact_hash` 用 `normalize_statement`。
- `ClaimExtractor._normalize` 委托 `normalize_statement`。
- **禁止**在 matcher/extractor/merge_engine 各自重造归一化逻辑（C1 验收项）。

---

## 5. 决策流程（matcher.match 决策树）

```
无候选 → new (NO_CANDIDATES)
exact sha256(normalized) 命中:
    object_refs 双方都有且集合不同 → contradicts (EXACT_NORMALIZED_MATCH + OBJECT_REFS_CONFLICT)
    否则 → duplicate (EXACT_NORMALIZED_MATCH)
best_score < unresolved_threshold → new (LOW_CONFIDENCE)
unresolved_threshold ≤ best_score < semantic_threshold → unresolved (AMBIGUOUS_CANDIDATES)
best_score ≥ semantic_threshold:
    temporal supersedes 条件成立 → supersedes (TEMPORAL_SUPERSEDES)
    objects_conflict → contradicts (OBJECT_REFS_CONFLICT)
    new 有 contradicts evidence → contradicts (NEW_HAS_CONTRADICTS_EVIDENCE)
    refines 真超集 → refines (REFINES_SUPERSET)
    否则 → supports (SUPPORTS_FALLBACK)
```

**保守复核（C2 已收紧）：**

- exact-hash / 高语义 + object_refs 不同：
  - 单位不同（如 1Gbps vs 1000Mbps）→ `unresolved`（`UNIT_INCOMPATIBLE`）
  - 极性/否定（true vs false）→ `unresolved`（`POLARITY_MISMATCH`）
  - 同单位不同数值（100Mbps vs 200Mbps）→ `contradicts`（`OBJECT_REFS_CONFLICT`）
- 高语义 + 同 predicate + subject 无交集（型号/地区）→ `unresolved`（`SCOPE_MISMATCH`）
- 高语义 + 同 subject/predicate/object + 强度词不同（最高可达 vs 保证达到）→ `unresolved`（`INTENSITY_MISMATCH`）
- 决策树 step 5 在 supports fallback 前插入上述 demote 检查。

---

## 6. 验收对照（纠偏方案 §C1）

- ✅ 所有 action 有明确正例反例（§1）
- ✅ Matcher 与 Merge Engine 共用同一份枚举（ClaimMergeAction + ReasonCode，定义于 matcher，merge import）
- ✅ 不允许各自实现 normalize（§4，唯一 normalize_statement）
- ✅ 当前测试迁移到统一 reason code（test_wiki_claim_matcher 断言 reason_codes）
