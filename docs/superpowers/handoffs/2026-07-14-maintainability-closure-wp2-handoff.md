# Task 完成报告 — Maintainability Closure WP2（Answer + MCP 起步）

## Task
- ID：WP2-T1 / T2 / T3(partial) / T4(partial) / T5(partial)
- 分支：`feat/maintainability-closure-wp0-wp1`
- Spec：`docs/superpowers/specs/04-maintainability-closure-spec.md`

## 修改范围
### Answer（完整）
- `src/answering/assembler.py` / `citations.py` / `fallbacks.py`
- `src/answering/service.py` — 去掉伪 dual-path（legacy/shadow 映射 unified）
- `src/answering/context_builder.py` / `generation.py` — 不再依赖 services.verified_answer
- `src/services/verified_answer.py` — **仅 re-export 兼容**

### MCP / Application（起步，未清空 server 工具主体）
- `src/application/tagging_service.py` — auto_tag 业务（无 `Database._instance`）
- `src/application/retrieval_commands.py` — search / fulltext / ask_verified
- `src/mcp/tools/retrieval.py` — 真实实现（5 个 public helpers）
- `src/mcp/server.py` — ping/search/ask verified path/auto_tag 委托 application

## 明确未完成（进入 WP2 后续 / WP3 前需继续）
- `src/mcp/server.py` 仍 ~3900 行，大量工具主体未迁移
- wiki / graph / memory / administration / ingest 工具未完整实拆
- MCP 内仍有 `get_conn()` / 直接 SQL（非 auto_tag 路径）
- server.py 工具函数清零（目标 ≤500 行）未达成

## 验证
- answering + ask/search contracts + architecture + retrieval + mcp_contract：**114+ passed**（debt shape 测试已更新）
- Hybrid Eval strict：**PASS**
- debt residual：**7**（原 9；清零 answering→verified_answer 业务依赖；mcp_tools_real_impl_count=5）

## 架构欠账变化
| 指标 | 前 | 后 |
|---|---|---|
| answering_depends_on_verified_answer | True | **False** |
| mcp_tools_real_impl_count | 0 | **5** |
| residual debt | 9 | **7** |

## 是否允许进入下一 Task
- **YES（有条件）** — 可开始 **WP2 续：MCP 分域搬迁** 或进入 **WP3 Container** 仅在「新 MCP 工具不再增 SQL」前提下
- 建议：先继续 WP2 MCP 分域（ingest/admin）直到 server 明显变薄，再 WP3
