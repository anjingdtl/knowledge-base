# ShineHeKB MCP 稳定性压测修复记录（2026-07-15）

## 修复范围

本记录对应 `ShineHeKB稳定性压测报告_2026-07-15.md`。报告中的五项问题均为 MCP 工具契约问题，不涉及知识数据正确性、向量覆盖或引用溯源。

| ID | 工具 | 根因 | 修复结果 |
| --- | --- | --- | --- |
| B1 | `execute_query` | 描述和错误文本错误地包含未实现的 `hybrid` | 仅公开 `structured` 和 `graph`；错误提示与实际支持值一致 |
| B2 | `graph_traverse` | `start_ids` 的 JSON 字符串格式及 `limit` 的用途未在工具描述中说明 | 描述提供 `start_ids='["knowledge-id"]'` 示例；非法 JSON 返回 `VALIDATION_ERROR` 和恢复提示 |
| B3 | `tags` | 无分页，调用方传 `limit` 会在 Schema 校验阶段失败 | 支持 `limit`、`offset`，返回 `count`、`next_offset`、`truncated`；无参数仍返回完整列表 |
| B4 | `extract_tasks_from_doc` | 工具名诱导调用方传 `doc_id`，但原函数只接收文本 | 兼容 `doc_id`，从已索引知识条目读取内容；要求 `content` 与 `doc_id` 二选一 |
| B5 | `ask_with_query` | 最小入参要求未写入工具说明 | 明确要求至少提供 `question` 或 `search_query`；保留已有运行时校验 |

## 调用示例

```text
execute_query(query_spec={"filter": {"tag": "企微"}}, type="structured")
graph_traverse(start_ids='["knowledge-id"]', max_depth=2, limit=20)
tags(limit=50, offset=0)
extract_tasks_from_doc(doc_id="knowledge-id")
ask_with_query(search_query="企微集约运营")
```

## 运维诊断边界

压测观察到 P95 延迟约 65 秒、若干 URL 导入任务失败；这两项不能仅凭该报告归类为 B1–B5 一样的 MCP 契约缺陷。它们需要在目标环境按下列路径诊断：

1. 执行 `shinehe doctor`，记录 LLM、Embedding、Reranker 端点可达性与配置告警。
2. 对慢问答查看工具 trace/审计记录，分别比较检索、重排和 LLM 生成耗时；重点核对供应商限流、模型配额和输入上下文大小。
3. 对失败 URL 调用 `list_jobs` 和 `get_job(job_id)`，保留 URL、失败时间和错误信息；先区分 SSRF 拦截、TLS/HTTP 失败、目标站反爬与解析器错误。
4. 生产 MCP 客户端超时配置不低于 100 秒，并只对明确的瞬时网络或供应商限流错误重试；不对参数 `VALIDATION_ERROR` 重试。

## 回归验证

`tests/test_mcp_stability_report_repair.py` 覆盖上述五项：B1/B2/B5 的描述与失败 envelope、B3 的分页与向后兼容、B4 的 `doc_id`/输入互斥行为。发布前还应运行：

```powershell
pytest tests/test_mcp_stability_report_repair.py tests/test_mcp_contract.py tests/test_mcp_server.py tests/test_mcp_stability.py -q
ruff check src tests
mypy src
pytest tests -q
```

### 本次验证结果

- `pytest tests -q`：1,913 passed、2 skipped，退出码 0；8 条 warning 均来自既有 `mcp_post_fix_test.py` 的非 `None` 返回值和第三方 `jieba` 的弃用提示。
- `ruff check src tests`：通过。
- `mypy src/mcp/tools/retrieval.py src/mcp/tools/graph.py src/mcp/tools/ingest.py src/mcp/tools/memory.py`：通过。
- `mypy src`：未作为本次修复的通过门槛。它在 `src/gui/main_window.py:597` 报一条既有类型错误（`_mcp_start_worker = None`）；`git blame` 归属基线提交 `07dbb11`，本次没有修改该文件。
