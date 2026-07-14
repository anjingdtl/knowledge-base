# Spec 2：Retrieval 编排统一与 Wiki Serving 隔离

> **建议路径：** `docs/superpowers/specs/2026-07-14-maintainability-phase-2-retrieval-wiki.md`  
> **建议版本：** `v1.8.2`～`v1.8.3`  
> **工期定位：** 核心业务管线整理。本期只治理 Retrieval 和 Wiki Serving，不调整 Answer、MCP、Container 或数据库。

## 1. 前置条件

本 Spec 依赖第一工期已经完成：

- `SearchExecution` 成为请求级唯一返回对象；
- Search、Ask、Wiki 契约已经冻结；
- `last_search_trace` 和 `last_disclose_claims` 已停止使用；
- 并发隔离测试通过；
- Wiki Serving 不变量已形成自动化门禁。

如果第一工期尚未达到上述状态，本期不得启动。

## 2. 背景

当前 Raw Retrieval、Verified Hybrid、Wiki Serving、Fallback、Conflict 和 Citation 逻辑存在于较大的服务流程中。

本期不改变检索算法，而是将职责明确分成：

```text
Raw Retrieval
Verified Wiki Serving
Retrieval Policy
Retrieval Orchestrator
```

目标是让 Evidence-only 和 Verified 模式共享同一编排入口，而不是长期维护两套完整 Search 主管线。

## 3. 目标架构

```text
                  ┌───────────────────────┐
                  │ RetrievalOrchestrator │
                  └───────────┬───────────┘
                              │
              ┌───────────────┴───────────────┐
              │                               │
    EvidenceOnlyPolicy                 VerifiedPolicy
              │                               │
       RawRetriever                  RawRetriever
                                              +
                                      VerifiedProvider
                                              │
                                            Fusion
                                              │
                                       SearchExecution
```

重要定义：

- `RawRetriever` 是证据检索能力；
- `VerifiedProvider` 是受 Serving Gate 保护的 Wiki 读取能力；
- `VerifiedPolicy` 是正式产品能力；
- Wiki Authoring 不属于本期；
- Graph、Memory 不参与本期；
- Raw Evidence 始终存在于 Verified 输出中。

## 4. 非目标

本期明确不做：

- 不统一 Ask；
- 不重写 LLM Answer；
- 不拆分 MCP Server；
- 不改变 Tool Profile；
- 不修改 AppContainer；
- 不迁移数据库；
- 不修改 Canonical Wiki 写入；
- 不修改 Projection 写入；
- 不修改 Wiki Maintenance；
- 不修改 Claim 提取或自动发布；
- 不改变 RRF、Rerank 和 Citation 算法。

## 5. 工作范围

### Task 2.1：定义 Retrieval 内部契约

创建：

```text
src/retrieval/models.py
src/retrieval/execution.py
```

定义：

```python
@dataclass(frozen=True)
class RawRetrievalResult:
    candidates: tuple[dict, ...]
    trace: dict
    warnings: tuple[str, ...] = ()
    fallbacks: tuple[dict, ...] = ()

@dataclass(frozen=True)
class VerifiedServingResult:
    eligible_claims: tuple[dict, ...]
    disclose_claims: tuple[dict, ...]
    conflicts: tuple[dict, ...]
    trace: dict
    warnings: tuple[str, ...] = ()
    fallback_reason: str | None = None
```

`SearchExecution` 继续作为 Orchestrator 的最终返回契约。

### Task 2.2：抽取 `VerifiedProvider`

创建：

```text
src/retrieval/verified_provider.py
tests/retrieval/test_verified_provider.py
```

职责：

```text
查询候选 Claim
→ 应用 Serving Gate
→ 验证 Evidence 可解析性
→ 应用 Freshness 规则
→ 处理 Conflict
→ 输出 Eligible / Disclose / Conflict
```

不得负责：

- Raw Search；
- Vector Search；
- RRF；
- Rerank；
- Answer generation；
- MCP Envelope；
- Wiki 写入；
- Projection 更新；
- Claim 创建；
- Auto Publish。

第一阶段 `VerifiedProvider` 只包装现有 Wiki Repository 和 Serving Gate，不重写底层规则。

### Task 2.3：抽取 `RawRetriever`

创建：

```text
src/retrieval/raw_retriever.py
tests/retrieval/test_raw_retriever.py
```

职责：

```text
Query Rewrite
→ Hybrid Search
→ FTS/Vector fallback
→ RRF
→ Rerank
→ Diversity
→ Raw Citation
```

初期优先采用适配器：

```python
class RawRetriever:
    def retrieve(...):
        return self._legacy_raw_pipeline(...)
```

先建立边界，再逐步移动实现。不得在首次 PR 中同时搬迁全部 Raw 代码。

### Task 2.4：建立 Retrieval Policy

创建：

```text
src/retrieval/policies/base.py
src/retrieval/policies/evidence_only.py
src/retrieval/policies/verified.py
tests/retrieval/test_retrieval_policies.py
```

接口：

```python
class RetrievalPolicy(Protocol):
    def execute(
        self,
        query: str,
        *,
        top_k: int,
        query_spec=None,
        deadline=None,
    ) -> SearchExecution:
        ...
```

#### EvidenceOnlyPolicy

```text
RawRetriever
→ SearchExecution
```

#### VerifiedPolicy

```text
RawRetriever
+
VerifiedProvider
→ Fusion
→ SearchExecution
```

VerifiedPolicy 必须保证：

- Raw Retrieval 不因 Wiki 失败而失败；
- Wiki Claim 不得绕过 Serving Gate；
- Conflict 不得被普通排序吞掉；
- stale、unsupported、retracted Claim 不进入可靠结果；
- `fallbacks` 明确记录降级原因。

### Task 2.5：建立 `RetrievalOrchestrator`

创建：

```text
src/retrieval/orchestrator.py
tests/retrieval/test_orchestrator.py
```

职责：

1. 解析 knowledge mode；
2. 选择 Retrieval Policy；
3. 传递 Deadline；
4. 返回 `SearchExecution`；
5. 不生成 Answer；
6. 不构造 MCP Envelope。

目标接口：

```python
class RetrievalOrchestrator:
    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        query_spec=None,
        deadline=None,
    ) -> SearchExecution:
        ...
```

### Task 2.6：Shadow 双跑

创建：

```text
tests/retrieval/test_shadow_comparison.py
src/retrieval/shadow_comparator.py
```

提出配置：

```yaml
retrieval:
  orchestrator: legacy
```

支持三种状态：

```text
legacy   旧流程正式返回
shadow   旧流程正式返回，新流程仅对比
unified  新流程正式返回
```

Shadow 对比：

```text
Top-K Source ID
Claim ID
Conflict
Fallback
Citation
Answer 所需上下文字段
Latency
Exception 类型
```

不得在日志中记录完整敏感文档内容。

### Task 2.7：切换 SearchService 为 Facade

切换后：

```python
class SearchService:
    def execute(...):
        return self._orchestrator.search(...)
```

保留 `SearchService` 类，避免所有调用方同时迁移。旧分支在本期初期不得立即删除。

### Task 2.8：删除双主管线

必须经过以下过程：

```text
legacy
→ shadow
→ unified
→ 保留一个正式版本
→ 删除旧主管线
```

不能在首次启用 unified 时立即删除 Legacy。

## 6. Agent 执行顺序

- [ ] 核验第一期准入条件；
- [ ] 定义 `RawRetrievalResult` 和 `VerifiedServingResult`；
- [ ] 抽取 VerifiedProvider 适配器；
- [ ] 完成 Wiki Serving 契约测试；
- [ ] 抽取 RawRetriever 适配器；
- [ ] 建立 EvidenceOnlyPolicy；
- [ ] 建立 VerifiedPolicy；
- [ ] 建立 RetrievalOrchestrator；
- [ ] 建立 Shadow Comparator；
- [ ] 以 legacy 模式运行新结构；
- [ ] 启用 shadow 并收集差异；
- [ ] 达到切换门槛后启用 unified；
- [ ] 保留 Legacy 一个正式版本；
- [ ] 删除旧主管线；
- [ ] 生成二期验收与承接报告。

## 7. Wiki 保护门禁

本期每次 PR 必须覆盖：

```text
active Claim 正常增强
stale Claim 被过滤
unsupported Claim 被过滤
retracted Claim 被过滤
Evidence 丢失时 Claim 被过滤
Conflict 被披露
Projection 不可用时 Raw fallback
Wiki Repository 异常时 Raw 正常
Authoring 关闭不影响 Serving
Auto Publish 保持关闭
```

指标：

| 指标 | 门槛 |
| --- | ---: |
| Eligible Claim 一致率 | 100% |
| Conflict 一致率 | 100% |
| Fallback 一致率 | 100% |
| Unsupported Claim Serving Rate | 0 |
| Stale Claim Serving Rate | 0 |
| Raw Fallback Success Rate | 100% |
| Citation Completeness | 100% |
| Hybrid Eval | 不低于第一工期基线 |

## 8. Shadow 切换标准

只有同时达到以下条件，才能把正式模式从 `legacy` 改为 `unified`：

```text
Top-5 Source Overlap ≥ 95%
Eligible Claim 一致率 = 100%
Conflict 一致率 = 100%
Fallback 一致率 = 100%
Citation Completeness = 100%
Retrieval Eval 不下降
Hybrid Eval 不下降
P95 延迟增幅不超过约定预算
无新增超时和异常类型
```

若来源排序存在合理差异，必须逐条分析，不得简单放宽门槛。

## 9. 验收标准

本期完成后：

- Search 只有一个 Orchestrator；
- Raw Retrieval 算法只有一个权威实现；
- Wiki Serving 有独立 Provider；
- Evidence-only 与 Verified 通过 Policy 区分；
- `SearchExecution` 契约不变化；
- Search/Ask/Wiki 快照全部通过；
- Legacy 配置仍可回滚；
- Answer、MCP、Container 和数据库未发生架构性改动。

## 10. 回滚策略

正式运行至少保留：

```yaml
retrieval:
  orchestrator: legacy
```

若 Unified 模式出现问题：

1. 切回 `legacy`；
2. 保留新模块和 Shadow 记录用于诊断；
3. 不需要回滚数据库；
4. 不影响 Canonical Wiki；
5. 不影响 MCP 工具契约。

## 11. 与第一工期的承接关系

本期使用第一工期产物：

```text
SearchExecution
Wiki Serving Contract
Search Contract
并发隔离测试
```

本期不得修改这些契约的既有语义。

## 12. 向第三工期交付

第三工期只能依赖：

```text
RetrievalOrchestrator.search()
SearchExecution
EvidenceOnlyPolicy
VerifiedPolicy
VerifiedProvider
统一后的 SearchService Facade
```

第三工期不得重新调用：

- Legacy Raw Search 私有方法；
- Wiki Repository 的内部查询细节；
- SearchService 的历史状态字段；
- MCP Server 中的 Search 内部实现。

## 13. 进入第三工期的准入条件

```text
Unified Retrieval 已作为正式模式稳定运行
Legacy 回滚开关经过验证
Shadow 对比达到门槛
Wiki Serving 契约全部通过
Retrieval Eval 无下降
Hybrid Eval 无下降
至少完成一次候选版本或正式版本验证
```

若 Unified Retrieval 尚不稳定，第三工期不得开始统一 Answer。

## 14. 推荐提交拆分

```text
feat(retrieval): define raw and verified execution contracts
refactor(wiki): isolate verified serving provider
refactor(retrieval): add raw retriever boundary
feat(retrieval): add evidence-only and verified policies
feat(retrieval): introduce retrieval orchestrator
feat(retrieval): add shadow comparison mode
refactor(search): route facade through unified orchestrator
test(retrieval): enforce wiki and shadow cutover gates
refactor(retrieval): retire legacy primary pipeline
docs(architecture): record phase-2 handoff contract
```
