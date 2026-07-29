# MCP Agent 知识命中率 V7 阶段性验收报告

> 结论：**不通过放行**（在 Tier 2A 停止，未进入 MCP Tier 2B/最终 37 例）  
> 日期：2026-07-29  
> 依据：`mcp-agent-knowledge-hit-rate-remediation-and-release-spec-v7.md`

## 1. 本轮已完成的工程修复

- 删除 answering/retrieval 中依赖 Golden 题干、知识编号和固定事实短语的查询规划、direct-slot 与 query variant 规则，改为通用的 query anchors、条件、数值维度与原题表面词处理。
- RawRetriever 在 rerank 前保留至少 20 条候选，并在 trace 中记录 pre-rerank/rerank candidate IDs。
- EvidenceGroup 按解析后的文档族、组织范围与版本分组；事实候选、claim protocol、渲染与 citation 均进一步收紧条件和 passage lineage。
- harness 增加 production/config/index/database/process/scorer 指纹、snapshot reuse 原因和 fingerprint 校验；评分器不再因同 knowledge ID 而放过错误 passage。
- A/B 改为“一次 raw capture、同一候选池双侧 replay”，并在 normal rerank 不可用时明确失败，不再把 fallback 标为 normal。
- 修复性能根因：同一请求的多个 query variants 现在一次批量 embedding，避免 Windows 隔离 provider 按变体反复冷启动。

## 2. 已执行测试

| 层级 | 结果 |
|---|---|
| 聚焦事实、MCP 合约、历史命中率与 V7 harness 回归 | 116 passed |
| passage/hybrid 相关回归 | 17 passed |
| V7 batching、anti-overfit、harness 完整性回归 | 40 passed（其中最终一次 10 passed） |
| 静态编译 | 通过 |
| anti-Golden 扫描 | 通过 |

pytest 仍有项目既有的 `asyncio_mode` 未识别警告；不影响本轮断言结果。

## 3. Tier 2A：同候选池 rerank A/B

当前冻结尝试产物：`artifacts/hit_rate_test_v7/tier2_ab_attempt_02/`。

| 项目 | normal-rerank | deterministic-fallback |
|---|---:|---:|
| answerable cases | 32 | 32 |
| Top-1 | 84.38% | 84.38% |
| Recall@5 | 100.00% | 100.00% |
| 同池逐例回归 | — | 0 |
| normal rerank 可用 | **否** | 不适用 |

`normal_rerank.json` 记录了两次独立的、5 秒上限的 `TimeoutError`；随后停止剩余 normal probes，避免把同一 provider 故障放大为 32 倍超时。comparison 的 `pass=false` 原因是 `normal_rerank_available=false`，不是 fallback 的检索质量下降。

capture/run fingerprint：`00616201a1be18a3ddf33d31`，两侧 replay 一致。

## 4. 门禁判定

| 门禁 | 判定 | 说明 |
|---|---|---|
| 通用化/anti-overfit | 通过 | 无 Golden ID、题库路径或完整问法映射依赖。 |
| 同池候选 A/B | **不通过** | 无可用 normal rerank 对照；不能认可 fallback 为等价替代。 |
| Tier 2B（16 例真实 MCP） | 未执行 | SPEC 规定 Tier 2A 不通过不得进入。 |
| Tier 3（pytest 全量 + 37 例） | 未执行 | 不能在前置门禁失败后生成伪最终验收。 |
| 发布/提交 | 未执行 | 仅在完整门禁通过后进行。 |

## 5. 阻塞根因与下一步

reranker 配置、endpoint 和凭证均已配置，但 API reranker 在真实隔离 worker 中两次超过 5 秒；此前一次 20 秒探测同样超时。因此这是 provider 连通性/服务响应或进程隔离启动链路的问题，不能由评分规则或 fallback 排序掩盖。

下一步应先定位并修复 reranker provider：保留相同请求 payload，记录不含密钥的 DNS/connect/TLS/HTTP 分段耗时与安全错误类型；若 provider 不可用，提供经测试的本地 reranker 或明确配置为 disabled，并重新定义允许的基线，而不是将 disabled/fallback 伪装为 normal。该问题解决后，必须新建 V7 尝试目录，从 Tier 2A 重新 capture/replay，再依次运行 Tier 2B、Tier 3。

此前 `artifacts/hit_rate_test_v7/tier2_ab/` 是 fingerprint 工具补强前的探索性产物，仅保留作追溯，不参与本报告结论。
