# Retrieval / Answer Boundaries v2

> 状态：Accepted（Phase 2 Task 2.1）
> 日期：2026-07-29
> 配套：`docs/architecture/adr-search-ask-contract-v2.md`
> 依据：`docs/superpowers/plans/2026-07-29-hit-rate-phase2-phase3-core-rebuild.md` §5

## 1. 目标

把分散在 MCP、SearchService、RawRetriever、Snapshot 与 AnswerService 中的业务编排
收敛为单向依赖的分层边界。Phase 2 以**行为保持、契约清晰、依赖单向**为主，不以
提高命中率为目标。

## 2. 目标分层

```text
MCP / REST / GUI          transport adapters only
        │
        ▼
SearchUseCase / AskUseCase / ReadUseCase
        │
        ├── RetrievalPipeline
        ├── EvidenceSnapshotService
        └── AnswerPipeline
                │
                ▼
      Provider Ports / Repositories
```

## 3. 依赖方向（强制）

| 层 | 允许依赖 | 禁止 |
|---|---|---|
| `src/mcp/**` | application UseCases、envelopes、policies、runtime | 直接调用 `SearchService._get_raw_retriever`、`PassageStore._get_conn`、DB SQL、answer 内部 stage |
| `src/api/**` / `src/gui/**` | application UseCases、services facades | 复制 MCP 业务逻辑 |
| `src/application/**` | retrieval / answering contracts、ports | 导入 MCP / GUI / FastAPI |
| `src/retrieval/**` | repositories、provider ports、models | 导入 MCP / answering renderer / GUI |
| `src/answering/**` | retrieval snapshot contracts、ports | 导入 MCP / GUI / API |
| `src/compatibility/**` | 任意（仅兼容垫片） | 作为 normal 生产路径的静默 fallback |

## 4. 单一主管线

### Search

```text
SearchUseCase
  → RetrievalPipeline (QueryPlan → CandidatePoolPolicy → channels → fusion → rerank)
  → EvidenceSnapshotService
  → SearchExecution / public adapter
```

### Ask

```text
AskUseCase
  → load Snapshot by id OR build via same EvidenceSnapshotService
  → AnswerPipeline (fact extract → plan → render → validate)
  → AnswerExecution / public adapter
```

不变量：

- Search 与 Ask **共用**同一 Snapshot 合同与同一 CandidatePoolPolicy 语义；
- Snapshot miss / expired / config mismatch 返回稳定 reason code；
- Ask 复用 snapshot 时不得隐式重新检索（除非 miss 且 trace 记录 rebuild）；
- `SearchService` 退化为兼容 Facade；`RetrievalCommands` 为 adapter 或被 UseCase 替代；
- normal 生产运行不得静默切入 `src/compatibility` legacy 路径。

## 5. Ports（最小集合）

```text
CandidateRetriever
Reranker
EvidenceRepository
SnapshotRepository
FactExtractor
AnswerRenderer
AnswerValidator
```

Container 注入实现；测试使用 port fakes，不 monkeypatch 私有方法作为长期策略
（过渡期允许，但新增测试优先 ports）。

## 6. MCP Adapter 预算

- 单个 MCP tool handler ≤ 100 行（告警，非格式压缩规避）；
- `retrieval.py` 最终仅兼容 re-export，目标 ≤ 300 行；
- 业务 stage 单函数建议 ≤ 150 行；
- MCP 只做：参数别名/校验、policy、deadline、UseCase 调用、Envelope。

必须下沉出 MCP：

- `_retrieve_candidates` / `_build_shared_snapshot`
- document passage selection
- citation allowlist 裁决
- index/db revision 计算
- PassageStore / Database 读取

## 7. 与公开契约的关系

- 对外字段与语义由 `adr-search-ask-contract-v2.md` 冻结；
- 本文件只规定**内部边界与依赖方向**；
- 内部 typed models（QueryPlan、CandidatePoolPolicy、EvidenceSnapshot…）
  仅在 transport adapter 转为 public dict。

## 8. 架构门禁（Task 2.6）

自动化测试应拒绝：

1. MCP 导入具体 DB / PassageStore / RawRetriever 实现细节（过渡白名单须收敛）；
2. MCP 调用下层私有方法（`_get_*` / `_raw_*`）；
3. application 导入 MCP；
4. answering 导入 MCP / GUI / API；
5. retrieval 导入 MCP / answer renderer；
6. normal Search/Ask 双主管线（legacy 仅 compatibility 显式 route）。

## 9. 迁移策略

1. 先固化契约 ADR + CandidatePoolPolicy（Task 2.0 / 2.1）✅
2. 引入 typed contracts 与 UseCase 薄壳，内部仍委托现有 SearchService/AnswerService
3. 逐段下沉 MCP 业务到 application / retrieval / answering
4. 打开架构门禁 strict；Shadow parity 比较 fingerprint 与 answer_mode
5. 删除静默 legacy 分支

Phase 2 完成前允许 Facade 双轨，但 trace 必须标明 `mode`；不得无 trace 静默回退。
