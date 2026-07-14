# Wiki Serving 不变量（WIKI-001 … WIKI-010）

> Phase-1 行为契约冻结。任何 Retrieval / Wiki 重构不得破坏下列不变量。  
> 自动化：`tests/test_wiki_serving_contract.py`

## 不变量列表

| ID | 不变量 | 含义 |
|---|---|---|
| **WIKI-001** | Raw Evidence 是最终证据底座 | 可靠结论必须能落到原始文档/Block；无 Claim 时仍可 `raw_only` 作答 |
| **WIKI-002** | Claim 必须具有可解析 Evidence | 无 supports Evidence、block 不可解析、hash 失配的 Claim 不得进入主结论 |
| **WIKI-003** | stale Claim 不得进入可靠主结论 | `stale` Evidence 或 freshness 过滤后的过期 Claim 不可作为唯一主结论 |
| **WIKI-004** | unsupported Claim 不得进入可靠主结论 | `ClaimStatus.UNSUPPORTED` 被 Serving Gate 拒绝 |
| **WIKI-005** | retracted Claim 不得进入可靠主结论 | `ClaimStatus.RETRACTED` 被 Serving Gate 拒绝 |
| **WIKI-006** | conflict 必须披露，不得静默消失 | 冲突时 `answer_mode=conflict_disclosure`，`conflicts` 非空 |
| **WIKI-007** | Wiki 故障必须降级 Raw | Claim 检索异常永不阻断 Raw 通道 |
| **WIKI-008** | Projection 不是 Canonical 权威 | 投影/编译产物不是 Search/Ask 的 Serving 入口；权威为 Canonical Claim + Gate |
| **WIKI-009** | Serving 与 Authoring 权限分离 | Serving 只读（`WikiServingGate` / `list_servable_*`）；写操作走 Authoring 路径 |
| **WIKI-010** | Auto Publish 默认关闭 | 项目初始化与 verified 迁移默认 `wiki.auto_publish=false`；draft 不得 Serving |

## 实现锚点

| 能力 | 模块 |
|---|---|
| Serving Gate | `src/services/wiki_serving_gate.py` |
| Search 编排 | `src/services/search_service.py`（`execute()` → `SearchExecution`） |
| Ask 组装 | `src/services/verified_answer.py` |
| 请求级输出 | `src/models/search_execution.py` |

## 与 Phase-1 的关系

- Search/Ask 契约快照冻结用户可观察行为。
- `SearchExecution` 保证 trace / disclose / conflict / fallback 与 results 同请求返回，禁止 `last_*` 共享状态。
- 第二工期（Retrieval/Wiki 重构）必须以本文件与契约测试为回归基线。
