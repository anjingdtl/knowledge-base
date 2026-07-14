# Spec 1：行为契约冻结与请求状态隔离

> **建议路径：** `docs/superpowers/specs/2026-07-14-maintainability-phase-1-contract-isolation.md`  
> **建议版本：** `v1.8.1`  
> **工期定位：** 安全地基建设。本期完成前，禁止开始 Retrieval、Wiki、MCP 或数据库架构调整。

## 1. 背景

当前 Search 和 Verified Answer 之间存在隐式数据传递：

```text
执行 Search
→ SearchService 保存 last_search_trace
→ SearchService 保存 last_disclose_claims
→ VerifiedAnswerService 再读取这些字段
```

这种设计在单线程调用中通常能够工作，但在并发请求中存在请求状态互相覆盖的风险，也增加了理解和测试成本。

同时，后续任何 Retrieval 或 Wiki 重构都需要一套稳定的行为基线，否则无法判断重构造成的是内部变化还是用户可观察行为退化。

## 2. 目标

本期只完成两件事：

1. 冻结 Search、Ask、Wiki Serving 的现有行为契约；
2. 将一次搜索产生的结果、Trace、Claim、Conflict 和 Fallback 放入同一个请求级返回对象。

本期完成后：

```text
Search 输入
→ SearchExecution
→ Answer 组装
```

不得再通过 Service 的“上一次请求状态”传递数据。

## 3. 非目标

本期明确不做：

- 不统一 Raw 和 Verified Retrieval；
- 不抽取 VerifiedProvider；
- 不修改 Wiki Serving Gate；
- 不修改 Claim、Evidence、Canonical Wiki；
- 不修改检索排序、RRF、Rerank 或 Citation；
- 不拆分 MCP Server；
- 不调整 Container；
- 不修改数据库 Schema；
- 不删除 Legacy Retrieval；
- 不新增产品能力。

## 4. 目标数据结构

创建 `src/models/search_execution.py`：

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class SearchExecution:
    results: tuple[dict[str, Any], ...]
    trace: dict[str, Any] = field(default_factory=dict)
    disclose_claims: tuple[dict[str, Any], ...] = ()
    conflicts: tuple[dict[str, Any], ...] = ()
    fallbacks: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
```

约束：

- 一个 `SearchExecution` 只能属于一个请求；
- 外层结构不可变；
- Trace、Claim、Conflict 和 Fallback 必须与 Results 同时生成；
- 不允许调用方在搜索结束后再读取 SearchService 内部状态；
- 不改变现有公开 MCP 返回结构。

## 5. 工作范围

### Task 1.1：冻结 Search 契约

创建：

```text
tests/test_public_search_contract.py
tests/snapshots/search_raw.json
tests/snapshots/search_verified.json
tests/snapshots/search_raw_fallback.json
tests/snapshots/search_no_result.json
```

覆盖：

- Raw 正常检索；
- Verified Claim 增强；
- Wiki 不可用时 Raw fallback；
- Vector 不可用时关键词降级；
- Rerank 不可用时保留候选；
- 无结果；
- Citation 完整性；
- Trace 基本字段。

快照忽略时间、随机 ID、非确定性浮点尾数和 LLM 文本措辞。

### Task 1.2：冻结 Ask 契约

创建：

```text
tests/test_public_ask_contract.py
tests/snapshots/ask_hybrid_verified.json
tests/snapshots/ask_raw_only.json
tests/snapshots/ask_conflict.json
tests/snapshots/ask_no_answer.json
tests/snapshots/ask_timeout.json
```

必须冻结：

```text
answer
answer_mode
sources
claims_used
raw_evidence_used
conflicts
fallbacks
warnings
trace_id
route
```

### Task 1.3：冻结 Wiki Serving 不变量

创建：

```text
tests/test_wiki_serving_contract.py
docs/architecture/wiki-invariants.md
```

必须固化：

```text
WIKI-001 Raw Evidence 是最终证据底座
WIKI-002 Claim 必须具有可解析 Evidence
WIKI-003 stale Claim 不得进入可靠主结论
WIKI-004 unsupported Claim 不得进入可靠主结论
WIKI-005 retracted Claim 不得进入可靠主结论
WIKI-006 conflict 必须披露，不得静默消失
WIKI-007 Wiki 故障必须降级 Raw
WIKI-008 Projection 不是 Canonical 权威
WIKI-009 Serving 与 Authoring 权限分离
WIKI-010 Auto Publish 默认关闭
```

### Task 1.4：增加 `SearchService.execute()`

修改 `src/services/search_service.py`，新增：

```python
def execute(
    self,
    query: str,
    top_k: int = 5,
    query_spec=None,
) -> SearchExecution:
    ...
```

保留兼容入口：

```python
def search(
    self,
    query: str,
    top_k: int = 5,
    query_spec=None,
) -> list[dict]:
    return list(
        self.execute(
            query=query,
            top_k=top_k,
            query_spec=query_spec,
        ).results
    )
```

第一步不得删除旧 `search()`，避免扩大调用方改动。

### Task 1.5：迁移 Verified Answer

修改 `src/services/verified_answer.py`：

```python
execution = self._search.execute(
    question,
    top_k=top_k,
)

payload = assemble_answer_payload(
    question=question,
    search_results=list(execution.results),
    search_trace=execution.trace,
    disclose_claims=list(execution.disclose_claims),
    conflicts=list(execution.conflicts),
    fallbacks=list(execution.fallbacks),
)
```

禁止继续读取 `last_search_trace` 和 `last_disclose_claims`。

### Task 1.6：并发隔离测试

创建 `tests/test_search_request_isolation.py`。

测试至少同时执行 50 个请求。每个请求注入不同的 query、trace ID、disclose Claim、conflict、fallback 和 source ID。

断言：

```text
Trace 串线数 = 0
Claim 串线数 = 0
Conflict 串线数 = 0
Fallback 串线数 = 0
Citation 串线数 = 0
```

测试至少重复运行 100 轮，或通过参数化覆盖足够并发组合。

### Task 1.7：删除共享请求状态

只有在所有生产调用方完成迁移后，才删除：

```text
SearchService.last_search_trace
SearchService.last_disclose_claims
SearchService.get_disclose_claim_rows()
```

删除前必须全仓库搜索调用方。

## 6. Agent 执行顺序

- [ ] 建立 Search 契约快照；
- [ ] 建立 Ask 契约快照；
- [ ] 建立 Wiki Serving 不变量测试；
- [ ] 新增 `SearchExecution`；
- [ ] 新增 `SearchService.execute()`；
- [ ] 迁移 VerifiedAnswerService；
- [ ] 增加并发隔离测试；
- [ ] 搜索并迁移剩余调用方；
- [ ] 删除 `last_*` 状态；
- [ ] 运行全量测试和质量评测；
- [ ] 生成一期验收报告。

## 7. 验收标准

本期完成必须同时满足：

- Search 契约快照通过；
- Ask 契约快照通过；
- Wiki Serving 契约通过；
- 50 并发请求无状态串线；
- Search 和 Ask 用户可观察行为不变；
- Retrieval Eval 不下降；
- Hybrid Eval 不下降；
- 生产代码不读取 `last_search_trace`；
- 生产代码不读取 `last_disclose_claims`；
- MCP Tool Contract 不变化；
- 数据库文件和 Schema 无变化。

## 8. 回滚策略

本期必须保留 `SearchService.search()` 兼容入口。

若上线后出现问题：

1. 回滚 VerifiedAnswerService 对 `execute()` 的调用；
2. 恢复旧 `search()` 内部实现；
3. 保留契约测试，不需要回滚测试；
4. 不涉及数据迁移，无数据恢复要求。

## 9. 与第二工期的承接关系

### 本期向第二工期交付

```text
SearchExecution
Search 契约快照
Ask 契约快照
Wiki Serving 契约
并发隔离测试
```

### 第二工期只能依赖

- `SearchExecution` 的字段和语义；
- 已冻结的 Search/Ask/Wiki 行为契约；
- 请求隔离保证。

### 第二工期禁止做

- 重新增加 Service 内部请求状态；
- 修改 `SearchExecution` 已冻结字段语义；
- 绕过 Wiki Serving 契约；
- 为简化 Orchestrator 删除 Conflict 或 Fallback 信息。

### 进入第二工期的准入条件

```text
全量测试通过
并发隔离测试通过
Retrieval Eval 无退化
Hybrid Eval 无退化
Search/Ask/Wiki 契约稳定
v1.8.1 已完成发布或候选版本验证
```

若其中任意条件失败，第二工期暂停。

## 10. 推荐提交拆分

```text
test(contract): freeze search response behavior
test(contract): freeze ask and wiki serving behavior
feat(search): add request-scoped SearchExecution
refactor(answer): consume request-scoped search output
test(search): prove concurrent request isolation
refactor(search): remove shared last-request state
docs(architecture): record phase-1 contracts and handoff
```
