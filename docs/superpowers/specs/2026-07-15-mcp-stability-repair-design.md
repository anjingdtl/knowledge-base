# MCP 稳定性压测修复设计

## 背景与目标

《ShineHeKB稳定性压测报告_2026-07-15》确认核心检索与数据一致性正常，但发现五个 MCP 工具的描述、参数 Schema 或实际行为不一致。本设计消除首次调用失败，并为受外部 LLM 配额影响的性能问题提供可执行诊断边界。

## 适用范围

本次在 MCP 薄层修复 B1–B5，不改变检索、图谱或记忆服务的业务语义：

| 报告项 | 根因 | 修复决策 |
| --- | --- | --- |
| B1 `execute_query` | 描述和错误信息声称支持未实现的 `hybrid` | 以现有实现为准，只声明并接受 `structured`、`graph` |
| B2 `graph_traverse` | 工具描述未说明 `start_ids` 是 JSON 字符串和 `limit` 是节点上限 | 在工具描述、参数说明和解析错误中给出可复制示例 |
| B3 `tags` | 运行时无分页，调用方易假定支持 `limit` | 增加可选 `limit`/`offset` 和一致的分页元数据 |
| B4 `extract_tasks_from_doc` | 工具名使调用方误传 `doc_id`，实际只接收文本 | 保留稳定工具名并支持 `doc_id` 读取现有知识；`content` 仍可直传 |
| B5 `ask_with_query` | 空参数校验与描述不一致 | 在描述与 docstring 中明确 `question` 或 `search_query` 至少一个；保留既有校验 |

## 设计

工具函数签名和 `_define_tool` 描述是 FastMCP 生成对外 Schema 的权威来源。因此每个修复同时修改函数签名/说明、运行时校验和契约测试。

`tags` 保持默认返回全部标签的向后兼容行为；提供 `limit` 后返回切片并在 envelope metadata 里给出 `count`、`limit`、`offset`、`next_offset` 与 `truncated`。`extract_tasks_from_doc` 要求二选一传入 `content` 或 `doc_id`；同时传入时拒绝，避免来源歧义。`doc_id` 仅读取已索引知识的内容，不引入任意文件读取。

`execute_query` 不实现名为 hybrid 的第三套执行器：该工具的 `QuerySpec` 是结构化过滤契约，真实混合检索已经由 `search`/`ask`/`ask_with_query` 暴露。移除虚假承诺比制造与现有检索参数不一致的并行路径更安全。

## 错误处理与测试

- 所有参数错误返回稳定的 `VALIDATION_ERROR` envelope，不抛出未处理异常。
- 新的回归测试直接调用真实 MCP 工具并断言对外 envelope、元数据及错误文本；不依赖 LLM 或网络。
- 每个修改遵守 Red → Green：先写会失败的契约测试并运行，再写最小实现。

## 运维边界

报告中的 P95 65 秒与 URL 导入失败不能从该报告推导为 MCP 参数层代码缺陷。修复报告会提供诊断步骤：按工具 trace 区分 LLM、重排和索引时间；对失败 URL 保留失败原因并通过 `get_job` 查询。生产客户端仍应配置不低于 100 秒的超时和重试策略。

## 验收标准

1. `execute_query(type="hybrid")` 明确失败，支持值仅列出 `structured / graph`。
2. `graph_traverse` 对非法 JSON 返回含示例的验证错误，公开描述含 `start_ids` 示例和 `limit`。
3. `tags(limit=..., offset=...)` 可预测分页，空参数保持旧行为。
4. `extract_tasks_from_doc` 可从 `doc_id` 提取，且缺失或冲突参数返回验证错误。
5. `ask_with_query` 的描述与空参数错误保持一致。
6. 定向 MCP 契约测试、全量 Python 测试、Ruff 与本次修改 MCP 模块的 Mypy 检查均通过；全库 Mypy 状态单独记录。
