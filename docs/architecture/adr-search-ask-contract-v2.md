# ADR: Search / Ask 公开契约 v2

> 状态：Accepted（Phase 2–3 Task 2.0.2c / 2.1）
> 日期：2026-07-29
> 依据：`docs/superpowers/plans/2026-07-29-hit-rate-phase2-phase3-core-rebuild.md` §3.2 / §5 Task 2.1
> 配套：`docs/architecture/retrieval-answer-boundaries-v2.md`（边界与依赖方向）

## 1. 背景

Phase 0–1 之后全量 pytest 出现一组公开契约失败（contract snapshot 漂移、`raw_only` vs
`no_answer` 分歧、内部 `fetch_k` 被旧测试固化）。这些分歧来自 v1.10–v1.11 期间检索/回答
链路的有意演进（RawRetriever over-fetch、query variants、canonical snapshot、结构化事实
协议 SPEC v4/v6、low-confidence alias fallback）。本 ADR 冻结当前公开契约语义，作为：

1. 更新 contract snapshot 与契约测试的唯一依据（先有 ADR，后刷新 snapshot）；
2. Phase 2 UseCase/adapter 重构必须保持的对外兼容面；
3. Phase 3 质量重建不得破坏的公开行为边界。

## 2. 不变量（所有阶段必须保持）

- MCP 工具名、参数、Envelope 顶层结构不变。
- `search(top_k=5)` 公开返回最多 5 条（`public_top_k`）；内部 over-fetch 不改变公开上限。
- 公开 payload 只增不删字段；值语义改变必须再出 ADR。
- 内部 trace 字段（`trace.stages.*`）属可观测性，非公开契约，但 snapshot 冻结其确定性子集。
- 非确定性字段（耗时、进程级熔断状态、请求 ID）不进 snapshot。

## 3. Search 公开契约

### 3.1 Envelope

`search` 工具返回 `ok(...)` Envelope：

```jsonc
{
  "ok": true,
  "data": [ /* result items，最多 public_top_k 条 */ ],
  "meta": {
    "total_estimate": <int>,
    "top_k": <int>,            // = public_top_k
    "limit": <int>,            // top_k 别名
    "no_match": <bool>,
    "top_score": <float>,
    "threshold": <float>,
    "source_path": <string>,   // 见 §3.3
    "reason": <string>,        // no_match 时的稳定原因码
    "low_confidence": <bool>,  // 可选，仅 §3.3 第 2 档
    "intent": <string>,        // 可选
    "evidence_snapshot_id": <string>,   // 可选，accepted 时
    "snapshot_fingerprint": <string>,   // 可选
    "accepted_knowledge_ids": [...], "accepted_passage_ids": [...],
    "adjacent_unit": ..., "adjacent_count": ..., "adjacent_fallback_reason": ...
  }
}
```

### 3.2 Result item 字段

冻结（v1 既有）：`source`, `block_id`, `knowledge_id`, `title`, `text`, `score`,
`match_channels`, `warnings`, `citation`。

**有意新增（additive，冻结为契约）**：

- `candidate_type`：`raw_block` | `passage` | `claim`（候选形态）；
- `retrieval_unit`：`block` | `passage`（检索单元）；
- `passage_id`：passage 单元稳定 ID（`block:<kid>:<bid>` 或 passage 索引 ID）。

判定：三者均为**只增字段**，不改变旧字段值语义，Agent 可安全忽略。予以冻结。

### 3.3 no-match 三档语义（冻结）

| 档 | 条件 | `data` | `meta.no_match` | `meta.source_path` |
|---|---|---|---|---|
| 1 accepted | canonical snapshot gate 通过 | 正常结果（≤public_top_k） | `false` | `canonical_snapshot` |
| 2 low-confidence | gate 拒绝，但 alias/surface FTS 有表面命中 | 全部带 `low_confidence=true` 与 `confidence_reason` 的标记结果 | `false` | `fulltext_fallback_low_confidence` |
| 3 no match | 无任何命中 | `[]` | `true` | `canonical_snapshot`（`reason=all_candidates_below_threshold` 等） |

设计意图：**search 是探索性读操作**，允许返回明确标记的低置信命中供 Agent 参考阅读；
**ask 是结论性写操作**，answerability 只由证据门禁裁决，low-confidence 命中**不得**作为
回答依据（见 §4.3）。第 2 档不是降低门槛：gate 拒绝事实未被撤销，结果被显式降级标记。

特殊短路：当前信息类查询（`is_current_information_query`）直接第 3 档，
`reason=requires_current_external_data`。

### 3.4 SearchExecution（服务层公开模型）

frozen dataclass：`results`, `trace`, `disclose_claims`, `conflicts`, `fallbacks`,
`warnings`。`trace.mode` 冻结值：`legacy_raw`（evidence-only）、`hybrid_verified`、
`query_spec`、`preaccepted_snapshot`。

### 3.5 Snapshot 规范化规则（测试基础设施）

contract snapshot 比较前必须规范化：

- 删除所有层级的 `ms`、`elapsed_ms`、`created_at/updated_at/timestamp/request_id`；
- 删除进程级熔断瞬时状态 `trace.stages.rerank.circuit` 中的时间戳类字段
  （`open_until`、`last_probe_at`），仅保留确定性计数与布尔（或整体删除 circuit）；
- `query_fingerprint`（query 的 sha256 前缀）是确定性的，可保留；
- float 字段按 `_FLOAT_KEYS` 四舍五入；
- dict 递归按 key 排序。

判定：`search_raw.json` / `search_no_result.json` 的漂移全部为 §3.2 additive 字段与
§3.5 之前的 trace 扩展（`query_variants`、`pre_rerank_candidate_ids`、
`raw_retrieval.requested_pool_k`、`rerank.*`）。按本 ADR 规范化后刷新 snapshot。

## 4. Ask 公开契约

### 4.1 Payload 字段

冻结必需键：`answer`, `answer_mode`, `sources`, `claims_used`, `raw_evidence_used`,
`conflicts`, `fallbacks`, `warnings`, `trace_id`, `route`。

additive 冻结：`conflict_disclosed`, `freshness_sensitive`, `search_trace`, `reason`,
`answer_validation_decision`, `user_notice`, `numeric_fact_audit`, `claim_audit`,
`fact_candidate_audit`, `answer_plan`, `query_plan`, `evidence_groups`,
`render_validation`, `primary_group_id`, `source_graph`, `block_contexts`, `wiki_context`。

字段语义（冻结）：

- `claims_used`：最终答案使用的事实 claim。`hybrid` 模式来自 Wiki verified claim；
  `raw_only` 模式可包含从 raw 证据确定性抽取的结构化 claim。任何 claim 必须经
  `evidence_passage_ids` 绑定到证据 passage；无绑定 claim 不得进入答案。
- `raw_evidence_used`：实际支撑答案的 raw passage 列表（含 knowledge_id/block_id/
  excerpt）。`raw_only` 必须非空。
- `sources`：对外引用行，必须落在 snapshot/generation allowlist 内。

### 4.2 answer_mode 枚举（冻结）

- `hybrid`：verified claim + raw 融合回答；
- `raw_only`：无可用 claim，但 raw 证据通过证据门禁并抽出可 grounding 的事实；
- `conflict_disclosure`：冲突并列披露（`conflict_disclosed=true`）；
- `no_answer`：证据不足/不相关/事实槽位未覆盖/过程性 prose 被拒。

`clarification_required` 为 Phase 3 预留值；引入前不得出现在公开输出。

### 4.3 raw_only / no_answer 裁决规则（冻结）

1. raw 证据**与查询语义相关**且能抽出可 grounding 事实 → `raw_only`；
2. raw 证据与查询不相关、或事实槽位（subject/predicate/object/condition/scope/version）
   未覆盖 → `no_answer`（`reason=direct_slot_not_satisfied` / `no_fact_candidate` 等）；
3. low-confidence search 命中（§3.3 第 2 档）**不**构成 ask 的回答依据；
4. `no_answer` 时 `answer=""`、`sources=[]`、`raw_evidence_used=[]`，warnings 含
   `no_answer`；
5. 语义空占位文本（如 "raw only base"）不构成可回答证据——契约测试 fixture 必须使用
   可判断的 query/evidence。

### 4.4 timeout / generate-failure 语义（冻结）

回答主路径为**确定性结构化事实协议**（claim 抽取 → grounding → plan → render），
不依赖 LLM 自由生成。LLM 仅用于受约束 claim-JSON 抽取，且失败可降级：

- LLM claim-JSON 抽取不可用/超时/异常 → 回退确定性抽取，payload `fallbacks` 记录
  `{stage: "claim_extraction", type: "llm_unavailable_deterministic_fallback"}`，
  `warnings` 记录 `generate_failed:<reason>`（兼容旧观测键）；
- 确定性路径仍能产出 grounding 事实 → `raw_only`（不因 LLM 失败而拒答）；
- 确定性路径也无事实 → `no_answer`（reason 来自门禁，而非 LLM 失败）；
- 检索阶段超时/熔断 → `fallbacks` 记录 `rerank` / `hybrid` 降级（既有 reason code：
  `rerank_timeout:<fp>`、`rerank_circuit_open:<reason>`、`timeout_keep_order`、
  `deterministic_hybrid_fallback`）。

## 5. CandidatePoolPolicy（冻结定义）

```python
CandidatePoolPolicy(
    public_top_k=5,     # 公开返回上限（= 请求 top_k）
    fetch_k=20,         # 内部候选池：max(public_top_k * 4, 20)
    rerank_top_k=5,     # rerank 输出上限（= public_top_k）
    final_top_k=5,      # 打包/去重后最终上限（= public_top_k）
    max_per_document=3, # 单文档候选上限（raw 打包）
)
```

规则：

- fallback 与主路径使用**同一 policy**（BlockStore fallback 也按 `fetch_k` 取候选）；
- trace 记录 pre-rerank 池大小（`raw_retrieval.requested_pool_k`、
  `pre_rerank_candidate_ids`）与 post-rerank 输出（`rerank.output_candidate_ids`）；
- query variants 扩展进检索查询集（上限 6），属候选生成而非公开契约。

## 6. Heartbeat 契约（冻结）

- heartbeat 为**best-effort 观测性**装饰器：`beat()` 失败只记 debug 日志，绝不传播进
  被装饰工具；
- ping/search/ask 等只读工具的契约结果不得受 heartbeat 写入失败、Config 不可用或
  数据目录只读影响；
- GUI 状态三态分离：`installed`（服务注册）/ `running`（受管进程存活或 TCP 端口可达）/
  `available`（心跳新鲜或端口可达）。`running` 判定只信任受管子进程与 TCP 端口探测，
  不信任陈旧 PID 或陈旧心跳；周期状态检查不得启动 `sc.exe`/`wmic` 等外部进程。

## 7. 刷新与兼容规则

- 本 ADR 通过后，按 §3.5 规范化刷新 `search_raw.json` / `search_no_result.json`；
- ask 契约测试 fixture 按 §4.3/§4.5 改为语义可判断证据；
- 旧测试对内部调用参数（`_hybrid_search(..., 5)` / `block_store.search(top_k=5)`）的
  断言改为验证 §5 policy 语义与公开输出；
- 任何后续值语义变更必须先补充本 ADR 或新 ADR，再改 snapshot。
